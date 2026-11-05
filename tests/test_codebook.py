"""コード体系(YAML)の読み込み・自動チェック・API。

v2 の tests/v2/test_codebook.py をコピーしたもの。テスト名の R-xx は v2 の要件 ID。
新しく足したテストは、この解析の要件 ID(E-xx)を付けている。
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from anomaly.codebook import CodebookError, load_codebook

from .conftest import CODEBOOK_TEST_YAML, REPO_ROOT


def _write_variant(tmp_path: Path, mutate) -> Path:
    data = yaml.safe_load(CODEBOOK_TEST_YAML.read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    mutate(data)
    p = tmp_path / "cb.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


# --- 読み込みと基本 API(R-21) ---

def test_r21_basic_attributes(codebook):
    assert codebook.version == "0.3.0-test"
    assert len(codebook.sha256) == 64
    assert codebook.product_ids() == ["ML", "LENS", "DC", "STB"]
    assert codebook.product_name("ML") == "ミラーレスカメラ"


def test_r21_code_info(codebook):
    info = codebook.code("ML", "M001")
    assert info.code == "M001"
    assert info.name == "電源不良"
    assert info.record_type == "failure"
    assert info.responsibility == "manufacturer"
    assert codebook.code("ML", "L001") is None
    assert codebook.is_valid_code("LENS", "L001")
    assert not codebook.is_valid_code("ML", "ZZZ999")


def test_r21_responsibility_and_service(codebook):
    assert codebook.is_manufacturer("ML", "M001")
    assert not codebook.is_manufacturer("ML", "M013")
    assert not codebook.is_manufacturer("ML", "M045")
    assert not codebook.is_manufacturer("ML", "ZZZ999")  # 未定義は False
    assert codebook.is_service("ML", "M043")
    assert not codebook.is_service("ML", "M001")
    assert not codebook.is_service("ML", "ZZZ999")


def test_r21_code_by_role(codebook):
    assert codebook.code_by_role("ML", "connected_device") == "M047"
    assert codebook.code_by_role("ML", "unknown") == "M_UNK"
    assert codebook.code_by_role("LENS", "other") == "L036"
    assert codebook.code_by_role("STB", "connected_device") == "S003"


def test_r17_r19_environment_factors(codebook):
    ml = codebook.environment_factors("ML")
    assert "none" not in ml and "unknown" not in ml  # R-18
    assert ml["impact"].always_user_caused is True
    assert ml["water"].always_user_caused is True
    assert ml["heat"].always_user_caused is False
    assert "battery_leak" not in ml
    stb = codebook.environment_factors("STB")  # 製品ごとの上書き(R-19)
    assert stb["battery_leak"].always_user_caused is True
    assert stb["heat"].always_user_caused is True
    assert stb["heat"].name == ml["heat"].name  # 上書きしていないキーは既定値のまま


def test_r20_claim_statuses(codebook):
    st = codebook.claim_statuses()
    assert list(st) == ["reproduced", "partial", "not_reproduced", "not_mentioned", "out_of_scope", "request_only"]
    assert {k for k, v in st.items() if v.counts_as_confirmed} == {"reproduced", "partial"}
    assert codebook.confirmed_statuses() == {"reproduced", "partial"}
    assert st["request_only"].name == "依頼のみ"


def test_r22_code_list_text(codebook):
    text = codebook.code_list_text("ML")
    lines = text.strip().splitlines()
    assert len(lines) == len(codebook.codes("ML"))
    assert lines[0].startswith("M001 | 電源不良 | ")
    assert "manufacturer" not in text and "user_or_unknown" not in text  # 責任は渡さない


# --- 自動チェック(R-16) ---

@pytest.mark.parametrize("label, mutate, expect", [
    ("必須キー欠落(code_prefix)", lambda d: d["product_categories"]["ML"].pop("code_prefix"), "code_prefix"),
    ("必須キー欠落(responsibility)", lambda d: d["product_categories"]["ML"]["failure_categories"]["M001"].pop("responsibility"), "M001"),
    ("role connected_device なし", lambda d: d["product_categories"]["ML"]["failure_categories"]["M047"].pop("role"), "connected_device"),
    ("role の重複", lambda d: d["product_categories"]["ML"]["failure_categories"]["M046"].__setitem__("role", "unknown"), "unknown"),
    ("サービスコードなし", lambda d: [v.__setitem__("record_type", "failure") for v in d["product_categories"]["STB"]["failure_categories"].values() if v["record_type"] == "service"], "service"),
    ("responsibility 不正", lambda d: d["product_categories"]["ML"]["failure_categories"]["M001"].__setitem__("responsibility", "maker"), "M001"),
    ("接頭文字の重複", lambda d: d["product_categories"]["STB"].__setitem__("code_prefix", "M"), "code_prefix"),
    ("接頭文字と合わないコード", lambda d: d["product_categories"]["STB"]["failure_categories"].__setitem__("X001", {"name": "x", "description": "x", "record_type": "failure", "responsibility": "manufacturer"}), "X001"),
    ("always_user_caused が真偽値でない", lambda d: d["environment_factors"]["impact"].__setitem__("always_user_caused", "yes"), "impact"),
])
def test_r16_validation_errors(tmp_path, label, mutate, expect):
    p = _write_variant(tmp_path, mutate)
    with pytest.raises(CodebookError) as ei:
        load_codebook(p)
    assert expect in str(ei.value), label


def test_r16_all_violations_listed(tmp_path):
    def mutate(d):
        d["product_categories"]["ML"]["failure_categories"]["M001"]["responsibility"] = "maker"
        d["product_categories"]["LENS"]["failure_categories"]["L001"]["responsibility"] = "maker"
    p = _write_variant(tmp_path, mutate)
    with pytest.raises(CodebookError) as ei:
        load_codebook(p)
    assert len(ei.value.errors) >= 2
    assert "M001" in str(ei.value) and "L001" in str(ei.value)


def test_r16_check_cli_ok(capsys):
    from anomaly.check import main
    assert main(["--codebook", str(CODEBOOK_TEST_YAML)]) == 0
    assert "OK: コード体系" in capsys.readouterr().out


def test_r16_check_cli_ng(tmp_path, capsys):
    from anomaly.check import main
    p = _write_variant(tmp_path, lambda d: d["product_categories"]["ML"].pop("code_prefix"))
    assert main(["--codebook", str(p)]) != 0
    assert "code_prefix" in capsys.readouterr().out


def test_E01_claim_statuses_optional(tmp_path):
    """E-01: v2 専用の claim_reproduction_statuses が無くても読み込める。"""
    p = _write_variant(tmp_path, lambda d: d.pop("claim_reproduction_statuses"))
    cb = load_codebook(p)
    assert cb.claim_statuses() == {}
    assert cb.code("ML", "M001").name == "電源不良"


def test_E01_claim_statuses_wrong_type(tmp_path):
    p = _write_variant(tmp_path, lambda d: d.__setitem__("claim_reproduction_statuses", ["x"]))
    with pytest.raises(CodebookError) as ei:
        load_codebook(p)
    assert "claim_reproduction_statuses" in str(ei.value)


def test_r14_repository_default_codebook_is_valid():
    """リポジトリ同梱の初期値 YAML(v2 の 0.4.1 のコピー)が自動チェックを通ること(R-16)。"""
    cb = load_codebook(REPO_ROOT / "config" / "classification_codes.yaml")
    assert cb.version == "0.4.1"
    assert set(cb.product_ids()) >= {"ML", "LENS"}
    assert cb.code_by_role("ML", "connected_device") == "M047"


def test_r21_setting_caused_code_is_not_manufacturer():
    """2026-11-03 追加: 設定・操作起因(M048 / L038)はメーカー責任ではない。"""
    cb = load_codebook(REPO_ROOT / "config" / "classification_codes.yaml")
    assert cb.code("ML", "M048").name == "設定・操作起因"
    assert not cb.is_manufacturer("ML", "M048")
    assert not cb.is_manufacturer("LENS", "L038")


def test_r21_lens_surface_vs_inside():
    """2026-11-03 追加: レンズ表面(L039)はユーザ責任、レンズ内(L028)はメーカー責任。"""
    cb = load_codebook(REPO_ROOT / "config" / "classification_codes.yaml")
    assert not cb.is_manufacturer("LENS", "L039")
    assert cb.is_manufacturer("LENS", "L028")
    assert "L039" in cb.code("LENS", "L028").description


def test_r15_compact_camera_product():
    """2026-11-03 追加: 製品 DC(コンパクトカメラ、接頭文字 D)。"""
    cb = load_codebook(REPO_ROOT / "config" / "classification_codes.yaml")
    assert cb.product_name("DC") == "コンパクトカメラ"
    assert len(cb.codes("DC")) == 48
    assert cb.code_by_role("DC", "connected_device") == "D046"
    assert cb.code_by_role("DC", "other") == "D048"
    assert cb.code_by_role("DC", "unknown") == "D_UNK"
    assert cb.is_manufacturer("DC", "D009") and not cb.is_manufacturer("DC", "D010")
    # 2026-11-03: コンパクトはセンサーにユーザが触れられないため、センサー内ゴミ D017 を削除し、センサーゴミ D018 をメーカー責任に
    assert cb.code("DC", "D017") is None
    assert cb.is_manufacturer("DC", "D018")
    assert {c for c in cb.codes("DC") if cb.is_service("DC", c)} == {"D042", "D043", "D044", "D045"}
