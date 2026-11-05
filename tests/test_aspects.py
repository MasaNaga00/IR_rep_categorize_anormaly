"""4つの観点の集計表と比較(要件定義書 A-05〜A-24)と、注目点の判定(A-14)。"""
from __future__ import annotations

from datetime import date

import pytest

from anomaly.analysis.aspects import analyze_combo, bin_labels, bin_of, lots, overall, recent, usage
from anomaly.analysis.findings import INSUFFICIENT, NO_DIFFERENCE, NOT_AVAILABLE, NOTABLE
from anomaly.analysis.prepare import build_combo, split_by_combo
from anomaly.settings import parse_settings

from .synth_classified import RUN_DATE, balanced, idx, make, with_recent_excess


def _combo(codebook, recs, s=None):
    s = s or parse_settings(None)
    return build_combo(recs[0]["model"], recs[0]["part_no"], recs, codebook, s.rare_code_min)


@pytest.fixture(scope="module")
def bal(codebook, settings):
    recs = balanced()
    return analyze_combo(_combo(codebook, recs), codebook, settings, RUN_DATE)


# --- 中立性: 差が無いデータで注目点が出ないこと(第11節) ---

def test_neutral_no_findings_on_balanced_data(bal):
    assert len(bal.combo.records) == 12 * 4 * 27
    assert bal.recent.judgement == NO_DIFFERENCE
    assert bal.usage.judgement == NO_DIFFERENCE
    assert bal.lot.judgement == NO_DIFFERENCE
    assert bal.n_findings() == 0
    assert bal.lot.facts["spikes"] == []              # どのロットも同じ件数
    for c in bal.comparisons():
        assert all(cr.p_adj == pytest.approx(1.0) for cr in c.result.categories)


def test_neutral_no_notes_on_balanced_data(bal):
    """販社の構成も同じため、販社の注記(A-23)も付かない。"""
    assert all(not any("販社の構成" in n for n in c.notes) for c in bal.comparisons())


# --- 差を作り込んだデータで検出できること ---

@pytest.fixture(scope="module")
def excess(codebook, settings):
    recs = with_recent_excess("M009", 60)
    return analyze_combo(_combo(codebook, recs), codebook, settings, RUN_DATE)


def test_A14_recent_excess_detected(excess):
    comp = excess.recent.comparisons[0]
    assert comp.judgement == NOTABLE
    f = comp.findings[0]
    assert f.code == "M009" and f.diff_pt > 0 and f.higher == "a"
    assert f.p_adj < 0.05 and abs(f.diff_pt) >= 10
    assert all(x.code != "OTHER_RARE" for x in comp.findings)


def test_A14_new_lot_detected(excess):
    notable = [c for c in excess.lot.comparisons if c.judgement == NOTABLE and c.lot_kind == "single"]
    assert [c.lot_code for c in notable] == [f"{idx(2025, 1) % 96 + 1:02d}"]   # 2025-01 のロット(架空の頭桁)
    assert notable[0].findings[0].code == "M009"
    assert "2025-01" in notable[0].group_a


def test_A14_usage_early_below_threshold(excess, codebook):
    """使用期間12か月以内の M009 は 20.0%、それ以降は 12.0% で差は 8 ポイント。閾値(10)未満なので差なし。"""
    comp = excess.usage.comparisons[0]
    m9 = comp.result.by_code()["M009"]
    assert round(m9.pct_a, 1) == 20.0 and round(m9.pct_b, 1) == 12.0
    assert comp.judgement == NO_DIFFERENCE
    s = parse_settings({"min_diff_pt": 5})                     # 閾値を下げれば注目点になる
    ca = analyze_combo(_combo(codebook, with_recent_excess("M009", 60), s), codebook, s, RUN_DATE)
    f = ca.usage.comparisons[0].findings[0]
    assert (f.code, f.higher) == ("M009", "a")


def test_A14_insufficient(codebook, settings):
    recs = [make(i, "M001", idx(2025, 3)) for i in range(20)] + [make(100 + i, "M005", idx(2024, 3)) for i in range(20)]
    ca = analyze_combo(_combo(codebook, recs), codebook, settings, RUN_DATE)
    assert ca.recent.judgement == INSUFFICIENT and ca.recent.comparisons[0].findings == []


