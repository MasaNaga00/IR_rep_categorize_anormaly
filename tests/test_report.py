"""定型文のレポートと代表事例(要件定義書 T-01、T-02 層1、T-04、T-06〜T-08、A-14b)。"""
from __future__ import annotations

import pytest

from anomaly.analysis.aspects import analyze_combo
from anomaly.analysis.findings import INSUFFICIENT, NOTABLE
from anomaly.analysis.prepare import build_combo
from anomaly.analyze import findings_json, staleness_warning
from anomaly.report.examples import pick_examples
from anomaly.report.markdown import all_examples, build_report
from anomaly.report.templates import FORBIDDEN_WORDS, comparison_sentences

from .synth_classified import RUN_DATE, balanced, idx, make, with_recent_excess

INFO = {"run_date": RUN_DATE.isoformat(), "serial_result": "x.csv", "internal_excluded": ["CMJ"], "warnings": []}


def _ca(codebook, settings, recs):
    c = build_combo(recs[0]["model"], recs[0]["part_no"], recs, codebook, settings.rare_code_min)
    return analyze_combo(c, codebook, settings, RUN_DATE)


@pytest.fixture(scope="module")
def bal(codebook, settings):
    return _ca(codebook, settings, balanced())


@pytest.fixture(scope="module")
def exc(codebook, settings):
    return _ca(codebook, settings, with_recent_excess("M009", 60))


def test_T02_no_difference_report(bal, settings):
    md = build_report(bal, settings, INFO)
    assert md.startswith("# 修理傾向の解析: X100 × P-001")
    assert "故障の種類の構成に目立った差は見られません" in md
    assert "目立った偏りは見られません" in md                 # ロット
    assert "まとまり(製造の順で隣り合う 3 ロットずつ) 4 個" in md
    assert "注目点が無いため、代表事例はありません" in md
    assert "ほかの故障の割合が低くなります" not in md
    assert "注目点あり" not in md


def test_T07_prerequisites(bal, settings):
    md = build_report(bal, settings, INFO)
    for text in ("修理件数: 1296 件(分類できた 1200 件 / 判定不能 48 件 / 分類不可 48 件)",
                 "受付日の期間: 2022-02-15 〜 2025-06-15(実行日 2025-07-10)",
                 "製造年月が求められた修理: 1296 件(100.0%)",
                 "内部コメントを分類に使っていない販社: CMJ",
                 "故障率ではありません", "正解データでは検証していません", "判定不能・分類不可を除いた件数"):
        assert text in md, text


def test_T02_notable_report(exc, settings):
    md = build_report(exc, settings, INFO)
    assert "『異音』の割合が" in md and "ポイント高く" in md
    assert "### 最近の修理とそれ以前の比較: 『異音』(直近6か月(2025-01〜2025-06))" in md
    assert "訳: 異音がする" in md or "(抜き出しなし)" not in md
    assert "| 製造ロット |" in md
    assert "『電源不良』(それ以前" not in md                  # 比べた群で低い注目点には事例を出さない
    assert "ほかの故障の割合が低くなります" in md                # 構成比の注記


def test_T04_forbidden_words_not_used(bal, exc, settings, codebook):
    small = _ca(codebook, settings, [make(i, "M001", idx(2025, 3)) for i in range(5)])
    for ca in (bal, exc, small):
        md = build_report(ca, settings, INFO)
        for w in FORBIDDEN_WORDS:
            assert w not in md, w


def test_T02_insufficient_sentence(codebook, settings):
    ca = _ca(codebook, settings, [make(i, "M001", idx(2025, 3)) for i in range(12)] + [make(100, "M001", idx(2024, 3))])
    comp = ca.recent.comparisons[0]
    assert comp.judgement == INSUFFICIENT
    text = comparison_sentences(comp, settings)[0]
    assert "直近6か月(2025-01〜2025-06)が 12 件" in text and "それ以前(〜2024-12)が 1 件" in text
    assert "比較による判断はできません" in text


def test_T02_no_mfg_report(codebook, settings):
    md = build_report(_ca(codebook, settings, balanced(with_mfg=False)), settings,
                      {**INFO, "serial_result": None})
    assert "使用期間の解析はできません" in md and "ロットの解析はできません" in md
    assert "シリアルの変換結果は指定されていません" in md


def test_A15a_spike_caveat_in_report(codebook, settings):
    recs, n = [], 0
    for lot, count in zip(range(idx(2023, 1), idx(2023, 8)), (20, 20, 20, 80, 20, 20, 20)):
        for i in range(count):
            recs.append(make(n, ("M001", "M005", "M008", "M009")[i % 4], lot + 5, mfg_idx=lot))
            n += 1
    md = build_report(_ca(codebook, settings, recs), settings, INFO)
    assert "生産台数のデータがないため、件数の偏りが生産量によるものかは判断できません" in md


