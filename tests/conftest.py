"""テストの共通設定(要件定義書 E-09、第11節)。

- 実際の設定ファイルには依存せず、tests/fixtures/ のテスト専用のコード体系・変換表・架空データを使う。
- 例外: リポジトリ同梱の初期値(config/)が検査を通ることを確認するテストだけは config/ を読む。
"""
from __future__ import annotations

import csv
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
    from anomaly.settings import parse_settings
    return parse_settings(None)


@pytest.fixture(scope="session")
def synth_csv() -> Path:
    return SYNTH / "repairs.csv"


@pytest.fixture(autouse=True)
def _no_local_dir_env(monkeypatch):
    """利用者の環境変数 ANOMALY_LOCAL_DIR がテストに影響しないようにする。"""
    monkeypatch.delenv("ANOMALY_LOCAL_DIR", raising=False)
