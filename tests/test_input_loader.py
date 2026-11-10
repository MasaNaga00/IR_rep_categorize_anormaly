"""入力 CSV の読み込み(要件定義書 I-01〜I-06、G-11、G-12、G-25)。架空データは fixtures/synthetic/README.md。"""
from __future__ import annotations

from datetime import date

import pytest

from anomaly.input_loader import (DEFAULT_COLUMNS, EXCLUDED_FIELDS, InputError, has_text, llm_comments,
                                  load_column_config, load_input, normalize_serial, parse_date)

from .conftest import REPO_ROOT, SYNTH, write_csv


@pytest.fixture(scope="module")
def loaded(codebook):
    return load_input(SYNTH / "repairs.csv", codebook.product_ids())


def _by_key(res):
    return {r["rec_key"]: r for r in res.records}


def test_I01_counts(loaded):
    assert loaded.total_rows == 15
    assert len(loaded.records) == 8
    assert len(loaded.excluded) == 7


def test_I01_fields(loaded):
    r = _by_key(loaded)["000002"]
    assert r["repair_id"] == "R002" and r["model"] == "X100" and r["product_type"] == "ML"
    assert r["part_no"] == "P-001" and r["serial"] == "071234568"
    assert r["received_date"] == "2026-02-03" and r["sales_company"] == "CUSA"
    assert r["user_comment"] == "Camera does not power on" and r["repair_comment"] == "Replaced main board"
    assert r["internal_1"] == "" and r["internal_2"] == ""
    assert r["row_number"] == 2


def test_I02_duplicate_repair_id_kept_as_separate_repairs(loaded):
    recs = [r for r in loaded.records if r["repair_id"] == "R002"]
    assert [r["rec_key"] for r in recs] == ["000002", "000003"]
    assert recs[0]["user_comment"] != recs[1]["user_comment"]
    assert loaded.duplicate_repair_ids() == {"R002": 2}
    assert loaded.duplicate_id_rows() == 2


def test_I02_rec_key_is_row_based_and_unique(loaded):
    keys = [r["rec_key"] for r in loaded.records]
    assert len(set(keys)) == len(keys)
    assert all(len(k) == 6 and k.isdigit() for k in keys)
    assert "000007" not in keys     # 除外した行の番号は欠番のまま(他の行の rec_key は変わらない)
    assert "000012" in keys


def test_I03_combos(loaded):
    assert loaded.combos() == {("X100", "P-001"): 7, ("L200", "P-777"): 1}


def test_I04_date_formats(loaded):
    k = _by_key(loaded)
    assert k["000001"]["received_date"] == "2026-01-15"   # YYYY-MM-DD
    assert k["000003"]["received_date"] == "2026-02-05"   # YYYY/M/D
    assert k["000004"]["received_date"] == "2025-12-09"   # Excel の日付シリアル値
    assert k["000012"]["received_date"] == "2026-03-01"   # 時刻付き


@pytest.mark.parametrize("value, expect", [
    ("2026-01-15", date(2026, 1, 15)),
    ("2026/1/5", date(2026, 1, 5)),
    ("2026.01.05", date(2026, 1, 5)),
    ("２０２６/０１/０５", date(2026, 1, 5)),   # 全角
    ("2026/03/01 10:20:00", date(2026, 3, 1)),
    ("2026-03-01T10:20:00", date(2026, 3, 1)),
    ("46000", date(2025, 12, 9)),
    ("46000.5", date(2025, 12, 9)),
    ("2026-13-01", None),
    ("2026-02-30", None),
    ("12345", None),                         # Excel の日付として範囲外
    ("20250717", date(2025, 7, 17)),         # 区切りの無い8桁は年月日(YYYYMMDD)
    ("２０２５０７１７", date(2025, 7, 17)),   # 全角の8桁
    ("20251301", None),                      # 8桁でも実在しない日付は読まない
    ("20250230", None),
    ("2025071", None),                       # 7桁(Excel の日付として範囲外)
    ("202507170", None),                     # 9桁
    ("", None),
    (None, None),
    ("abc", None),
])
def test_I04_parse_date(value, expect):
    assert parse_date(value) == expect


def test_I05_exclusion_reasons(loaded):
    reasons = {e["rec_key"]: e["reason"] for e in loaded.excluded}
    assert reasons == {
        "000007": "missing_model",
        "000008": "invalid_received_date",
        "000009": "unknown_product",
        "000010": "missing_part_no",
        "000011": "missing_repair_id",
        "000013": "missing_sales_company",
        "000015": "missing_received_date",
    }
    assert loaded.excluded_counts() == {
        "missing_repair_id": 1, "missing_model": 1, "missing_part_no": 1, "missing_sales_company": 1,
        "missing_received_date": 1, "invalid_received_date": 1, "unknown_product": 1,
    }
    bad = next(e for e in loaded.excluded if e["reason"] == "invalid_received_date")
    assert bad["value"] == "2026-13-01"


def test_I05a_excluded_rows_values(loaded):
    rows = {e["rec_key"]: e for e in loaded.excluded_rows()}
    assert set(rows["000009"]) == set(EXCLUDED_FIELDS)
    assert rows["000009"]["value"] == "XX" and rows["000009"]["reason_name"] == "コード体系に無い製品種別"
    assert rows["000008"]["value"] == "2026-13-01" and rows["000008"]["sales_company"] == "CEU"
    assert rows["000007"]["value"] == ""                          # 必須の値が空の行は値なし
    assert rows["000011"]["repair_id"] == ""