def test_T08_examples_deterministic(exc, settings):
    comp = exc.recent.comparisons[0]
    f = comp.findings[0]
    a = pick_examples(comp, f, 3, 42, exc.combo.key)
    b = pick_examples(comp, f, 3, 42, exc.combo.key)
    assert a == b and len(a) == 3
    assert all(e["excerpt"] == "이상한 소리가 남" or e["excerpt"].startswith("text") for e in a)
    assert all(e["excerpt_ja"] == "異音がする" for e in a if e["excerpt"] == "이상한 소리가 남")
    c = pick_examples(comp, f, 3, 7, exc.combo.key)
    assert {e["rec_key"] for e in c} != {e["rec_key"] for e in a} or len(comp.members["a"]) <= 3
    rows = all_examples(exc, settings)
    assert rows and rows[0]["観点"] and rows[0]["日本語訳"] is not None


def test_A14b_findings_json(exc, settings):
    j = findings_json(exc, settings, INFO)
    assert j["model"] == "X100" and j["n_findings"] == exc.n_findings() > 0
    assert j["aspects"]["recent"] == NOTABLE
    rc = next(c for c in j["comparisons"] if c["aspect"] == "recent")
    f = rc["findings"][0]
    assert set(f) == {"code", "name", "a_count", "a_n", "a_pct", "b_count", "b_n", "b_pct", "diff_pt", "ratio", "p_adj"}
    assert rc["group_a"]["n"] == f["a_n"]
    assert j["criteria"]["min_group_n"] == 30


def test_A07_staleness_warning(settings):
    recs = [make(1, "M001", idx(2025, 3))]
    from datetime import date
    assert staleness_warning(recs, settings, date(2025, 7, 10))          # 最新 2025-03 < 2025-06
    assert not staleness_warning([make(1, "M001", idx(2025, 6))], settings, date(2025, 7, 10))
    assert staleness_warning([], settings, date(2025, 7, 10)) == []


# --- 全体の偏り(A-05a) ---

def test_A05a_concentration_balanced(bal, settings):
    c = bal.overall.facts["concentration"]
    assert c["n_valid"] == 1200 and c["n_codes"] == 5
    assert c["top1"]["code"] == "M001" and c["top1"]["pct"] == 40.0
    assert c["top3_pct"] == 84.0 and c["codes_for_80"] == 3
    md = build_report(bal, settings, INFO)
    assert "最も多い『電源不良』が全体の 40.0%、上位3つで 84.0% を占めます。" in md
    assert "全体の80%は 3 種類の故障で占められます(故障の種類は全部で 5 種類)。" in md
    assert "集中しています" not in md and "偏りは小さい" not in md      # 30%〜50% は判断の言葉を付けない
    rows = {r["指標"]: r["値"] for r in bal.overall.tables["偏りの指標"]}
    assert rows["全体の80%になるまでの種類の数"] == 3


def test_A05a_concentration_high_and_low(codebook, settings):
    high = [make(i, "M007", idx(2025, 3)) for i in range(31)] + [make(100 + i, "M001", idx(2025, 3)) for i in range(9)]
    md = build_report(_ca(codebook, settings, high), settings, INFO)
    assert "特定の故障の種類(『シャッター不具合』)に修理が集中しています。" in md
    codes = ["M001", "M002", "M003", "M004", "M005", "M007"]
    low = [make(i, codes[i % 6], idx(2025, 3)) for i in range(60)]
    md2 = build_report(_ca(codebook, settings, low), settings, INFO)
    assert "特定の故障の種類への偏りは小さいです。" in md2 and "全体の80%は 5 種類" in md2


def test_A05a_concentration_small_n_and_empty(codebook, settings):
    small = [make(i, "M007", idx(2025, 3)) for i in range(5)]
    md = build_report(_ca(codebook, settings, small), settings, INFO)
    assert "5 件と少ないため、割合は参考値です" in md
    none_valid = [make(i, "UNK", idx(2025, 3)) for i in range(5)]
    ca = _ca(codebook, settings, none_valid)
    assert ca.overall.facts["concentration"]["top1"] is None
    assert "分類できた修理がないため" in build_report(ca, settings, INFO)


def test_A05a_concentration_in_findings_json(bal, settings):
    j = findings_json(bal, settings, INFO)
    assert j["concentration"]["top1"]["name"] == "電源不良"
