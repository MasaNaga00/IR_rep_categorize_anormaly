"""解析の実行: 分類済みの修理 → 組み合わせごとの表・注目点・レポート(要件定義書 第7〜9節)。

  python anomaly_cli.py analyze --classified <分類済みのフォルダ> [--serial-result <変換結果CSV>]
                                [--run-date YYYY-MM-DD] [--output-dir <出力先>] [--local-dir ...]

分類済みのフォルダには classifications.csv / failures.csv / environments.csv(outputs/files.py の形)が必要。
段階5で、対話式の実行(分類から解析まで)の中から呼ばれる。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from .analysis.aspects import ComboAnalysis, analyze_combo
from .analysis.findings import JUDGEMENT_NAMES
from .analysis.environment import split_by_environment
from .analysis.prepare import build_combo, split_by_combo
from .analysis.summary import Summary, build_summary
from .codebook import Codebook
from .outputs.excel import combo_sheets, index_rows, write_workbook
from .outputs.files import combo_dir_name, write_json
from .outputs.summary_sheet import ENV_SHEET_NAME, SHEET_NAME, add_empty_sheet, add_summary_sheet, env_line
from .outputs.tableau import write_tableau
from .report.markdown import build_report
from .settings import Settings


def staleness_warning(records: list[dict], s: Settings, run_date: date) -> list[str]:
    """A-07: データ中の最新の受付日が、直近期間の最終月より前なら警告。"""
    if not records:
        return []
    latest = max(r["received_date"] for r in records)
    run_idx = run_date.year * 12 + run_date.month - 1
    y, m = int(latest[:4]), int(latest[5:7])
    if y * 12 + m - 1 < run_idx - 1:
        return [f"注意: データ中の最新の受付日({latest})が、直近期間の最終月より前です。データの抽出日が古い可能性があります。"]
    return []


def findings_json(ca: ComboAnalysis, s: Settings, run_info: dict) -> dict:
    """A-14b"""
    return {
        "model": ca.combo.model, "part_no": ca.combo.part_no, "product_type": ca.combo.product_type,
        "run_date": run_info["run_date"], "n_records": len(ca.combo.records), "n_findings": ca.n_findings(),
        "concentration": ca.overall.facts["concentration"],
        "aspects": {a.name: a.judgement for a in ca.aspects()},
        "comparisons": [c.as_dict() for c in ca.comparisons()],
        "lot_spikes": [{"lot": name, "n": n} for name, n in ca.lot.facts.get("spikes", [])],
        "notes": {"lot": ca.lot.notes},
        "criteria": {"min_group_n": s.min_group_n, "lot_min_n": s.lot_min_n, "alpha": s.alpha,
                     "min_diff_pt": s.min_diff_pt, "min_code_n": s.min_code_n},
    }


def env_summary(ca: ComboAnalysis, codebook: Codebook, s: Settings, run_date: date) -> tuple[Summary | None, int, list]:
    """A-47: 環境要因で除いた残りの修理で、組み合わせを作り直して解析し、サマリーを作る。
    戻り値: (サマリー。残りが無ければ None, 除いた件数, [(要因の名前, 件数)])"""
    combo = ca.combo
    kept, removed, by = split_by_environment(combo.records, codebook.environment_factors(combo.product_type),
                                             s.env_exclude_mode)
    if not kept:
        return None, len(removed), by
    sub = build_combo(combo.model, combo.part_no, kept, codebook, s.rare_code_min, s.keep_top_codes)
    sm = build_summary(analyze_combo(sub, codebook, s, run_date), s, run_date)
    sm.env_removed, sm.env_removed_by, sm.env_mode = len(removed), by, s.env_exclude_mode
    return sm, len(removed), by


def _add_summaries(wb, sm: Summary, sm_env: Summary | None, n_removed: int, by: list, s: Settings, combo) -> None:
    """O-07: 1枚目「サマリー(環境要因除く)」、2枚目「サマリー」。"""
    add_summary_sheet(wb, sm, SHEET_NAME, 0, note=f"環境要因の記述がある修理も含めた集計です。環境要因を除いた集計は「{ENV_SHEET_NAME}」シート。")
    if sm_env is not None:
        add_summary_sheet(wb, sm_env, ENV_SHEET_NAME, 0)
    else:
        add_empty_sheet(wb, ENV_SHEET_NAME, f"{combo.model} × {combo.part_no} 修理傾向サマリー(環境要因を除く)",
                        [env_line(n_removed, by, s.env_exclude_mode), "環境要因を除くと、対象の修理がありません。"], 0)
    wb.active = 0


def analyze_records(records: list[dict], codebook: Codebook, s: Settings, run_date: date, out_dir: str | Path,
                    run_info: dict | None = None) -> list[tuple[ComboAnalysis, str]]:
    """組み合わせごとに解析し、O-01 のファイルを書く。戻り値: (解析結果, フォルダ名) のリスト。"""
    out = Path(out_dir)
    info = {"run_date": run_date.isoformat(), **(run_info or {})}
    info["warnings"] = list(info.get("warnings", [])) + staleness_warning(records, s, run_date)
    results = []
    for combo in split_by_combo(records, codebook, s.rare_code_min, s.keep_top_codes):
        ca = analyze_combo(combo, codebook, s, run_date)
        folder = combo_dir_name(combo.model, combo.part_no)
        d = out / "combos" / folder
        sm = build_summary(ca, s, run_date)                               # A-40〜A-46
        sm_env, n_removed, by = env_summary(ca, codebook, s, run_date)    # A-47
        write_workbook(d / "tables.xlsx", combo_sheets(ca, s, info),
                       before_save=lambda wb, a=(sm, sm_env, n_removed, by, s, combo): _add_summaries(wb, *a))
        d.joinpath("report.md").write_text(build_report(ca, s, info), encoding="utf-8")
        write_json(d / "findings.json", findings_json(ca, s, info))
        results.append((ca, folder))
    write_workbook(out / "index.xlsx", {"一覧": index_rows(results)})
    write_tableau(out / "tableau_long.csv", records, codebook, s)
    return results


def summary_lines(results: list[tuple[ComboAnalysis, str]]) -> list[str]:
    lines = [f"組み合わせ: {len(results)} 件"]
    for ca, folder in results[:10]:
        lines.append(f"  {ca.combo.model} × {ca.combo.part_no}({len(ca.combo.records)} 件): "
                     f"期間比較={JUDGEMENT_NAMES[ca.recent.judgement]} / 使用期間={JUDGEMENT_NAMES[ca.usage.judgement]} / "
                     f"ロット={JUDGEMENT_NAMES[ca.lot.judgement]}、注目点 {ca.n_findings()}")
    if len(results) > 10:
        lines.append(f"  ほか {len(results) - 10} 件(index.xlsx を参照)")
    return lines


def main(argv=None) -> int:
    from .codebook import CodebookError, load_codebook
    from .input_loader import load_column_config
    from .local_config import resolve_paths
    from .outputs.files import read_classified
    from .serial_merge import SerialResultError, load_serial_results, merge_serial, result_column_names
    from .settings import SettingsError, load_settings

    ap = argparse.ArgumentParser(description="分類済みの修理を、製品×部番ごとに解析してレポートを出力する")
    ap.add_argument("--classified", required=True, help="classifications.csv などがあるフォルダ")
    ap.add_argument("--serial-result", help="シリアル変換結果 CSV(指定すると、製造年月を結合し直す)")
    ap.add_argument("--run-date", help="実行日(基準日)YYYY-MM-DD。省略すると今日")
    ap.add_argument("--output-dir", help="出力先。省略すると分類済みのフォルダ(classify の実行フォルダ)に書く")
    ap.add_argument("--local-dir")
    ap.add_argument("--codebook")
    ap.add_argument("--columns")
    ap.add_argument("--settings")
    a = ap.parse_args(argv)
    paths = resolve_paths(local_dir=a.local_dir, codebook=a.codebook, columns=a.columns, settings=a.settings)
    try:
        run_date = datetime.strptime(a.run_date, "%Y-%m-%d").date() if a.run_date else date.today()
        cb = load_codebook(paths.codebook)
        s = load_settings(paths.settings)
        records = read_classified(a.classified)
        info: dict = {"serial_result": None}
        if a.serial_result:
            res = load_serial_results(a.serial_result, *result_column_names(load_column_config(paths.columns)))
            merge_serial(records, res, s.usage_max_months)
            info["serial_result"] = str(a.serial_result)
        elif any(r.get("mfg_status") for r in records):
            info["serial_result"] = "(分類済みのデータに結合済み)"
    except (CodebookError, SettingsError, SerialResultError, ValueError, OSError) as e:
        print(f"NG: {e}")
        return 1
    out = Path(a.output_dir) if a.output_dir else Path(a.classified)
    if (out / "index.xlsx").exists() or (out / "combos").exists():
        print(f"NG: 出力先に解析の結果が既にあります(上書きしません): {out}")
        return 1
    for w in s.warnings:
        print(f"注意: {w}")
    info["internal_excluded"] = list(s.internal_comment_excluded_sales_companies)
    results = analyze_records(records, cb, s, run_date, out, info)
    for line in summary_lines(results):
        print(line)
    print(f"出力: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