def test_I05a_excluded_summary_lines(loaded):
    t = "\n".join(loaded.excluded_summary_lines())
    assert "除外: 製品名が空 1 件" in t
    assert "除外: 受付日が読めない 1 件(値の例: '2026-13-01' 1件)" in t
    assert "除外: コード体系に無い製品種別 1 件(値の例: 'XX' 1件)" in t
    assert "除外した行の一覧: excluded_rows.csv" in t and "すべての行が除外" not in t
    assert "excluded_rows.csv" not in "\n".join(loaded.excluded_summary_lines(list_file=None))


def test_I05a_all_excluded_top5_values(tmp_path, codebook):
    header = [DEFAULT_COLUMNS[k] for k in DEFAULT_COLUMNS]
    rows = []
    for i, (ptype, d) in enumerate([("ml", "2026-01-05")] * 3 + [("ML", f"0{m}/05/2026") for m in range(1, 8)]):
        rows.append([f"A{i}", "X9", ptype, "P1", "", d, "CEU", "u", "r", "", ""])
    res = load_input(write_csv(tmp_path / "x.csv", header, rows), codebook.product_ids())
    assert not res.records and res.excluded_counts() == {"invalid_received_date": 7, "unknown_product": 3}
    t = "\n".join(res.excluded_summary_lines())
    assert "コード体系に無い製品種別 3 件(値の例: 'ml' 3件)" in t
    assert "受付日が読めない 7 件(値の例: '01/05/2026' 1件, " in t and "ほか 2 種類" in t
    assert "注意: すべての行が除外されました" in t


def test_I06_serial_normalized(loaded):
    k = _by_key(loaded)
    assert k["000004"]["serial"] == "AB123456"    # 全角 → 半角、大文字
    assert k["000012"]["serial"] == "AB12X"       # 前後の空白を除く
    assert k["000014"]["serial"] == ""


@pytest.mark.parametrize("value, expect", [(" ab12 ", "AB12"), ("ＡＢ１２", "AB12"), (None, ""), ("", "")])
def test_I06_normalize_serial(value, expect):
    assert normalize_serial(value) == expect


def test_I01_key_fields_stripped(loaded):
    r = _by_key(loaded)["000012"]
    assert (r["repair_id"], r["model"], r["part_no"]) == ("R010", "L200", "P-777")


def test_I01_comments_kept_as_is(loaded):
    r = _by_key(loaded)["000001"]
    assert r["internal_1"] == "お客様へ見積連絡済み。"


def test_I01_missing_required_column(tmp_path, codebook):
    header = [v for k, v in DEFAULT_COLUMNS.items() if k != "part_no"]
    p = write_csv(tmp_path / "x.csv", header, [])
    with pytest.raises(InputError) as ei:
        load_input(p, codebook.product_ids())
    assert "部品番号" in str(ei.value)


def test_I01_serial_column_optional(codebook):
    res = load_input(SYNTH / "repairs_no_serial.csv", codebook.product_ids())
    assert res.has_serial_column is False
    assert len(res.records) == 1 and res.records[0]["serial"] == ""


def test_I01_custom_columns(tmp_path, codebook):
    cols = dict(DEFAULT_COLUMNS, repair_id="RepairNo", model="Model")
    header = [cols[k] for k in DEFAULT_COLUMNS]
    p = write_csv(tmp_path / "x.csv", header, [["A1", "X9", "ML", "P1", "S1", "2026-01-01", "CEU", "u", "r", "", ""]])
    res = load_input(p, codebook.product_ids(), cols)
    assert res.records[0]["repair_id"] == "A1" and res.records[0]["model"] == "X9"


def test_I01_header_with_spaces(tmp_path, codebook):
    header = [f" {v} " for v in DEFAULT_COLUMNS.values()]
    p = write_csv(tmp_path / "x.csv", header, [["A1", "X9", "ML", "P1", "S1", "2026-01-01", "CEU", "u", "r", "", ""]])
    assert len(load_input(p, codebook.product_ids()).records) == 1


def test_I01_column_config_repository_default():
    cols = load_column_config(REPO_ROOT / "config" / "input_columns.yaml")
    assert {k: v for k, v in cols.items() if k in DEFAULT_COLUMNS} == DEFAULT_COLUMNS
    assert cols["serial_result_model"] is None and cols["serial_result_reference_date"] is None


def test_I01_column_config_errors(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("repair_id: RepairNo\nunknown: X\nmodel: ''\n", encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        load_column_config(p)
    assert "unknown" in str(ei.value) and "model" in str(ei.value)


def test_I01_column_config_missing_file_uses_defaults(tmp_path):
    cols = load_column_config(tmp_path / "none.yaml")
    assert {k: cols[k] for k in DEFAULT_COLUMNS} == DEFAULT_COLUMNS


def test_G12_G25_llm_comments(loaded, settings):
    r = _by_key(loaded)["000001"]                                        # CMJ
    c = llm_comments(r, settings.internal_excluded(r["sales_company"]))
    assert set(c) == {"user_comment", "repair_comment"}                  # 内部コメントはキーごと省く
    r2 = _by_key(loaded)["000003"]                                       # CUSA
    c2 = llm_comments(r2, settings.internal_excluded(r2["sales_company"]))
    assert set(c2) == {"user_comment", "repair_comment", "internal_1", "internal_2"}
    assert c2["internal_2"] == "Main PCB replaced"


def test_G11_has_text(loaded, settings):
    k = _by_key(loaded)
    assert has_text(llm_comments(k["000002"], False))
    assert not has_text(llm_comments(k["000006"], False))               # すべて空欄
    assert not has_text({"user_comment": "  \n ", "repair_comment": ""})  # 空白だけは空
    # 内部コメントにだけ中身がある修理は、内部コメントを渡さないと空になる
    rec = {"user_comment": "", "repair_comment": "", "internal_1": "PCB NG", "internal_2": ""}
    assert has_text(llm_comments(rec, False)) and not has_text(llm_comments(rec, True))
