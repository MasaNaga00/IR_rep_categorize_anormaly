"""コード体系の Excel 入出力。

v2 の tests/v2/test_codes_excel.py をコピーしたもの。テスト名の R-xx は v2 の要件 ID。
"""
from __future__ import annotations

import shutil

import openpyxl
import pytest
import yaml

from anomaly.codebook import load_codebook
from anomaly.codes_excel import CODE_HEADERS, CodesImportError, export_codes, import_codes

from .conftest import CODEBOOK_TEST_YAML, REPO_ROOT


@pytest.fixture
def yaml_copy(tmp_path):
    p = tmp_path / "classification_codes.yaml"
    shutil.copyfile(REPO_ROOT / "config" / "classification_codes.yaml", p)
    return p


@pytest.fixture
def xlsx(tmp_path, yaml_copy):
    x = tmp_path / "codes.xlsx"
    export_codes(yaml_copy, x)
    return x


def _row_of(ws, code):
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, 1).value == code:
            return r
    raise KeyError(code)


def _col(name):
    return CODE_HEADERS.index(name) + 1


# --- 書き出し(R-58, R-60) ---

def test_r60_sheets_and_headers(xlsx):
    wb = openpyxl.load_workbook(xlsx)
    assert wb.sheetnames == ["説明", "製品設定", "環境要因", "ML", "LENS", "DC"]
    assert [c.value for c in wb["ML"][1]] == CODE_HEADERS
    assert [c.value for c in wb["製品設定"][1]] == ["製品ID", "表示名", "接頭文字"]
    assert [c.value for c in wb["環境要因"][1]] == ["要因", "名称", "説明文", "常にユーザ起因", "キーワード"]
    ws = wb["ML"]
    r = _row_of(ws, "M001")
    assert ws.cell(r, _col("名称")).value == "電源不良"
    assert ws.cell(r, _col("責任区分")).value == "manufacturer"
    assert ws.cell(_row_of(ws, "M047"), _col("役割")).value == "connected_device"
    assert ws.max_row - 1 == 49
    assert ws.data_validations.dataValidation   # ドロップダウンがある


def test_r58_no_overwrite_without_force(tmp_path, yaml_copy, xlsx):
    with pytest.raises(FileExistsError):
        export_codes(yaml_copy, xlsx)
    export_codes(yaml_copy, xlsx, force=True)


# --- 往復(R-59, R-60) ---

def test_r59_roundtrip_keeps_content(tmp_path, yaml_copy, xlsx):
    before = load_codebook(yaml_copy)
    raw_before = yaml.safe_load(yaml_copy.read_text(encoding="utf-8"))
    res = import_codes(xlsx, yaml_copy, now="20261103_070000")
    after = load_codebook(yaml_copy)
    assert res.added == {} and res.changed == {} and res.removed == {}
    assert after.product_ids() == before.product_ids()
    for pid in before.product_ids():
        assert after.codes(pid) == before.codes(pid)
        assert after.environment_factors(pid) == before.environment_factors(pid)
    assert after.claim_statuses() == before.claim_statuses()
    raw_after = yaml.safe_load(yaml_copy.read_text(encoding="utf-8"))
    # Excel に無い項目は引き継ぐ(R-60)
    assert raw_after["product_categories"]["ML"]["failure_categories"]["M046"]["is_special"] is True
    assert raw_after["environment_factors"]["water"]["keywords"] == raw_before["environment_factors"]["water"]["keywords"]
    assert raw_after["meta"]["version"] == raw_before["meta"]["version"]


def test_r62_backup(tmp_path, yaml_copy, xlsx):
    original = yaml_copy.read_bytes()
    res = import_codes(xlsx, yaml_copy, now="20261103_070000")
    assert res.backup_path == tmp_path / "classification_codes_20261103_070000.bak.yaml"
    assert res.backup_path.read_bytes() == original