def test_A14_thresholds_respected(codebook):
    """差の大きさ・件数の条件を満たさないものは、p 値が小さくても注目点にしない。"""
    recs = with_recent_excess("M009", 60)
    s = parse_settings({"min_diff_pt": 60})
    ca = analyze_combo(_combo(codebook, recs, s), codebook, s, RUN_DATE)
    assert ca.recent.judgement == NO_DIFFERENCE
    s2 = parse_settings({"min_code_n": 100})                   # 直近の M009 は 78 件なので注目点にならない
    ca2 = analyze_combo(_combo(codebook, recs, s2), codebook, s2, RUN_DATE)
    assert "M009" not in {f.code for f in ca2.recent.comparisons[0].findings}
    # M009 が多い分、他の故障の割合は直近で低くなる(それ以前の側の件数が多いため、こちらは注目点になりうる)
    assert all(f.higher == "b" for f in ca2.recent.comparisons[0].findings)


# --- 観点1: 全体(A-05, A-06, A-19) ---

def test_A05_overall_tables(bal, codebook):
    t = bal.overall.tables
    rows = {r["コード"]: r for r in t["全体"]}
    n_valid = 12 * 4 * 25
    assert rows["M001"]["件数"] == 12 * 4 * 10 and rows["M001"]["割合(%)"] == 40.0
    assert rows["M_UNK"]["件数"] == 48 and rows["M_UNK"]["割合(%)"] is None
    assert t["全体"][-1]["名称"] == "分類不可" and t["全体"][-1]["件数"] == 48
    assert bal.overall.facts["n_valid"] == n_valid
    assert [x[0] for x in bal.overall.facts["top"]] == ["M001", "M005", "M008"]
    assert {r["販社"] for r in t["販社別"]} == {"CUSA", "CCN", "CEU", "CMJ", "CKR"}
    assert t["再修理の可能性"] == []


def test_A05_environment_and_repeat_serial(codebook):
    env = [{"factor": "impact", "source": "user", "abnormal_use": False, "excerpt": "dropped"}]
    recs = [make(1, "M001", 24300, env=env, serial="AAA"), make(2, "M001", 24301, serial="AAA"),
            make(3, "M005", 24302, env=env + [{"factor": "water", "source": "repair", "abnormal_use": False, "excerpt": "wet"}])]
    ar = overall(_combo(codebook, recs), codebook)
    env_rows = {r["要因"]: r for r in ar.tables["環境要因"]}
    assert env_rows["(いずれかの記述あり)"]["件数"] == 2 and env_rows["impact"]["件数"] == 2 and env_rows["water"]["件数"] == 1
    assert env_rows["impact"]["名称"]
    assert ar.tables["再修理の可能性"] == [{"シリアル": "AAA", "件数": 2, "rec_key": "000001;000002"}]


# --- 観点2: 期間(A-07, A-08, A-10) ---

def test_A07_recent_window_excludes_current_month(codebook, settings):
    recs = [make(1, "M001", idx(2025, 7)), make(2, "M001", idx(2025, 6)), make(3, "M001", idx(2025, 1)),
            make(4, "M001", idx(2024, 12))]
    ar = recent(_combo(codebook, recs), settings, date(2025, 7, 10))
    comp = ar.comparisons[0]
    assert comp.group_a == "直近6か月(2025-01〜2025-06)" and comp.group_b == "それ以前(〜2024-12)"
    assert (comp.na, comp.nb) == (2, 1)
    assert any("2025-07" in n and "1 件" in n for n in comp.notes)


def test_A08_same_length(codebook):
    s = parse_settings({"recent_compare": "same_length", "recent_months": 3})
    recs = [make(1, "M001", idx(2025, 6)), make(2, "M001", idx(2025, 3)), make(3, "M001", idx(2025, 1)),
            make(4, "M001", idx(2024, 12))]
    comp = recent(_combo(codebook, recs, s), s, date(2025, 7, 1)).comparisons[0]
    assert comp.group_a == "直近3か月(2025-04〜2025-06)" and comp.group_b == "その前の3か月(2025-01〜2025-03)"
    assert (comp.na, comp.nb) == (1, 2)
    assert any("前の受付 1 件" in n for n in comp.notes)


def test_A10_monthly_table(bal):
    rows = bal.recent.tables["月別推移"]
    totals = [r for r in rows if r["名称"] == "修理件数(合計)"]
    assert sum(r["件数"] for r in totals) == len(bal.combo.records)
    assert totals == sorted(totals, key=lambda r: r["年月"])


def test_A23_sales_note(codebook, settings):
    recs = ([make(i, "M001", idx(2025, 3), sales="CUSA") for i in range(40)]
            + [make(100 + i, "M001", idx(2024, 3), sales="CEU") for i in range(40)])
    comp = recent(_combo(codebook, recs), settings, RUN_DATE).comparisons[0]
    assert comp.judgement == NO_DIFFERENCE
    assert any("販社の構成が異なります" in n for n in comp.notes)
    assert comp.profiles["a"]["sales"] == {"CUSA": 100.0}


