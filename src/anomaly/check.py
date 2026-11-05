"""python anomaly_cli.py check — 設定ファイル一式の検査(要件定義書 U-09)。

コード体系(v2 の R-16 と同じチェック)、input_columns.yaml、settings.yaml を検査する。
シリアル変換表の検査は、別ツール(シリアル変換ツール)の check で行う。
問題(NG)が1つでもあれば終了コード 1。注意(警告)だけなら 0。
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from .codebook import CodebookError, load_codebook
from .input_loader import load_column_config
from .local_config import resolve_paths
from .settings import SettingsError, load_settings


@dataclass
class CheckItem:
    name: str
    path: str
    ok: bool
    summary: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_checks(paths) -> list[CheckItem]:
    items: list[CheckItem] = []

    try:
        cb = load_codebook(paths.codebook)
        prods = ", ".join(f"{p}({len(cb.codes(p))})" for p in cb.product_ids())
        items.append(CheckItem("コード体系", str(paths.codebook), True, f"version={cb.version}  製品={prods}"))
    except CodebookError as e:
        items.append(CheckItem("コード体系", str(paths.codebook), False, errors=e.errors))

    try:
        cols = load_column_config(paths.columns)
        items.append(CheckItem("列の対応づけ", str(paths.columns), True, f"{len(cols)} 列"))
    except ValueError as e:
        items.append(CheckItem("列の対応づけ", str(paths.columns), False, errors=[str(e)]))

    try:
        load_settings(paths.settings)
        items.append(CheckItem("解析の設定", str(paths.settings), True, "既定値・閾値"))
    except SettingsError as e:
        items.append(CheckItem("解析の設定", str(paths.settings), False, errors=e.errors))
    return items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="設定ファイル一式の検査")
    ap.add_argument("--local-dir")
    ap.add_argument("--codebook")
    ap.add_argument("--columns")
    ap.add_argument("--settings")
    a = ap.parse_args(argv)
    paths = resolve_paths(local_dir=a.local_dir, codebook=a.codebook, columns=a.columns, settings=a.settings)
    items = run_checks(paths)
    for it in items:
        head = "OK" if it.ok else "NG"
        print(f"{head}: {it.name}  {it.summary}".rstrip())
        print(f"    {it.path}")
        for e in it.errors:
            print(f"    - {e}")
        for w in it.warnings:
            print(f"    注意: {w}")
    ng = [it for it in items if not it.ok]
    print(f"結果: {'問題なし' if not ng else f'NG {len(ng)} 件'}")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
