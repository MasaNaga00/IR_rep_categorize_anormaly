"""シリアル変換結果の結合と使用期間(要件定義書 S-08、S-10〜S-19)。

架空の変換結果は tests/fixtures/serial_results/converted_repairs.csv(repairs.csv を変換ツールで変換した想定。
X100 は type2・起点 2018-01、L200 は prefix2_only)。
"""
from __future__ import annotations

import csv

import pytest

from anomaly.input_loader import DEFAULT_COLUMNS, load_column_config, load_input
from anomaly.serial_merge import (MergeSummary, SerialResultError, load_serial_results, main, merge_serial,
                                  result_column_names, unknown_warnings, usage_months, ym_to_index)

from .conftest import FIXTURES, SYNTH, read_csv, write_csv

CONVERTED = FIXTURES / "serial_results" / "converted_repairs.csv"
RESULT_HEADER = ["製品名", "シリアル", "受付日", "serial_normalized", "lot_code", "lot_order", "mfg_ym", "mfg_status", "mfg_warning"]


def _result(tmp_path, rows, header=RESULT_HEADER, name="r.csv"):
    return write_csv(tmp_path / name, header, rows)


def _load(path):
    return load_serial_results(path, "製品名", "受付日")


def _rec(model="X100", serial="0512", received="2026-03-10", **kw):
    return {"rec_key": "000001", "model": model, "serial": serial, "received_date": received, **kw}


@pytest.fixture(scope="module")
def merged(codebook):
    loaded = load_input(SYNTH / "repairs.csv", codebook.product_ids())
    res = _load(CONVERTED)
    summ = merge_serial(loaded.records, res, 96)
    return {r["rec_key"]: r for r in loaded.records}, summ, res


# --- 読み込み(S-10, S-13) ---

def test_S10_load_fixture():
    res = _load(CONVERTED)
    assert res.rows == 15
    assert res.skipped_no_serial == 1           # R012
    assert res.skipped_no_date == 2             # 受付日 2026-13-01 と空欄
    assert len(res.by_key) == 12
    assert len(res.sha256) == 64
    v = res.by_key[("X100", "061234567", "2026-01-15")]
    assert (v.mfg_ym, v.lot_code, v.lot_order, v.mfg_status) == ("2018-06", "06", 24221, "ok")


def test_S10_result_column_names_default_and_override(tmp_path):
    assert result_column_names(dict(DEFAULT_COLUMNS)) == ("製品名", "受付日")
    p = tmp_path / "c.yaml"
    p.write_text("serial_result_model: Model\nserial_result_reference_date: RefDate\n", encoding="utf-8")
    assert result_column_names(load_column_config(p)) == ("Model", "RefDate")


def test_S10_custom_column_names(tmp_path):
    p = _result(tmp_path, [["X100", "0512", "2026-03-10", "0512", "05", "24220", "2018-05", "ok", ""]],
                header=["Model", "Serial", "RefDate"] + RESULT_HEADER[3:])
    res = load_serial_results(p, "Model", "RefDate")
    assert ("X100", "0512", "2026-03-10") in res.by_key


def test_S13_missing_columns(tmp_path):
    p = _result(tmp_path, [], header=["製品名", "受付日", "mfg_ym"])
    with pytest.raises(SerialResultError) as ei:
        _load(p)
    assert "列がありません" in str(ei.value) and "mfg_status" in str(ei.value)


@pytest.mark.parametrize("row, expect", [
    (["X100", "0512", "2026-03-10", "0512", "05", "24220", "2018-05", "done", ""], "mfg_status"),
    (["X100", "0512", "2026-03-10", "0512", "05", "24220", "2018/05", "ok", ""], "mfg_ym"),
    (["X100", "0512", "2026-03-10", "0512", "05", "24220", "2018-13", "ok", ""], "mfg_ym"),
    (["X100", "0512", "2026-03-10", "0512", "05", "1.5", "2018-05", "ok", ""], "lot_order"),
    (["X100", "0512", "2026-03-10", "0512", "05", "abc", "2018-05", "ok", ""], "lot_order"),
    (["X100", "0512", "2026-03-10", "0512", "05", "24220", "", "ok", ""], "mfg_ym が空"),
])
def test_S13_invalid_values(tmp_path, row, expect):
    with pytest.raises(SerialResultError) as ei:
        _load(_result(tmp_path, [row]))
    assert expect in str(ei.value)
    assert "2行目" in str(ei.value)


def test_S13_lot_order_integral_float_accepted(tmp_path):
    res = _load(_result(tmp_path, [["X100", "0512", "2026-03-10", "0512", "05", "24220.0", "2018-05", "ok", ""]]))
    assert res.by_key[("X100", "0512", "2026-03-10")].lot_order == 24220