# --- 観点3: 使用期間(A-11〜A-13c) ---

def test_A11_bins():
    bins = (0, 7, 13, 25, 37)
    assert bin_labels(bins) == ["0〜6か月", "7〜12か月", "13〜24か月", "25〜36か月", "37か月以上"]
    assert [bin_of(m, bins) for m in (0, 6, 7, 12, 13, 36, 37, 90)] == [0, 0, 1, 1, 2, 3, 4, 4]


def test_A12_usage_tables(bal):
    t = bal.usage.tables
    dist = {r["区切り"]: r["件数"] for r in t["使用期間_分布"]}
    per = 12 * 27
    assert dist == {"0〜6か月": 2 * per, "7〜12か月": 0, "13〜24か月": per, "25〜36か月": per, "37か月以上": 0}
    m1 = next(r for r in t["使用期間_コード別"] if r["コード"] == "M001")
    assert m1["中央値(か月)"] == 10.0 and m1["件数"] == 480
    assert bal.usage.facts["median"] == 10.0
    assert bal.usage.comparisons[0].group_a == "使用期間12か月以内"


def test_A13c_usage_not_available(codebook, settings):
    recs = balanced(with_mfg=False)
    ca = analyze_combo(_combo(codebook, recs), codebook, settings, RUN_DATE)
    assert ca.usage.judgement == NOT_AVAILABLE and ca.usage.comparisons == []
    assert ca.lot.judgement == NOT_AVAILABLE
    assert ca.recent.judgement == NO_DIFFERENCE


# --- 観点4: ロット(A-15〜A-15c, A-24, S-18) ---

def test_A15_lot_table_order_and_names(bal):
    rows = bal.lot.tables["ロット"]
    assert len(rows) == 12
    assert [r["製造年月"] for r in rows] == [f"2022-{m:02d}" for m in range(1, 13)]
    assert rows[0]["ロット"] == f"2022-01(頭桁 {idx(2022, 1) % 96 + 1:02d})"
    assert all(r["検定の対象"] == "○" for r in rows)
    assert any("古いロットほど" in n for n in bal.lot.notes)


def test_A15a_spike_is_reference_only(codebook, settings):
    recs = []
    n = 0
    for lot, count in zip(range(idx(2023, 1), idx(2023, 8)), (20, 20, 20, 80, 20, 20, 20)):
        for i in range(count):
            recs.append(make(n, ("M001", "M005", "M008", "M009")[i % 4], lot + 5, mfg_idx=lot))
            n += 1
    ar = lots(_combo(codebook, recs), settings)
    assert ar.facts["spikes"] == [(f"2023-04(頭桁 {idx(2023, 4) % 96 + 1:02d})", 80)]
    assert ar.judgement == NO_DIFFERENCE                 # 件数の偏りは注目点にしない
    assert next(r for r in ar.tables["ロット"] if r["件数"] == 80)["件数の偏り(参考)"] == "参考"


def test_A15b_prefix_only_lots(codebook, settings):
    recs = []
    n = 0
    for lc in ("10", "11", "12"):
        for i in range(40):
            recs.append(make(n, ("M001", "M005")[i % 2], idx(2025, 3), lot_code=lc))
            n += 1
    ar = lots(_combo(codebook, recs), settings)
    assert [r["ロット"] for r in ar.tables["ロット"]] == ["頭桁 10", "頭桁 11", "頭桁 12"]
    assert ar.facts["prefix_only"] is True
    assert any("周期" in n for n in ar.notes)
    assert ar.judgement == NO_DIFFERENCE


def test_A15c_small_lots_not_tested(codebook, settings):
    recs = [make(i, "M001", idx(2025, 3), mfg_idx=idx(2024, 1)) for i in range(4)]
    recs += [make(100 + i, "M001", idx(2025, 3), mfg_idx=idx(2024, 2)) for i in range(40)]
    ar = lots(_combo(codebook, recs), settings)
    singles = [c for c in ar.comparisons if c.lot_kind == "single"]
    assert len(singles) == 1                               # 4件のロットは検定しない(lot_min_n = 5)
    assert singles[0].judgement == INSUFFICIENT            # 他のロット(4件)が min_group_n 未満


def _sparse_lots(codebook, s, defect_lots=(), n_per_lot=3, months=24):
    """1ロット3件の少ないロット。defect_lots のロットは、すべて M009。"""
    recs, n = [], 0
    for j, lot in enumerate(range(idx(2023, 1), idx(2023, 1) + months)):
        for i in range(n_per_lot):
            code = "M009" if j in defect_lots else ("M001", "M005", "M008")[(n) % 3]
            recs.append(make(n, code, lot + 6, mfg_idx=lot))
            n += 1
    return lots(_combo(codebook, recs, s), s)


