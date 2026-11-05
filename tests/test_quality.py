"""品質確認(要件定義書 G-14、G-15、G-30)。"""
from __future__ import annotations

import json

import openpyxl
import pytest

from anomaly.classify.cache import NullCache
from anomaly.classify.runner import classify_records
from anomaly.classify_cmd import main as classify_main
from anomaly.input_loader import load_input
from anomaly.local_config import init_local
from anomaly.quality.compare_runs import compare, main as compare_main
from anomaly.quality.evaluate import NameConverter, evaluate, main as evaluate_main, normalize_name
from anomaly.quality.sample_check import language_metrics, sample_records, sample_rows
from anomaly.settings import parse_settings

from .conftest import CODEBOOK_TEST_YAML, SYNTH, read_csv, write_csv
from .fake_dify import FakeDify

CB = ["--codebook", str(CODEBOOK_TEST_YAML)]


@pytest.fixture
def classified(codebook, settings):
    recs = load_input(SYNTH / "repairs.csv", codebook.product_ids()).records
    classify_records(recs, codebook, settings, FakeDify(), NullCache())
    return recs


def _many(codebook, n_per_lang=30):
    recs = []
    for i in range(n_per_lang):
        for j, (lang, text) in enumerate((("en", "no power"), ("zh", "无法开机"), ("ko", "전원"), ("ja", "電源"))):
            recs.append({"rec_key": f"{i * 4 + j:06d}", "lang": lang, "product_type": "ML", "repair_id": f"R{i}{j}",
                         "sales_company": "CUSA", "classify_status": "ok", "info_level": "sufficient", "primary_code": "M001",
                         "failures": [{"code": "M001", "source": "user", "excerpt": text, "excerpt_ja": "電源"}],
                         "user_comment": text})
    return recs


# --- 抜き取り確認(G-14) ---

def test_G14_sample_per_language_and_deterministic(codebook):
    recs = _many(codebook)
    s = parse_settings({"sample_per_language": 5})
    a = sample_records(recs, s)
    assert [r["lang"] for r in a] == ["ja"] * 5 + ["en"] * 5 + ["zh"] * 5 + ["ko"] * 5
    assert [r["rec_key"] for r in a] == [r["rec_key"] for r in sample_records(recs, s)]        # 乱数の種を固定
    b = sample_records(recs, parse_settings({"sample_per_language": 5, "random_seed": 7}))
    assert [r["rec_key"] for r in a] != [r["rec_key"] for r in b]


def test_G14_sample_rows(classified, codebook, settings):
    rows = sample_rows(classified, codebook, settings)
    assert {r["言語"] for r in rows} == {"ja", "en", "zh", "ko"}                                 # 空欄(none)は除く
    r = next(x for x in rows if x["rec_key"] == "000004")
    assert r["主な故障"] == "M001 電源不良" and "訳:无法开机" in r["故障の種類(日本語訳)"]
    assert r["確認(×)"] == "" and r["ユーザコメント"] == "无法开机"


def test_G14_language_metrics(classified, codebook):
    issues = [{"rec_key": "000002", "kind": "excerpt_not_found"}]
    rows = {r["言語"]: r for r in language_metrics(classified, codebook, issues)}
    assert rows["none"]["件数"] == 1 and rows["none"]["判定不能(%)"] == 100.0
    assert rows["en"]["件数"] == 3 and rows["en"]["分類不可(%)"] == 0.0
    assert rows["en"]["抜き出しが原文に無い割合(%)"] == pytest.approx(33.3)
    assert rows["ja"]["故障の種類の平均件数"] == 1.0


def test_G14_classify_writes_quality_files(tmp_path, capsys):
    local = tmp_path / "local"
    init_local(local)
    out = tmp_path / "run"
    assert classify_main(["--input", str(SYNTH / "repairs.csv"), "--local-dir", str(local), "--output-dir", str(out)] + CB,
                         client=FakeDify()) == 0
    wb = openpyxl.load_workbook(out / "sample_check.xlsx", read_only=True)
    assert wb.sheetnames == ["説明", "抜き取り"]
    wb.close()
    langs = {r["言語"] for r in read_csv(out / "language_metrics.csv")}
    assert langs == {"ja", "en", "zh", "ko", "none"}
    capsys.readouterr()


# --- 評価(G-15) ---

def test_G15_normalize_name():
    assert normalize_name("ﾚﾝｽﾞ表面：ｺﾞﾐ／汚れ") == normalize_name("レンズ表面:ゴミ/汚れ")
    assert normalize_name(" AF 不良 ") == normalize_name("ＡＦ不良")


def test_G15_name_converter(codebook):
    conv = NameConverter(codebook, {("ML", normalize_name("電源が入らない")): "M001"})
    assert conv.code("ML", "電源不良") == "M001"
    assert conv.code("ML", "電源が入らない") == "M001"            # 対応表
    assert conv.code("ML", "謎の故障") is None
    assert conv.unconverted == {("ML", "謎の故障"): 1}


