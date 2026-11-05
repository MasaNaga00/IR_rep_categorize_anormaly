"""python anomaly_cli.py classify — 入力 CSV を Dify で分類し、分類済みのデータを書き出す(要件定義書 第5節、O-01、O-05)。

  python anomaly_cli.py classify --input <入力CSV> [--serial-result <変換結果CSV>] [--local-dir ...]
                                 [--internal-comments default|all|none] [--batch-size N] [--dry-run] [--no-cache]
                                 [--output-dir <実行フォルダ>]

出力(実行フォルダ): raw/、classifications.csv、failures.csv、environments.csv、classify_failed.csv、
validation_issues.csv、sample_check.xlsx、language_metrics.csv、run_meta.json。続けて analyze --classified <実行フォルダ> で解析できる。
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from .classify.cache import ClassifyCache, NullCache, cache_signature, sha256_of
from .classify.runner import ClassifyResult, classify_records
from .codebook import Codebook, CodebookError, load_codebook
from .constants import INTERNAL_MODES
from .dify_client import KEY_CLASSIFY, DifyClient
from .input_loader import InputError, LoadResult, load_column_config, load_input
from .local_config import resolve_paths
from .outputs.files import write_classified, write_csv, write_json
from .serial_merge import MergeSummary, SerialResultError, load_serial_results, merge_serial, result_column_names
from .settings import Settings, SettingsError, load_settings

FAILED_FIELDS = ("rec_key", "repair_id", "sales_company", "failed_reason")
ISSUE_FIELDS = ("rec_key", "batch_id", "severity", "kind", "message")


def internal_setting(settings: Settings, mode: str) -> str:
    """キャッシュの区別に使う、内部コメントの渡し方(G-16)。"""
    if mode == "default":
        return "default:" + ",".join(sorted(settings.internal_comment_excluded_sales_companies))
    return mode


def write_outputs(out: Path, records: list[dict], result: ClassifyResult, codebook: Codebook, settings: Settings) -> None:
    from .quality.sample_check import write_quality_files

    write_classified(out, records, codebook.code_name)
    write_csv(out / "classify_failed.csv", FAILED_FIELDS, (r for r in records if r.get("classify_status") == "failed"))
    issues = [i.to_dict() for i in result.issues]
    write_csv(out / "validation_issues.csv", ISSUE_FIELDS, issues)
    write_quality_files(out, records, codebook, settings, issues)          # G-14


def run_meta(*, started: datetime, args, paths, codebook: Codebook, settings: Settings, prompt_sha: str,
             loaded: LoadResult, result: ClassifyResult, merge: MergeSummary | None, cache, seconds: float) -> dict:
    """O-05"""
    return {
        "started_at": started.isoformat(timespec="seconds"),
        "input": str(args.input),
        "codebook": {"path": str(paths.codebook), "version": codebook.version, "sha256": codebook.sha256},
        "classify_prompt": {"path": str(paths.classify_prompt), "sha256": prompt_sha},
        "settings": settings.as_dict(),
        "internal_comments": args.internal_comments,
        "counts": {"input_rows": loaded.total_rows, "loaded": len(loaded.records), "excluded": loaded.excluded_counts(),
                   "duplicate_repair_id_rows": loaded.duplicate_id_rows()},
        "classify": result.stats.as_dict(),
        "serial_merge": merge.as_dict() if merge is not None else None,
        "cache": str(cache.path) if getattr(cache, "path", None) else None,
        "seconds": round(seconds, 1),
        "random_seed": settings.random_seed,
        "dry_run": bool(args.dry_run),
    }


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="入力 CSV を Dify で分類し、分類済みのデータを書き出す")
    ap.add_argument("--input", required=True)
    ap.add_argument("--serial-result", help="シリアル変換結果 CSV(分類の後に結合する。S-10)")
    ap.add_argument("--output-dir", help="実行フォルダ。省略すると <ローカル作業フォルダ>/outputs/<日時>")
    ap.add_argument("--internal-comments", choices=INTERNAL_MODES, default="default",
                    help="default: settings.yaml の販社の一覧どおり / all: すべて渡す / none: すべて渡さない(G-30)")
    ap.add_argument("--batch-size", type=int, help="settings.yaml の batch_size より優先")
    ap.add_argument("--dry-run", action="store_true", help="Dify を呼ばず、リクエストの中身だけを raw/ に書く")
    ap.add_argument("--no-cache", action="store_true", help="キャッシュを使わない(読まない・書かない)")
    ap.add_argument("--local-dir")
    ap.add_argument("--codebook")
    ap.add_argument("--columns")
    ap.add_argument("--settings")
    ap.add_argument("--prompt", help="分類用プロンプト(キャッシュの区別に使う。Dify に貼ったものと同じにする)")
    return ap


def main(argv=None, client=None) -> int:
    a = build_parser().parse_args(argv)
    started = datetime.now()
    t0 = time.monotonic()
    paths = resolve_paths(local_dir=a.local_dir, codebook=a.codebook, columns=a.columns, settings=a.settings,
                          classify_prompt=a.prompt)
    try:
        cb = load_codebook(paths.codebook)
        s = load_settings(paths.settings)
        if a.batch_size is not None:
            if a.batch_size < 1:
                raise ValueError("--batch-size は 1 以上にしてください")
            s = dataclasses.replace(s, batch_size=a.batch_size)
        cols = load_column_config(paths.columns)
        loaded = load_input(a.input, cb.product_ids(), cols)
        serial = load_serial_results(a.serial_result, *result_column_names(cols)) if a.serial_result else None
        prompt_sha = sha256_of(paths.classify_prompt)
        if client is None and not a.dry_run:
            client = DifyClient.from_env(paths.env_file, key_var=KEY_CLASSIFY)
    except (CodebookError, SettingsError, SerialResultError, InputError, ValueError, OSError) as e:
        print(f"NG: {e}")
        return 1

    out = Path(a.output_dir) if a.output_dir else paths.outputs_dir / f"{started:%Y%m%d_%H%M%S}"
    if (out / "classifications.csv").exists():
        print(f"NG: 実行フォルダに分類の結果が既にあります(上書きしません): {out}")
        return 1
    out.mkdir(parents=True, exist_ok=True)
    cache = NullCache() if a.no_cache else ClassifyCache(
        paths.cache_dir, cache_signature(cb.sha256, prompt_sha, internal_setting(s, a.internal_comments)))

    print(f"入力: {a.input}({len(loaded.records)} 件、除外 {len(loaded.excluded)} 件、組み合わせ {len(loaded.combos())} 個)")

    def progress(done, total):
        print(f"\r  分類中: {done} / {total} 件", end="", flush=True)

    result = classify_records(loaded.records, cb, s, client, cache, internal_mode=a.internal_comments,
                              raw_dir=out / "raw", progress=None if a.dry_run else progress, dry_run=a.dry_run)
    if not a.dry_run:
        print()
    if a.dry_run:
        print(f"dry-run: Dify には送っていません。リクエスト {result.stats.batches} 件(修理 {result.stats.sent} 件)を "
              f"{out / 'raw'} に書きました(キャッシュ {result.stats.cached} 件、コメントが空欄 {result.stats.no_text} 件)。")
        write_json(out / "run_meta.json", run_meta(started=started, args=a, paths=paths, codebook=cb, settings=s,
                                                   prompt_sha=prompt_sha, loaded=loaded, result=result, merge=None,
                                                   cache=cache, seconds=time.monotonic() - t0))
        return 0

    merge = merge_serial(loaded.records, serial, s.usage_max_months)
    write_outputs(out, loaded.records, result, cb, s)
    write_json(out / "run_meta.json", run_meta(started=started, args=a, paths=paths, codebook=cb, settings=s,
                                               prompt_sha=prompt_sha, loaded=loaded, result=result, merge=merge,
                                               cache=cache, seconds=time.monotonic() - t0))
    for line in result.stats.summary_lines() + merge.summary_lines():
        print(line)
    if result.stats.failed_ratio() > s.failed_warn_ratio:          # G-22
        print(f"注意: 分類不可が {result.stats.failed_ratio():.1%} あります(基準 {s.failed_warn_ratio:.0%})。"
              "時間をおいて同じコマンドを実行すると、分類不可の修理だけが送り直されます(別の実行フォルダに出力)。")
    print(f"出力: {out}")
    print(f"次: python anomaly_cli.py analyze --classified \"{out}\"" + (f" --local-dir \"{a.local_dir}\"" if a.local_dir else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
