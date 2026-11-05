"""2群の比較と補正(要件定義書 A-04、A-09)。"""
from __future__ import annotations

import pytest

from anomaly.analysis.prepare import RARE_CODE, build_combo, is_failed, is_unknown
from anomaly.analysis.stats import bh_adjust, chi2_test, compare, fisher_p

from .synth_classified import make


def test_A09_bh_adjust_known_values():
    # R: p.adjust(c(0.01, 0.04, 0.03, 0.2), "BH") = 0.04 0.0533 0.0533 0.2
    adj = bh_adjust([0.01, 0.04, 0.03, 0.2])
    assert adj == pytest.approx([0.04, 0.0533333, 0.0533333, 0.2], rel=1e-4)
    assert bh_adjust([0.5, None, 0.01]) == pytest.approx([0.5, None, 0.02])
    assert bh_adjust([]) == []
    assert bh_adjust([0.9, 0.95]) == pytest.approx([0.95, 0.95])   # 1 を超えない・単調


def test_A09_fisher():
    assert fisher_p(5, 10, 5, 10) == pytest.approx(1.0)
    assert fisher_p(10, 10, 0, 10) < 0.001
    assert fisher_p(1, 0, 1, 10) is None


def test_A09_compare_identical_groups():
    cats = ["A"] * 20 + ["B"] * 10
    r = compare(cats, list(cats), ["A", "B"])
    assert r.chi2_p == pytest.approx(1.0)
    assert all(c.p_adj == pytest.approx(1.0) and c.diff_pt == 0 for c in r.categories)


def test_A09_compare_values():
    r = compare(["A"] * 30 + ["B"] * 10, ["A"] * 10 + ["B"] * 30, ["A", "B"])
    a = r.by_code()["A"]
    assert (a.a, a.na, a.b, a.nb) == (30, 40, 10, 40)
    assert a.pct_a == 75 and a.pct_b == 25 and a.diff_pt == 50 and a.ratio == 3
    assert a.p_adj < 0.001 and r.chi2_p < 0.001
    assert r.chi2_reference is False


def test_A09_chi2_reference_flag():
    p, ref = chi2_test([[10, 1, 1, 1], [10, 1, 0, 1]])
    assert p is not None and ref is True


def test_A09_chi2_not_possible():
    assert chi2_test([[5, 0], [3, 0]]) == (None, False)        # カテゴリが1つ
    assert chi2_test([[5, 5], [0, 0]]) == (None, False)        # 片方の群が空
    r = compare([], ["A"], ["A"])
    assert r.chi2_p is None and r.categories[0].p_raw is None


def test_A09_ratio_none_when_b_zero():
    r = compare(["A"] * 5, ["B"] * 5, ["A", "B"])
    assert r.by_code()["A"].ratio is None


def test_A04_rare_codes_merged(codebook):
    recs = ([make(i, "M001", 24300) for i in range(10)] + [make(100 + i, "M005", 24300) for i in range(5)]
            + [make(200 + i, "M008", 24300) for i in range(4)] + [make(300, "M009", 24300)])
    c = build_combo("X100", "P-001", recs, codebook, rare_code_min=5)
    assert c.categories == ["M001", "M005", RARE_CODE]
    assert c.category_of["M008"] == RARE_CODE and c.category_of["M009"] == RARE_CODE
    assert c.raw_counts["M008"] == 4                          # まとめる前の件数は残す
    assert c.name(RARE_CODE) == "その他(少数)"


def test_G20_unknown_and_failed(codebook):
    recs = [make(1, "M001", 24300), make(2, "UNK", 24300), make(3, "FAILED", 24300)]
    weird = make(4, "M001", 24300)
    weird["primary_code"] = "M_UNK"                           # info_level が thin でも、判定不能コードなら判定不能
    weird["info_level"] = "thin"
    recs.append(weird)
    assert is_failed(recs[2]) and not is_failed(recs[1])
    assert is_unknown(recs[1], "M_UNK") and is_unknown(weird, "M_UNK") and not is_unknown(recs[2], "M_UNK")
    c = build_combo("X100", "P-001", recs, codebook, 1)
    assert (len(c.valid), len(c.unknown), len(c.failed)) == (1, 2, 1)