def test_r63_changes_and_changelog(tmp_path, yaml_copy, xlsx):
    wb = openpyxl.load_workbook(xlsx)
    ws = wb["ML"]
    ws.cell(_row_of(ws, "M028"), _col("責任区分")).value = "user_or_unknown"       # 変更
    ws.delete_rows(_row_of(ws, "M037"))                                          # 削除
    ws.append(["M049", "新しいコード", "テスト用", "failure", "manufacturer", None, None, "verified"])  # 追加
    wb["環境要因"].cell(2, 4).value = True if wb["環境要因"].cell(2, 4).value is False else False
    wb.save(xlsx)
    res = import_codes(xlsx, yaml_copy, now="20261103_070000")
    assert res.added == {"ML": ["M049"]}
    assert res.changed == {"ML": ["M028"]}
    assert res.removed == {"ML": ["M037"]}
    assert res.env_changed
    cb = load_codebook(yaml_copy)
    assert not cb.is_manufacturer("ML", "M028")
    assert cb.code("ML", "M037") is None and cb.code("ML", "M049").name == "新しいコード"
    raw = yaml.safe_load(yaml_copy.read_text(encoding="utf-8"))
    last = raw["meta"]["changelog"][0]
    assert last["date"] == "2026-11-03" and "Excel" in last["changes"][0]
    assert "追加 1" in last["changes"][0]


def test_r60_add_product(tmp_path):
    y = tmp_path / "classification_codes.yaml"
    shutil.copyfile(CODEBOOK_TEST_YAML, y)
    x = tmp_path / "codes.xlsx"
    export_codes(y, x)
    wb = openpyxl.load_workbook(x)
    wb["製品設定"].append(["PRN", "プリンター", "P"])
    ws = wb.copy_worksheet(wb["STB"])
    ws.title = "PRN"
    for r in range(2, ws.max_row + 1):
        ws.cell(r, 1).value = ws.cell(r, 1).value.replace("S", "P", 1)
    wb.save(x)
    res = import_codes(x, y, now="20261103_070000")
    cb = load_codebook(y)
    assert "PRN" in cb.product_ids() and cb.product_name("PRN") == "プリンター"
    assert cb.code_by_role("PRN", "connected_device") == "P003"
    assert sorted(res.added["PRN"]) == ["P001", "P002", "P003", "P004", "P_UNK"]
    # 製品ごとの環境要因の上書きは引き継ぐ(R-60)
    assert cb.environment_factors("STB")["battery_leak"].always_user_caused is True


# --- 取り込みの安全策(R-61) ---

@pytest.mark.parametrize("label, mutate, expect", [
    ("責任区分の値が不正", lambda wb: wb["ML"].cell(2, _col("責任区分")).__setattr__("value", "maker"), "responsibility"),
    ("コードの重複", lambda wb: wb["ML"].append(["M001", "重複", "x", "failure", "manufacturer", None, None, "verified"]), "M001"),
    ("製品のシートが無い", lambda wb: wb["製品設定"].append(["PRN", "プリンター", "P"]), "PRN"),
    ("接頭文字と合わない", lambda wb: wb["LENS"].append(["X001", "x", "x", "failure", "manufacturer", None, None, "verified"]), "X001"),
    ("接続機器要因の役割が無い", lambda wb: wb["ML"].cell(_row_of(wb["ML"], "M047"), _col("役割")).__setattr__("value", None), "connected_device"),
    ("常にユーザ起因が読めない", lambda wb: wb["環境要因"].cell(2, 4).__setattr__("value", "たぶん"), "常にユーザ起因"),
])
def test_r61_invalid_excel_does_not_touch_yaml(tmp_path, yaml_copy, xlsx, label, mutate, expect):
    original = yaml_copy.read_bytes()
    wb = openpyxl.load_workbook(xlsx)
    mutate(wb)
    wb.save(xlsx)
    with pytest.raises(CodesImportError) as ei:
        import_codes(xlsx, yaml_copy, now="20261103_070000")
    assert expect in str(ei.value), label
    assert yaml_copy.read_bytes() == original
    assert not list(tmp_path.glob("*.bak.yaml"))


# --- コマンド ---

def test_r58_r59_cli(tmp_path, capsys):
    from anomaly.codes_excel import main_export, main_import
    local = tmp_path / "local"
    local.mkdir()
    shutil.copyfile(REPO_ROOT / "config" / "classification_codes.yaml", local / "classification_codes.yaml")
    assert main_export(["--local-dir", str(local)]) == 0
    x = local / "classification_codes.xlsx"
    assert x.exists()
    assert main_export(["--local-dir", str(local)]) != 0          # 上書きしない
    assert main_import(["--local-dir", str(local), "--xlsx", str(x)]) == 0
    out = capsys.readouterr().out
    assert "変更なし" in out or "追加 0" in out
    assert len(out.splitlines()) <= 20
