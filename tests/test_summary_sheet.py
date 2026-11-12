"""サマリーシート(要件定義書 A-40〜A-45、O-07)。架空データは synth_classified.make() で組み立てる。

RUN_DATE = 2025-07-10 なので、直近6か月 = 2025-01〜2025-06。
"""
from __future__ import annotations

import zipfile

import openpyxl
import pytest

from anomaly.analysis.aspects import analyze_combo
from anomaly.analysis.findings import NO_DIFFERENCE, NOTABLE
from anomaly.analysis.prepare import build_combo
from anomaly.analysis.summary import (GROUP_ALL, GROUP_GE, GROUP_LT, OTHERS_ROW, PERIOD_ALL, PERIOD_RECENT,
                                      TOTAL_ROW, UNKNOWN_ROW, build_summary, usage_group)
from anomaly.analyze import analyze_records
from anomaly.settings import parse_settings

from .synth_classified import PATTERN, RUN_DATE, balanced, idx, make

RECENT = idx(2025, 3)       # 直近に入る受付月
BEFORE = idx(2024, 3)       # それ以前の受付月


class Gen:
    def __init__(self):
        self.n = 0
        self.recs: list[dict] = []

    def add(self, code, received, usage=None, sales="CUSA", **kw):
        self.n += 1
        mfg = None if usage is None else received - usage
        self.recs.append(make(self.n, code, received, sales=sales, mfg_idx=mfg, **kw))
        return self.recs[-1]

    def block(self, received, usage, times=1, sales=None):
        """PATTERN(27件。判定不能・分類不可を1件ずつ含む)を times 回。"""
        for _ in range(times):
            for code, s, _lang in PATTERN:
                self.add(code, received, usage, sales=sales or s)


def balanced_gen() -> Gen:
    """使用期間(5か月 / 30か月)× 期間(直近 / それ以前)のどれも同じ構成。"""
    g = Gen()
    for usage in (5, 30):
        for received in (RECENT, BEFORE):
            g.block(received, usage, times=2)
    return g


def summarize(codebook, recs, **settings):
    s = parse_settings(settings or None)
    combo = build_combo(recs[0]["model"], recs[0]["part_no"], recs, codebook, s.rare_code_min, s.keep_top_codes)
    ca = analyze_combo(combo, codebook, s, RUN_DATE)
    return build_summary(ca, s, RUN_DATE)


def _row(sm, key):
    return next(r for r in sm.rows if r.key == key)


# --- A-40: 群と期間 ---

def test_A40_usage_group_boundary_excludes_24():
    """24か月は「24か月以上」に入る(24か月未満 = 0〜23か月。ユーザ決定 2026-11-12)。"""
    def rec(m, st="ok"):
        return {"usage_months": m, "usage_status": st}
    assert usage_group(rec(0), 24) == GROUP_LT and usage_group(rec(23), 24) == GROUP_LT
    assert usage_group(rec(24), 24) == GROUP_GE and usage_group(rec(30), 24) == GROUP_GE
    assert usage_group(rec(100, "over_max"), 24) == GROUP_GE          # 96か月超は「以上」に入れる
    assert usage_group(rec(-1, "negative"), 24) is None               # 日付矛盾
    assert usage_group({"usage_months": None, "usage_status": "no_mfg_ym"}, 24) is None
    assert usage_group({}, 24) is None                                # シリアル変換結果を結合していない


