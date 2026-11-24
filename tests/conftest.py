"""テストの共通設定(要件定義書 E-09、第11節)。

- 実際の設定ファイルには依存せず、tests/fixtures/ のテスト専用のコード体系・変換表・架空データを使う。
- 例外: リポジトリ同梱の初期値(config/)が検査を通ることを確認するテストだけは config/ を読む。
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = REPO_ROOT / "tests" / "fixtures"
SYNTH = FIXTURES / "synthetic"
CODEBOOK_TEST_YAML = FIXTURES / "codebook_test.yaml"


def run_cli(args: list[str], drop_env_prefix: str | None = None) -> subprocess.CompletedProcess:
    """anomaly_cli.py を別のプロセスで起動する(U-02、U-07 のテスト用)。

    - 今の環境変数を引き継ぐ(Windows では SYSTEMROOT などが無いと Python が起動できないため)。
    - 出力の文字コードを UTF-8 に固定する(Windows では別プロセスの出力が cp932 になり、日本語を読めないため。
      2026-11-24 に会社PCで4件失敗した原因)。
    - drop_env_prefix: この文字で始まる環境変数を外す(例: "DIFY_" で Dify の接続情報を外す)。
    """
    env = {k: v for k, v in os.environ.items() if not (drop_env_prefix and k.startswith(drop_env_prefix))}
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
    return subprocess.run([sys.executable, str(REPO_ROOT / "anomaly_cli.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


@pytest.fixture(scope="session")
def codebook_path() -> Path:
    return CODEBOOK_TEST_YAML


@pytest.fixture(scope="session")
def codebook():
    from anomaly.codebook import load_codebook
    return load_codebook(CODEBOOK_TEST_YAML)


@pytest.fixture(scope="session")
def settings():
    """架空データ(synth_classified.py)は、使用期間を「12か月以内 / 13か月以上」で比べる前提で作ってある。
    そのため、使用期間の区切り(既定 24)を 13 にする(13か月未満 = 12か月以内)。"""
    from anomaly.settings import parse_settings
    return parse_settings({"usage_split_months": 13})


@pytest.fixture(scope="session")
def synth_csv() -> Path:
    return SYNTH / "repairs.csv"


@pytest.fixture(autouse=True)
def _no_local_dir_env(monkeypatch):
    """利用者の環境変数 ANOMALY_LOCAL_DIR がテストに影響しないようにする。"""
    monkeypatch.delenv("ANOMALY_LOCAL_DIR", raising=False)