def test_S13_errors_listed_up_to_10(tmp_path):
    rows = [["X100", f"05{i:02d}", "2026-03-10", f"05{i:02d}", "05", "x", "", "bad", ""] for i in range(8)]
    with pytest.raises(SerialResultError) as ei:
        _load(_result(tmp_path, rows))
    assert ei.value.total == 16                 # 1行に2つの違反(mfg_status, lot_order)× 8行
    assert len(ei.value.errors) == 10
    assert "ほか 6 件" in str(ei.value)


def test_S13_not_utf8(tmp_path):
    p = tmp_path / "sjis.csv"
    p.write_bytes(",".join(RESULT_HEADER).encode("utf-8") + "\nX100,0512,2026-03-10,0512,05,1,2018-05,ok,電源\n".encode("cp932"))
    with pytest.raises(SerialResultError) as ei:
        _load(p)
    assert "UTF-8" in str(ei.value)


def test_S10_multiline_fields_in_result(tmp_path):
    """変換ツールは入力の列(コメントなど)をそのまま残すため、改行を含む列があっても読めること。"""
    header = ["製品名", "シリアル", "受付日", "user_comment"] + RESULT_HEADER[3:]
    p = _result(tmp_path, [["X100", "0512", "2026-03-10", "1行目\n2行目", "0512", "05", "24220", "2018-05", "ok", ""],
                           ["X100", "0612", "2026-03-10", "", "0612", "06", "24221", "2018-06", "ok", ""]], header=header)
    res = _load(p)
    assert res.rows == 2 and len(res.by_key) == 2


# --- 同じキーの行(S-12) ---

def test_S12_identical_duplicates_merged(tmp_path):
    row = ["X100", "0512", "2026-03-10", "0512", "05", "24220", "2018-05", "ok", ""]
    res = _load(_result(tmp_path, [row, list(row)]))
    assert len(res.by_key) == 1 and res.duplicate_rows == 1


def test_S12_conflicting_duplicate_keys_is_error(tmp_path):
    a = ["X100", "0512", "2026-03-10", "0512", "05", "24220", "2018-05", "ok", ""]
    b = ["X100", "0512", "2026/3/10", "0512", "05", "24316", "2026-05", "ok", ""]   # 日付の書き方が違っても同じキー
    with pytest.raises(SerialResultError) as ei:
        _load(_result(tmp_path, [a, b]))
    assert "変換結果が違う" in str(ei.value) and "0512" in str(ei.value)


# --- 結合(S-11, S-14〜S-16) ---

def test_S11_merge_fixture(merged):
    recs, summ, _ = merged
    r = recs["000001"]
    assert (r["mfg_ym"], r["lot_code"], r["lot_order"], r["mfg_status"]) == ("2018-06", "06", 24221, "ok")
    assert (r["usage_months"], r["usage_status"]) == (91, "ok")
    assert recs["000003"]["mfg_ym"] == "2018-07"          # 修理IDが重複していても、キーで正しく結合
    assert recs["000004"]["mfg_status"] == "invalid" and recs["000004"]["lot_code"] == "AB"   # 全角シリアル・Excel 日付
    assert recs["000012"]["mfg_status"] == "invalid"     # 前後の空白・小文字・時刻付きの日付
    assert recs["000014"]["mfg_status"] == "no_serial"
    assert dict(summ.status_counts) == {"ok": 5, "invalid": 2, "no_serial": 1}
    assert dict(summ.usage_counts) == {"ok": 5, "no_mfg_ym": 3}
    assert summ.total == 8 and summ.converted_ratio() == pytest.approx(5 / 8)


def test_S11_key_normalization(tmp_path):
    p = _result(tmp_path, [[" X100 ", "ab12", "2026/3/10 09:00:00", "ａｂ１２", "AB", "", "", "invalid", ""]])
    recs = [_rec(serial="AB12")]
    merge_serial(recs, _load(p), 96)
    assert recs[0]["mfg_status"] == "invalid"


def test_S14_not_converted(tmp_path):
    p = _result(tmp_path, [["X100", "0512", "2026-03-10", "0512", "05", "24220", "2018-05", "ok", ""]])
    recs = [_rec(received="2026-03-11"),                 # 受付日が違う
            _rec(model="X200"),                          # 製品名が違う
            _rec(model="X200", serial="0612"),
            _rec(serial="")]                             # シリアルが空 → no_serial
    summ = merge_serial(recs, _load(p), 96)
    assert [r["mfg_status"] for r in recs] == ["not_converted"] * 3 + ["no_serial"]
    assert summ.not_converted_models == {"X200": 2, "X100": 1}
    assert all(r["mfg_ym"] == "" and r["lot_order"] is None for r in recs)


def test_S14_empty_serial_ignores_result(tmp_path):
    p = _result(tmp_path, [["X100", "", "2026-03-10", "", "", "", "", "no_serial", ""]])
    res = _load(p)
    assert res.skipped_no_serial == 1
    recs = [_rec(serial="")]
    merge_serial(recs, res, 96)
    assert recs[0]["mfg_status"] == "no_serial"


