"""起動用スクリプト。インストール不要で、リポジトリの直下から実行する(要件定義書 U-02, U-07)。

  python anomaly_cli.py               [--local-dir ...]     対話式(y / n で最後まで進む)
  python anomaly_cli.py run           --input <CSV> [--serial-result <CSV>] --yes [--local-dir ...]   質問なしで最後まで
  python anomaly_cli.py init          --local-dir C:\\work\\anomaly_local
  python anomaly_cli.py check         [--local-dir ...]
  python anomaly_cli.py codes-export  [--local-dir ...] [--xlsx ...] [--force]
  python anomaly_cli.py codes-import  --xlsx <Excel> [--local-dir ...]
  python anomaly_cli.py merge-serial  --input <入力CSV> [--serial-result <変換結果CSV>] [--output <CSV>] [--local-dir ...]
  python anomaly_cli.py classify      --input <入力CSV> [--serial-result ...] [--internal-comments default|all|none] [--dry-run] [--local-dir ...]
  python anomaly_cli.py analyze       --classified <分類済みのフォルダ> [--serial-result ...] [--run-date YYYY-MM-DD] [--output-dir ...]
  python anomaly_cli.py summarize     --run <実行フォルダ> [--local-dir ...] [--force]      Dify で要約文を作り report.md に入れる
  python anomaly_cli.py evaluate      --gold <正解CSV> --run <実行フォルダ> [--local-dir ...]  日本データの評価
  python anomaly_cli.py compare-runs  --a <実行フォルダ> --b <実行フォルダ> [--sales-company CMJ] [--output <CSV>]

"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# コマンド → (モジュール, 関数名)
COMMANDS = {
    "run": ("anomaly.cli", "main_run"),
    "init": ("anomaly.init_local", "main"),
    "check": ("anomaly.check", "main"),
    "codes-export": ("anomaly.codes_excel", "main_export"),
    "codes-import": ("anomaly.codes_excel", "main_import"),
    "merge-serial": ("anomaly.serial_merge", "main"),
    "classify": ("anomaly.classify_cmd", "main"),
    "analyze": ("anomaly.analyze", "main"),
    "summarize": ("anomaly.report.summary", "main"),
    "evaluate": ("anomaly.quality.evaluate", "main"),
    "compare-runs": ("anomaly.quality.compare_runs", "main"),
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):              # 対話式(U-02)
        if argv and argv[0] in ("-h", "--help"):
            print(__doc__)
            return 0
        from anomaly.cli import main as interactive
        return interactive(argv)
    if argv[0] not in COMMANDS:
        print(__doc__)
        return 1
    import importlib
    mod_name, func = COMMANDS[argv[0]]
    return getattr(importlib.import_module(mod_name), func)(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
