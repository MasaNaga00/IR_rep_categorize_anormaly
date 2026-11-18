"""分類タグ付きの修理データ tagged_repairs.xlsx(要件定義書 O-08)。架空データは synth_classified.make()。

RUN_DATE = 2025-07-10 なので、直近6か月 = 2025-01〜2025-06。
"""
from __future__ import annotations

from datetime import datetime

import openpyxl

from anomaly.analysis.aspects import analyze_combo
from anomaly.analysis.prepare import build_combo
from anomaly.analyze import analyze_records
from anomaly.input_loader import DEFAULT_COLUMNS
from anomaly.outputs.tagged_repairs import tagged_rows
from anomaly.settings import parse_settings

from .synth_classified import RUN_DATE, balanced, idx, make

RECENT, BEFORE = idx(2025, 3), idx(2024, 3)


def _env(factor, abnormal=False, source="user"):
    return [{"factor": factor, "source": source, "abnormal_use": abnormal, "excerpt": ""}]


def _sample():
    recs = [
        make(3, "M001", RECENT, mfg_idx=RECENT - 5),                                  # 24か月未満・直近
        make(1, "M005", BEFORE, mfg_idx=BEFORE - 30, env=_env("impact")),             # 24か月以上・落下
        make(2, "UNK", BEFORE),                                                       # 判定不能・製造年月不明
        make(4, "FAILED", RECENT, mfg_idx=RECENT + 2),                                # 分類不可・日付矛盾
        make(5, "M008", BEFORE, mfg_idx=BEFORE - 24, env=_env("heat", source="repair")),   # ちょうど24か月・通常の高温
        make(6, "M009", BEFORE, mfg_idx=BEFORE - 10, mfg_status="service_part"),
    ]
    recs[0]["failures"] = [{"code": "M002", "source": "user", "excerpt": "Error 70", "excerpt_ja": "エラー70"},
                           {"code": "M001", "source": "repair", "excerpt": "PCB NG", "excerpt_ja": "基板不良"}]
    recs[-1].update(usage_months=None, usage_status="no_mfg_ym")                       # 結合のとき使用期間を計算しない(S-08a)
    return recs


def _rows(codebook, recs, **settings):
    s = parse_settings(settings or None)
    combo = build_combo(recs[0]["model"], recs[0]["part_no"], recs, codebook, s.rare_code_min, s.keep_top_codes)
    return tagged_rows(analyze_combo(combo, codebook, s, RUN_DATE), codebook, s, RUN_DATE)


def test_O08_rows_in_input_order_with_tags(codebook):
    rows = _rows(codebook, _sample())
    assert [r["行番号"] for r in rows] == [1, 2, 3, 4, 5, 6]                       # 元の CSV の行の順
    by = {r["行番号"]: r for r in rows}
    r3 = by[3]
    assert r3["主な故障コード"] == "M001" and r3["主な故障"] == "電源不良"
    assert r3["書かれていた故障すべて"] == "エラー(ユーザ); 電源不良(修理者)"
    assert (r3["主な故障の根拠(原文)"], r3["主な故障の根拠(日本語訳)"]) == ("PCB NG", "基板不良")
    assert r3["分類の状態"] == "分類できた" and r3["直近6か月"] == "○"
    assert r3["使用期間の区分"] == "製造後24か月未満" and r3["使用期間(月)"] == 5
    assert by[1]["環境要因"] == "落下・衝撃(ユーザ)" and by[1]["環境要因で除いた修理"] == "○"
    assert by[1]["使用期間の区分"] == "製造後24か月以上" and by[1]["直近6か月"] == ""
    assert by[2]["分類の状態"] == "判定不能" and by[2]["使用期間の区分"] == "製造年月不明"
    assert by[4]["分類の状態"] == "分類不可" and by[4]["主な故障"] == "分類不可" and by[4]["使用期間の区分"] == "日付矛盾"
    assert by[4]["書かれていた故障すべて"] == ""
    assert by[5]["使用期間の区分"] == "製造後24か月以上"                              # 24か月ちょうどは「以上」
    assert by[5]["環境要因"] == "高温環境(修理者)" and by[5]["環境要因で除いた修理"] == ""   # 通常の使用の高温は除かない
    assert by[6]["使用期間の区分"] == "サービス部品" and by[6]["使用期間(月)"] is None


def test_O08_settings_change_headers_and_marks(codebook):
    rows = _rows(codebook, _sample(), recent_months=3, usage_split_months=12, env_exclude_mode="any")
    by = {r["行番号"]: r for r in rows}
    assert "直近3か月" in by[3] and by[3]["直近3か月"] == ""                       # 2025-03 は直近3か月(04〜06)の外
    assert by[3]["使用期間の区分"] == "製造後12か月未満"
    assert by[5]["環境要因で除いた修理"] == "○"                                    # any なら通常の高温も除く


def test_O08_notable_lot_marked_only_when_notable(codebook):
    assert not any(r["注目点のロット"] for r in _rows(codebook, balanced()))     # 差の無いデータでは付かない
    recs = balanced()
    lot = idx(2022, 5)
    for i in range(40):
        recs.append(make(90000 + i, "M009", lot + 6, mfg_idx=lot))
    rows = _rows(codebook, recs)
    marked = {r["製造年月"] for r in rows if r["注目点のロット"]}
    assert "2022-05" in marked and marked <= {"2022-04", "2022-05", "2022-06"}     # 1ロット、または隣り合うまとまり


def test_O08_file_per_combo_with_original_column_names(codebook, tmp_path):
    cols = dict(DEFAULT_COLUMNS, repair_id="RepairNo", user_comment="UserComment")
    recs = _sample() + [make(7, "M001", BEFORE, model="L200", part="P-777", product="LENS")]
    analyze_records(recs, codebook, parse_settings(None), RUN_DATE, tmp_path, columns=cols)
    path = tmp_path / "combos" / "X100__P-001" / "tagged_repairs.xlsx"
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["修理データ", "列の説明"]
    ws = wb["修理データ"]
    heads = [c.value for c in ws[1]]
    assert heads[:4] == ["行番号", "RepairNo", "販社名", "受付日"] and "UserComment" in heads and "Internal_2" in heads
    assert heads[-1] == "注目点のロット" and "主な故障" in heads
    assert ws.max_row == 1 + 6                                                    # この組み合わせの6件
    assert isinstance(ws.cell(2, 4).value, datetime)                              # 受付日は Excel の日付
    assert ws.freeze_panes == "C2" and ws.auto_filter.ref
    assert (tmp_path / "combos" / "L200__P-777" / "tagged_repairs.xlsx").exists()   # 組み合わせごと
    doc = [r[0].value for r in wb["列の説明"].iter_rows(min_row=2)]
    assert doc[:2] == ["行番号", "RepairNo"] and "直近6か月" in doc