def test_S15_without_result_file(codebook):
    loaded = load_input(SYNTH / "repairs.csv", codebook.product_ids())
    summ = merge_serial(loaded.records, None, 96)
    assert dict(summ.status_counts) == {"not_converted": 7, "no_serial": 1}
    assert summ.converted_ratio() == 0
    assert "指定なし" in summ.summary_lines()[0]
    assert summ.as_dict()["serial_result_file"] is None


def test_S16_S17_summary(merged):
    _, summ, res = merged
    d = summ.as_dict()
    assert d["mfg_status"] == {"ok": 5, "invalid": 2, "no_serial": 1}
    assert d["serial_result_file"]["sha256"] == res.sha256
    assert d["serial_result_file"]["path"].endswith("converted_repairs.csv")
    lines = summ.summary_lines()
    assert "converted_repairs.csv" in lines[0] and "15 行" in lines[0]
    assert any("ok 5" in l and "全 8 件" in l for l in lines)
    assert len(lines) <= 6


def test_S16_not_converted_models_top10(tmp_path):
    p = _result(tmp_path, [["X100", "0512", "2026-03-10", "0512", "05", "24220", "2018-05", "ok", ""]])
    recs = [_rec(model=f"M{i:02d}") for i in range(12) for _ in range(i + 1)]
    summ = merge_serial(recs, _load(p), 96)
    line = next(l for l in summ.summary_lines() if "結合できなかった機種" in l)
    assert "M11 12" in line and "M00" not in line and "M01" not in line   # 上位10件だけ


# --- 注意(S-19) ---

def test_S19_warning_counts(tmp_path):
    rows = [["X100", "0512", "2026-03-10", "0512", "05", "24220", "2018-05", "ok", "after_reference"],
            ["X100", "0612", "2026-03-10", "0612", "06", "24221", "2018-06", "ok", "serial_was_number;after_reference"],
            ["X100", "0712", "2026-03-10", "0712", "07", "24222", "2018-07", "ok", "future_flag"]]
    recs = [_rec(serial="0512"), _rec(serial="0612"), _rec(serial="0712")]
    summ = merge_serial(recs, _load(_result(tmp_path, rows)), 96)
    assert summ.warning_counts == {"after_reference": 2, "serial_was_number": 1, "future_flag": 1}
    assert recs[1]["mfg_warning"] == "serial_was_number;after_reference"
    assert unknown_warnings(summ) == ["future_flag"]


# --- 使用期間(S-08) ---

@pytest.mark.parametrize("received, ym, expect", [
    ("2026-03-10", "2026-03", (0, "ok")),
    ("2026-03-31", "2025-03", (12, "ok")),
    ("2026-03-10", "2018-03", (96, "ok")),            # 上限ちょうど
    ("2026-03-10", "2018-02", (97, "over_max")),
    ("2026-03-10", "2026-04", (-1, "negative")),
    ("2026-03-10", "", (None, "no_mfg_ym")),
])
def test_S08_usage_months(received, ym, expect):
    assert usage_months(received, ym, 96) == expect


def test_S08_usage_max_from_settings(tmp_path):
    p = _result(tmp_path, [["X100", "0512", "2026-03-10", "0512", "05", "24220", "2018-05", "ok", ""]])
    recs = [_rec()]
    merge_serial(recs, _load(p), 60)
    assert recs[0]["usage_status"] == "over_max" and recs[0]["usage_months"] == 94


def test_S08_ym_to_index():
    assert ym_to_index("2026-01") == 24312 and ym_to_index("2018-05") == 24220


# --- コマンド(U-07: merge-serial) ---

def test_U07_merge_serial_command(tmp_path, capsys):
    out = tmp_path / "merged.csv"
    rc = main(["--input", str(SYNTH / "repairs.csv"), "--serial-result", str(CONVERTED), "--output", str(out)])
    text = capsys.readouterr().out
    assert rc == 0
    assert "8 件、除外 7 件" in text and "ok 5" in text
    assert "除外: コード体系に無い製品種別 1 件(値の例: 'XX' 1件)" in text and "excluded_rows.csv" not in text   # I-05a
    rows = read_csv(out)
    assert len(rows) == 8
    r = next(x for x in rows if x["rec_key"] == "000001")
    assert (r["mfg_ym"], r["usage_months"], r["usage_status"]) == ("2018-06", "91", "ok")
    assert main(["--input", str(SYNTH / "repairs.csv"), "--serial-result", str(CONVERTED), "--output", str(out)]) == 1   # 上書きしない


def test_U07_merge_serial_command_without_result(capsys):
    assert main(["--input", str(SYNTH / "repairs.csv")]) == 0
    assert "指定なし" in capsys.readouterr().out


def test_U07_merge_serial_command_bad_result(tmp_path, capsys):
    p = _result(tmp_path, [["X100", "0512", "2026-03-10", "0512", "05", "24220", "2018-05", "done", ""]])
    assert main(["--input", str(SYNTH / "repairs.csv"), "--serial-result", str(p)]) == 1
    assert "NG:" in capsys.readouterr().out