def test_A15d_windows_detect_consecutive_sparse_lots(codebook, settings):
    """1ロット3件では1ロットごとの比較はできないが、隣り合う3ロットのまとまりなら、続けて出ている偏りを拾える。"""
    ar = _sparse_lots(codebook, settings, defect_lots=(3, 4, 5))
    assert [c for c in ar.comparisons if c.lot_kind == "single"] == []
    windows = [c for c in ar.comparisons if c.lot_kind == "window"]
    assert len(windows) == 8
    assert [c.group_a for c in windows[:3]] == ["ロット 2023-01〜2023-03", "ロット 2023-04〜2023-06", "ロット 2023-07〜2023-09"]
    assert windows[-1].group_a == "ロット 2024-10〜2024-12"
    notable = [c for c in windows if c.judgement == NOTABLE]
    assert [c.group_a for c in notable] == ["ロット 2023-04〜2023-06"]
    assert notable[0].findings[0].code == "M009" and notable[0].findings[0].a == 9
    rows = ar.tables["ロット_まとめ"]
    assert rows[1]["ロットの数"] == 3 and rows[1]["件数"] == 9 and rows[1]["検定の対象"] == "○"


def test_A15d_windows_neutral(codebook, settings):
    ar = _sparse_lots(codebook, settings)
    assert ar.judgement == NO_DIFFERENCE and all(not c.findings for c in ar.comparisons)


def test_A15d_window_disabled(codebook):
    s = parse_settings({"lot_window": 1})
    ar = _sparse_lots(codebook, s, defect_lots=(3, 4, 5))
    assert ar.comparisons == [] and ar.judgement == INSUFFICIENT and ar.tables["ロット_まとめ"] == []


def test_A15d_window_with_one_lot_skipped(codebook, settings):
    """修理のある月が離れていて、まとまりにロットが1つしかない場合は比べない(1ロットの比較と同じになるため)。"""
    recs = [make(i, "M001", idx(2024, 1), mfg_idx=idx(2023, 1)) for i in range(10)]
    recs += [make(100 + i, "M005", idx(2024, 9), mfg_idx=idx(2023, 9)) for i in range(40)]
    ar = lots(_combo(codebook, recs), settings)
    assert [c.lot_kind for c in ar.comparisons] == ["single", "single"]
    assert [r["ロットの数"] for r in ar.tables["ロット_まとめ"]] == [1, 1]


def test_A15d_prefix_only_window_names(codebook, settings):
    recs, n = [], 0
    for lc in ("10", "11", "12", "13"):
        for i in range(10):
            recs.append(make(n, ("M001", "M005")[i % 2], idx(2025, 3), lot_code=lc))
            n += 1
    ar = lots(_combo(codebook, recs), settings)
    assert [r["まとまり"] for r in ar.tables["ロット_まとめ"]] == ["頭桁 10〜12", "頭桁 13〜15"]


def test_A15a_lot_bh_pooled_across_lots(codebook, settings):
    """ロット×コードの補正は全ロットをまとめて行う(偶然の差を拾いにくくする)。"""
    recs = with_recent_excess("M009", 60)
    ca = analyze_combo(_combo(codebook, recs), codebook, settings, RUN_DATE)
    raw = [cr.p_raw for c in ca.lot.comparisons for cr in c.result.categories]
    adj = [cr.p_adj for c in ca.lot.comparisons for cr in c.result.categories]
    m = len(raw)
    assert m > len(ca.lot.comparisons[0].result.categories)
    i = raw.index(min(raw))
    assert adj[i] == pytest.approx(min(1.0, min(raw) * m), rel=1e-9) or adj[i] >= min(raw)


def test_A01_split_by_combo(codebook, settings):
    recs = balanced() + [make(90000 + i, "L001", idx(2025, 3), model="L200", part="P-777", product="LENS") for i in range(5)]
    combos = split_by_combo(recs, codebook, settings.rare_code_min)
    assert [c.key for c in combos] == [("X100", "P-001"), ("L200", "P-777")]
    assert combos[1].unknown_code == "L_UNK"


def test_exclusions_table(bal):
    rows = {(r["区分"], r["状態"]): r["件数"] for r in bal.exclusions}
    assert rows[("分類", "判定不能")] == 48 and rows[("分類", "分類不可")] == 48
    assert rows[("製造年月", "ok")] == len(bal.combo.records)
