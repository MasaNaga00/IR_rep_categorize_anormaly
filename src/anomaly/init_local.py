"""python anomaly_cli.py init --local-dir <path>(要件定義書 E-06)"""
from __future__ import annotations

import argparse
import sys

from .local_config import init_local


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ローカル作業フォルダを作り、初期値をコピーする(既存のファイルは上書きしない)")
    ap.add_argument("--local-dir", required=True)
    a = ap.parse_args(argv)
    created = init_local(a.local_dir)
    print(f"ローカル作業フォルダ: {a.local_dir}")
    print(f"作成: {len(created)} 件" + ("" if created else "(既存のファイルはそのまま)"))
    for p in created:
        print(f"  + {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