def test_G15_evaluate(classified, codebook):
    run_rows = [{k: str(v) if v is not None else "" for k, v in r.items() if not isinstance(v, list)} for r in classified]
    fails = {r["rec_key"]: {f["code"] for f in r["failures"]} for r in classified}
    gold = [
        {"repair_id": "R001", "sales_company": "CMJ", "received_date": "2026/1/15", "primary_name": "電源不良", "failure_codes": "M001"},
        {"repair_id": "R002", "sales_company": "CUSA", "received_date": "2026-02-03", "primary_code": "M005", "failure_codes": "M001;M005"},
        {"repair_id": "R002", "sales_company": "CUSA", "received_date": "2026-02-05", "primary_name": "謎の故障"},
        {"repair_id": "R012", "sales_company": "CMJ", "received_date": "2026-03-17", "primary_name": ""},
        {"repair_id": "R999", "sales_company": "CMJ", "received_date": "2026-03-17", "primary_code": "M001"},
    ]
    res = evaluate(gold, run_rows, fails, codebook)
    assert (res["evaluated"], res["primary_match"], res["primary_accuracy"]) == (2, 1, 0.5)
    assert (res["not_found"], res["unconverted"], res["gold_empty"]) == (1, 1, 1)
    assert (res["tp"], res["fp"], res["fn"]) == (2, 0, 1) and res["precision"] == 1.0 and res["recall"] == 0.667
    assert res["mismatches"][0]["gold_code"] == "M005" and res["mismatches"][0]["run_code"] == "M001"
    assert res["unconverted_names"] == [{"product_type": "ML", "past_name": "謎の故障", "count": 1}]


def test_G15_evaluate_command(tmp_path, capsys):
    local = tmp_path / "local"
    init_local(local)
    out = tmp_path / "run"
    classify_main(["--input", str(SYNTH / "repairs.csv"), "--local-dir", str(local), "--output-dir", str(out)] + CB, client=FakeDify())
    gold = write_csv(tmp_path / "gold.csv", ["repair_id", "sales_company", "received_date", "primary_name"],
                     [["R001", "CMJ", "2026-01-15", "電源不良"], ["R004", "CKR", "2026-03-10", "ｴﾗｰ"]])
    assert evaluate_main(["--gold", str(gold), "--run", str(out), "--local-dir", str(local)] + CB) == 0
    text = capsys.readouterr().out
    assert "評価 2 件" in text and "50.0%" in text
    j = json.loads((out / "evaluation" / "evaluate_summary.json").read_text(encoding="utf-8"))
    assert j["primary_accuracy"] == 0.5
    assert len(read_csv(out / "evaluation" / "mismatches.csv")) == 1
    bad = write_csv(tmp_path / "bad.csv", ["id", "name"], [["R001", "x"]])
    assert evaluate_main(["--gold", str(bad), "--run", str(out)] + CB) == 1


# --- 2つの実行の比較(G-30) ---

def test_G30_compare():
    def row(k, code, sales="CMJ", status="ok", info="sufficient"):
        return {"rec_key": k, "repair_id": f"R{k}", "sales_company": sales, "primary_code": code, "primary_name": code,
                "classify_status": status, "info_level": info}
    a = [row("1", "M001"), row("2", "M005"), row("3", "M_UNK", info="none"), row("4", "M001", sales="CUSA")]
    b = [row("1", "M001"), row("2", "M008"), row("3", "M009"), row("4", "M001", sales="CUSA"), row("5", "M001")]
    res = compare(a, b, "CMJ")
    assert res["matched"] == 3 and res["only_b"] == 1
    assert (res["primary_agree"], res["both_classified"], res["primary_agree_pct"]) == (1, 3, 33.3)
    assert res["unknown_pct_a"] == 33.3 and res["unknown_pct_b"] == 0.0
    assert [d["rec_key"] for d in res["differences"]] == ["2", "3"]
    assert compare(a, b)["matched"] == 4


def test_G30_compare_command_internal_on_off(tmp_path, capsys):
    """日本データ(CMJ)で、内部コメントを渡さない(既定)と渡す(all)を比べる流れ。"""
    local = tmp_path / "local"
    init_local(local)
    args = ["--input", str(SYNTH / "repairs.csv"), "--local-dir", str(local)] + CB
    classify_main(args + ["--output-dir", str(tmp_path / "a")], client=FakeDify())
    classify_main(args + ["--output-dir", str(tmp_path / "b"), "--internal-comments", "all"], client=FakeDify())
    diff = tmp_path / "diff.csv"
    assert compare_main(["--a", str(tmp_path / "a"), "--b", str(tmp_path / "b"), "--sales-company", "CMJ", "--output", str(diff)]) == 0
    text = capsys.readouterr().out
    assert "比較(CMJ): 突き合わせ 2 件" in text and "一致率: 100.0%" in text
    assert diff.exists()
    assert compare_main(["--a", str(tmp_path / "none"), "--b", str(tmp_path / "b")]) == 1