def test_A40_counts_by_group_and_period(codebook):
    g = Gen()
    for _ in range(3):
        g.add("M001", RECENT, 23)          # 未満・直近
    for _ in range(4):
        g.add("M001", BEFORE, 24)          # 以上・それ以前(24か月ちょうど)
    for _ in range(2):
        g.add("M005", RECENT, 100)         # 96か月超 → 以上・直近
    for _ in range(5):
        g.add("M005", BEFORE)              # 製造年月不明
    g.add("M005", RECENT, -2)              # 日付矛盾(受付が製造より前)
    g.add("M001", idx(2025, 7))            # 実行日の月(全期間には入れ、直近には入れない)。製造年月不明
    sm = summarize(codebook, g.recs)
    n = {k: c.n for k, c in sm.cells.items()}
    assert n[(GROUP_ALL, PERIOD_ALL)] == 16 and n[(GROUP_ALL, PERIOD_RECENT)] == 6
    assert n[(GROUP_LT, PERIOD_ALL)] == 3 and n[(GROUP_LT, PERIOD_RECENT)] == 3
    assert n[(GROUP_GE, PERIOD_ALL)] == 6 and n[(GROUP_GE, PERIOD_RECENT)] == 2
    assert (sm.n_no_mfg, sm.n_negative) == (6, 1)
    assert sm.mfg_known_pct == pytest.approx(56.2)
    assert (sm.recent_from, sm.recent_to) == ("2025-01", "2025-06")
    assert sm.group_names[GROUP_LT] == "製造後24か月未満" and sm.group_names[GROUP_GE] == "製造後24か月以上"
    assert any("実行日の月(2025-07)以降の受付 1 件" in t for t in sm.notes)


def test_A40_split_and_recent_months_from_settings(codebook):
    g = Gen()
    g.add("M001", idx(2025, 6), 11)
    g.add("M001", idx(2024, 12), 13)
    sm = summarize(codebook, g.recs, usage_split_months=12, recent_months=3)
    assert sm.cells[(GROUP_LT, PERIOD_ALL)].n == 1 and sm.cells[(GROUP_GE, PERIOD_ALL)].n == 1
    assert (sm.recent_from, sm.recent_to) == ("2025-04", "2025-06")
    assert sm.cells[(GROUP_ALL, PERIOD_RECENT)].n == 1
    assert sm.period_names[PERIOD_RECENT] == "直近3か月"


# --- A-41: 構成比の分母と行 ---

def test_A41_denominator_includes_unknown_excludes_failed(codebook):
    sm = summarize(codebook, balanced_gen().recs)
    cell = sm.cells[(GROUP_ALL, PERIOD_ALL)]
    assert cell.n == 8 * 27 and cell.n_failed == 8 and cell.base == 8 * 26
    unk = _row(sm, UNKNOWN_ROW)
    assert unk.counts[(GROUP_ALL, PERIOD_ALL)] == 8 and unk.pcts[(GROUP_ALL, PERIOD_ALL)] == pytest.approx(3.8)
    total = _row(sm, TOTAL_ROW)
    assert total.counts[(GROUP_ALL, PERIOD_ALL)] == cell.base and total.pcts[(GROUP_ALL, PERIOD_ALL)] == 100.0
    body = [r for r in sm.rows if r.key != TOTAL_ROW]
    for gp in sm.cells:
        assert sum(r.counts[gp] for r in body) == sm.cells[gp].base


def test_A41_top_n_and_others_row(codebook):
    sm = summarize(codebook, balanced_gen().recs, summary_top_n=2)
    keys = [r.key for r in sm.rows]
    assert keys == ["M001", "M005", OTHERS_ROW, UNKNOWN_ROW, TOTAL_ROW]
    others = _row(sm, OTHERS_ROW)
    assert others.name == "上記以外(3種類)"
    assert any(t.startswith("「上記以外」の内訳(全件・全期間): 画像不具合 40件、異音 24件、エラー 8件") for t in sm.notes)
    assert others.counts[(GROUP_ALL, PERIOD_ALL)] == 8 * (5 + 3 + 1)        # M008 + M009 + M002


def test_A41_others_counts_codes_inside_rare(codebook):
    """「その他(少数)」にまとめた種類は、中のコードの数で「○種類」と書く(1種類と数えない)。"""
    g = balanced_gen()
    for code in ("M003", "M007", "M016", "M017"):          # 1件ずつ(上位5種類より下なので「その他(少数)」に入る)
        g.add(code, BEFORE, 30)
    sm = summarize(codebook, g.recs, summary_top_n=2)
    others = _row(sm, OTHERS_ROW)
    assert others.name == "上記以外(7種類)"                 # M008・M009・M002 + まとめた4種類
    assert sm.others_count == 7
    note = next(t for t in sm.notes if t.startswith("「上記以外」の内訳"))
    assert "動作不良 1件" in note and "シャッター不具合 1件" in note


