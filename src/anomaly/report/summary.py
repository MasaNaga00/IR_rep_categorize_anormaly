"""Dify での要約文(説明文の層2)(要件定義書 T-02、T-03、T-05)。

  python anomaly_cli.py summarize --run <実行フォルダ> [--local-dir ...] [--force]

実行フォルダの combos/*/ の findings.json と report.md を材料に、要約用の Dify ワークフローで要約を作り、
report.md の冒頭に「要約」として入れる。要約文の数値が材料に無い場合は、report.md の末尾に警告を書く。
定型文(層1)は変えない。--force で、入れてある要約を作り直す。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..dify_client import KEY_SUMMARY, DifyAPIError
from .number_check import unmatched_numbers
from .templates import FORBIDDEN_WORDS

START, END = "<!-- summary:start -->", "<!-- summary:end -->"
CHECK_START, CHECK_END = "<!-- numcheck:start -->", "<!-- numcheck:end -->"
_BLOCK = re.compile(r"\n?<!-- (summary|numcheck):start -->.*?<!-- \1:end -->\n?", re.DOTALL)


def strip_summary(md: str) -> str:
    """入れてある要約と警告を取り除き、定型文だけに戻す。"""
    return _BLOCK.sub("\n", md).replace("\n\n\n", "\n\n")


def has_summary(md: str) -> bool:
    return START in md


def insert_summary(md: str, text: str, warnings: list[str]) -> str:
    base = strip_summary(md)
    block = (f"{START}\n## 要約(Dify による文章)\n\n"
             "> 定型文と注目点の一覧だけを材料に、Dify(LLM)が作った文章です。数値は表・定型文と照合しています。\n\n"
             f"{text.strip()}\n{END}\n")
    i = base.find("\n## 前提")
    out = base[:i + 1] + block + base[i + 1:] if i >= 0 else base.rstrip("\n") + "\n\n" + block
    if warnings:
        out = out.rstrip("\n") + "\n\n" + (f"{CHECK_START}\n## 要約の確認\n\n" + "\n".join(f"- {w}" for w in warnings)
                                         + f"\n{CHECK_END}\n")
    return out


def check_summary(text: str, findings: dict, report_md: str) -> list[str]:
    """T-05 と T-04 の確認。警告の文のリスト。"""
    warnings = []
    nums = unmatched_numbers(text, findings, report_md)
    if nums:
        warnings.append(f"要約文に、表・定型文に無い数値があります: {', '.join(nums)}。要約文を使う前に、表で確かめてください。")
    words = [w for w in FORBIDDEN_WORDS if w in text]
    if words:
        warnings.append(f"要約文に、使わない約束の言葉があります: {', '.join(words)}(構成比の比較で、故障率ではありません)。")
    return warnings


def build_inputs(findings: dict, report_md: str) -> dict:
    """要約用ワークフローの入力変数(手順書 docs/dify/summary_workflow_setup.md)。"""
    return {"findings": json.dumps(findings, ensure_ascii=False), "report": strip_summary(report_md)}


@dataclass
class SummaryStats:
    done: int = 0
    skipped: int = 0
    failed: int = 0
    warned: int = 0
    errors: list[str] = field(default_factory=list)

    def summary_line(self) -> str:
        return (f"要約: 作った {self.done} 件(うち数値などの警告あり {self.warned} 件)、"
                f"作り済みで飛ばした {self.skipped} 件、作れなかった {self.failed} 件")


def summarize_run(run_dir: str | Path, client, force: bool = False) -> SummaryStats:
    st = SummaryStats()
    for d in sorted((Path(run_dir) / "combos").glob("*")):
        rp, fp = d / "report.md", d / "findings.json"
        if not rp.exists() or not fp.exists():
            continue
        md = rp.read_text(encoding="utf-8")
        if has_summary(md) and not force:
            st.skipped += 1
            continue
        findings = json.loads(fp.read_text(encoding="utf-8"))
        try:
            resp = client.run(build_inputs(findings, md))
        except DifyAPIError as e:
            st.failed += 1
            st.errors.append(f"{d.name}: {e}")
            continue
        text = resp.text.strip()
        if not text:
            st.failed += 1
            st.errors.append(f"{d.name}: 要約が空でした")
            continue
        warnings = check_summary(text, findings, strip_summary(md))
        rp.write_text(insert_summary(md, text, warnings), encoding="utf-8")
        st.done += 1
        st.warned += bool(warnings)
    return st


def main(argv=None, client=None) -> int:
    from ..dify_client import DifyClient
    from ..local_config import resolve_paths

    ap = argparse.ArgumentParser(description="Dify で要約文を作り、report.md の冒頭に入れる")
    ap.add_argument("--run", required=True, help="analyze まで終わった実行フォルダ")
    ap.add_argument("--local-dir")
    ap.add_argument("--force", action="store_true", help="入れてある要約を作り直す")
    a = ap.parse_args(argv)
    if not (Path(a.run) / "combos").exists():
        print(f"NG: 解析の結果(combos/)がありません: {a.run}")
        return 1
    if client is None:
        try:
            client = DifyClient.from_env(resolve_paths(local_dir=a.local_dir).env_file, key_var=KEY_SUMMARY)
        except ValueError as e:
            print(f"NG: {e}")
            print(f"対処: .env に {KEY_SUMMARY}(要約用ワークフローの API キー)を書いてください。")
            return 1
    st = summarize_run(a.run, client, a.force)
    print(st.summary_line())
    for e in st.errors[:10]:
        print(f"  {e}")
    return 0 if not st.failed else 1


if __name__ == "__main__":
    sys.exit(main())