# --- 中立性: 差の無いデータで色が付かないこと(第11節) ---

def test_A42_A43_neutral_no_color_on_balanced(codebook):
    sm = summarize(codebook, balanced_gen().recs)
    assert sm.axis_a.judgement == NO_DIFFERENCE
    assert all(sm.axis_b[g].judgement == NO_DIFFERENCE for g in sm.axis_b)
    assert not any(r.notable_a or any(r.notable_b.values()) for r in sm.rows)
    assert not any("販社の構成" in t for t in sm.notes)
    assert not [f for f in sm.findings if f["観点"].startswith("サマリー")]


# --- 差を作り込んだデータで検出できること ---

def excess_gen() -> Gen:
    g = balanced_gen()
    for _ in range(40):
        g.add("M009", RECENT, 5)           # 24か月未満・直近にだけ M009 を足す
    return g


def test_A43_recent_excess_in_lt_group_colored(codebook):
    sm = summarize(codebook, excess_gen().recs)
    m9 = _row(sm, "M009")
    assert sm.axis_b[GROUP_LT].judgement == NOTABLE and m9.notable_b[GROUP_LT]
    assert not m9.notable_b[GROUP_GE]                          # 24か月以上には足していない
    assert sm.axis_b[GROUP_GE].judgement == NO_DIFFERENCE
    assert m9.notable_a                                         # 全期間でも 未満 の方が多い
    f = next(f for f in sm.findings if f["観点"] == "サマリー②(時期)" and "24か月未満" in f["比較"])
    assert f["故障の種類"] == m9.name
    assert f["差(ポイント)"] > 10 and f["p値(補正後)"] < 0.05


def test_A41_notable_row_shown_beyond_top_n(codebook):
    sm = summarize(codebook, excess_gen().recs, summary_top_n=1)
    keys = [r.key for r in sm.rows]
    assert keys[0] == "M001" and "M009" in keys                 # 上位1つに入らなくても、注目点なので出す
    assert any("注目点になった故障の種類も表に出しています" in t for t in sm.notes)


def test_A43_small_group_not_tested(codebook):
    g = balanced_gen()
    for _ in range(10):
        g.add("M009", idx(2025, 2), 50)          # 分母の最大は 全件・直近の 110件(2ブロック×2 の 100件 + 10件)
    sm = summarize(codebook, g.recs, min_group_n=120)
    assert all(sm.axis_b[k].judgement == "insufficient" for k in sm.axis_b)
    assert sm.small(GROUP_LT, PERIOD_RECENT)
    assert any("件数が 120 件未満の区分" in t for t in sm.notes)


def test_A45_sales_note(codebook):
    g = Gen()
    for usage in (5, 30):
        g.block(BEFORE, usage, times=2)
    g.block(RECENT, 5, times=2, sales="CCN")                     # 直近は CCN だけ
    g.block(RECENT, 30, times=2, sales="CCN")
    sm = summarize(codebook, g.recs)
    assert any("販社の構成が異なります" in t and "全件: 直近6か月 と それ以前" in t for t in sm.notes)


def test_A45_env_share(codebook):
    g = Gen()
    g.add("M001", RECENT, 5, env=[{"factor": "drop", "source": "user"}])
    g.add("M001", RECENT, 5)
    g.add("FAILED", RECENT, 5, env=[{"factor": "drop", "source": "user"}])   # 分類不可は分母にも数えない
    sm = summarize(codebook, g.recs)
    c = sm.cells[(GROUP_LT, PERIOD_RECENT)]
    assert (c.env_any, c.base) == (1, 2)


# --- O-07: シート ---

def test_O07_summary_sheet_first_with_five_charts(codebook, tmp_path):
    s = parse_settings(None)
    analyze_records(excess_gen().recs, codebook, s, RUN_DATE, tmp_path)
    path = tmp_path / "combos" / "X100__P-001" / "tables.xlsx"
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames[0] == "サマリー" and wb.sheetnames[1] == "前提"
    ws = wb["サマリー"]
    assert ws["A1"].value == "X100 × P-001 修理傾向サマリー"
    texts = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)]
    assert any(t.startswith("① 使用期間で比べる") for t in texts) and any(t.startswith("② 時期で比べる") for t in texts)
    assert any("製造後24か月未満" in t for t in texts) and "注目点の一覧(すべての観点。決まった条件 A-14 による)" in texts
    assert any(t.startswith("上記以外") for t in texts) or len([t for t in texts if t == "判定不能"]) >= 2
    with zipfile.ZipFile(path) as z:
        charts = [n for n in z.namelist() if n.startswith("xl/charts/chart")]
    assert len(charts) == 5                                 # サマリーの3つ + ロットの2つ(A-46)
    assert any(t.startswith("③ 製造ロットで比べる") for t in texts)


# --- A-46: 製造ロット(製造年月)ごとの表 ---

def test_A46_months_continuous_and_small_lots_blank(codebook):
    g = Gen()
    for _ in range(6):
        g.add("M001", BEFORE, 20)                  # 製造 2022-07(6件)
    for _ in range(2):
        g.add("M005", BEFORE, 18)                  # 製造 2022-09(2件)
    g.add("FAILED", BEFORE, 18)                    # 分類不可は数えない
    g.add("M001", BEFORE)                          # 製造年月不明
    lc = summarize(codebook, g.recs).lot_chart
    assert lc.axis == "製造年月"
    assert [r.label for r in lc.rows] == ["2022-07", "2022-08", "2022-09"]   # 修理の無い月も並べる
    r7, r8, r9 = lc.rows
    assert (r7.total, r8.total, r9.total) == (6, 0, 2)
    assert r7.pcts["M001"] == 100.0 and r9.pcts["M005"] is None              # 5件未満は構成比を空欄
    assert (lc.n_outside, lc.n_failed) == (1, 1)
    assert [name for _, name in lc.series][-2:] == ["その他", "判定不能"]


def test_A46_top5_series_and_other(codebook):
    g = Gen()
    for code, k in (("M001", 6), ("M005", 5), ("M008", 4), ("M009", 3), ("M002", 2), ("M003", 1), ("M007", 1)):
        for _ in range(k):
            g.add(code, BEFORE, 20)
    lc = summarize(codebook, g.recs).lot_chart
    assert [k for k, _ in lc.series][:5] == ["M001", "M005", "M008", "M009", "M002"]
    assert lc.rows[0].counts["__LOT_OTHER__"] == 2                           # 6位以下はその他


def test_A46_star_only_on_notable_lot(codebook):
    neutral = summarize(codebook, balanced()).lot_chart
    assert not any(r.star for r in neutral.rows)                              # 差の無いデータでは付かない
    recs = balanced()
    lot = idx(2022, 5)
    for i in range(40):
        recs.append(make(90000 + i, "M009", lot + 6, mfg_idx=lot))            # 2022-05 製造のロットに M009 を足す
    lc = summarize(codebook, recs).lot_chart
    starred = [r.label for r in lc.rows if r.star]
    assert "2022-05★" in starred
    assert all(r.label.startswith(("2022-04", "2022-05", "2022-06")) for r in lc.rows if r.star)  # 1ロット or まとまり


def test_A46_prefix_only_uses_lot_code(codebook):
    g = Gen()
    for code in ("03", "01", "02"):
        for _ in range(5):
            g.add("M001", BEFORE, lot_code=code)
    lc = summarize(codebook, g.recs).lot_chart
    assert lc.axis == "ロット(頭桁)" and [r.label for r in lc.rows] == ["01", "02", "03"]


def test_A46_no_lot_info(codebook):
    g = Gen()
    g.add("M001", BEFORE)
    assert summarize(codebook, g.recs).lot_chart is None
