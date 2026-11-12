"""出力ファイルと analyze コマンド(要件定義書 O-01〜O-06、U-07)。"""
from __future__ import annotations

import json

import openpyxl
import pytest

from anomaly.analyze import analyze_records, main
from anomaly.outputs.files import combo_dir_name, read_classified, safe_name, write_classified
from anomaly.outputs.tableau import tableau_rows

from .conftest import read_csv
from .synth_classified import RUN_DATE, balanced, idx, make, with_recent_excess


def test_O06_safe_names():
    assert safe_name("A/B:C*D") == "A_B_C_D"
    assert combo_dir_name("X 100", "P/001") == "X_100__P_001"
    assert safe_name("  ") == "_"


def test_O01_classified_roundtrip(tmp_path, codebook):
    env = [{"factor": "impact", "source": "user", "abnormal_use": True, "excerpt": "落とした"}]
    recs = [make(1, "M001", idx(2025, 3), env=env, mfg_idx=idx(2024, 1)), make(2, "FAILED", idx(2025, 3)),
            make(3, "UNK", idx(2025, 3), lot_code="12")]
    recs[0]["failures"].append({"code": "M005", "source": "user", "excerpt": "AF\n不良", "excerpt_ja": "AF 不良"})
    write_classified(tmp_path, recs, codebook.code_name)
    back = read_classified(tmp_path)
    assert [r["rec_key"] for r in back] == ["000001", "000002", "000003"]
    r = back[0]
    assert [f["code"] for f in r["failures"]] == ["M001", "M005"] and r["failures"][1]["excerpt"] == "AF\n不良"
    assert r["environment"] == env
    assert r["lot_order"] == idx(2024, 1) and r["usage_months"] == 14 and r["primary_name"] == "電源不良"
    assert back[2]["lot_order"] == 12 and back[2]["usage_months"] is None
    assert read_csv(tmp_path / "failures.csv")[0]["name"] == "電源不良"


def test_O01_read_classified_missing_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_classified(tmp_path)


def test_O03_tableau_rows(codebook, settings):
    recs = [make(1, "M001", idx(2025, 3), mfg_idx=idx(2025, 1)), make(2, "FAILED", idx(2025, 3)), make(3, "UNK", idx(2025, 3))]
    recs[0]["failures"].append({"code": "M005", "source": "user", "excerpt": "x", "excerpt_ja": "x"})
    rows = list(tableau_rows(recs, codebook, settings))
    assert [(r["rec_key"], r["code"], r["is_primary"]) for r in rows] == [
        ("000001", "M001", True), ("000001", "M005", False), ("000002", "CLASSIFY_FAILED", True), ("000003", "M_UNK", True)]
    assert rows[0]["usage_bin"] == "0〜6か月" and rows[0]["received_ym"] == "2025-03"
    assert rows[2]["primary_name"] == "分類不可" and rows[3]["code_name"] == "判定不能"


@pytest.fixture(scope="module")
def analyzed(tmp_path_factory, codebook, settings):
    out = tmp_path_factory.mktemp("out")
    recs = with_recent_excess("M009", 60) + [make(90000 + i, "L001", idx(2025, 3), model="L 200", part="P/777", product="LENS")
                                            for i in range(5)]
    results = analyze_records(recs, codebook, settings, RUN_DATE, out, {"internal_excluded": ["CMJ"], "serial_result": "x"})
    return out, results


def test_O01_files_written(analyzed):
    out, results = analyzed
    assert (out / "index.xlsx").exists() and (out / "tableau_long.csv").exists()
    for folder in ("X100__P-001", "L_200__P_777"):
        d = out / "combos" / folder
        assert (d / "tables.xlsx").exists() and (d / "report.md").exists() and (d / "findings.json").exists()
    assert [f for _, f in results] == ["X100__P-001", "L_200__P_777"]


def test_O04_tables_sheets(analyzed):
    out, _ = analyzed
    wb = openpyxl.load_workbook(out / "combos" / "X100__P-001" / "tables.xlsx", read_only=True)
    assert wb.sheetnames == ["サマリー(環境要因除く)", "サマリー", "前提", "概要", "全体", "偏りの指標", "コード別(まとめる前)", "記載割合", "環境要因", "販社別", "再修理の可能性",
                             "期間比較", "月別推移", "使用期間_分布", "使用期間_コード別", "使用期間_クロス", "使用期間_比較",
                             "ロット", "ロット_構成", "ロット_まとめ", "ロット_比較", "群の構成", "代表事例", "除外・変換不能"]
    ws = wb["期間比較"]
    header = [c.value for c in next(ws.iter_rows(max_row=1))]
    assert header[:5] == ["比較", "群A", "群B", "コード", "名称"] and "注目点" in header
    marks = [row[header.index("注目点")] for row in ws.iter_rows(min_row=2, values_only=True)]
    assert "○" in marks
    assert wb["再修理の可能性"]["A1"].value == "(データなし)"
    wb.close()


def test_O02_index(analyzed):
    out, _ = analyzed
    wb = openpyxl.load_workbook(out / "index.xlsx", read_only=True)
    rows = list(wb["一覧"].iter_rows(values_only=True))
    header = rows[0]
    first = dict(zip(header, rows[1]))
    assert first["製品名"] == "X100" and first["修理件数"] == 1356 and first["注目点の数"] > 0
    assert first["期間比較"] == "注目点あり" and first["フォルダ"] == "X100__P-001"
    assert first["最も多い故障"] == "電源不良" and first["最も多い故障の割合(%)"] == 38.1 and first["上位3つの割合(%)"] == 80.0
    second = dict(zip(header, rows[2]))
    assert second["期間比較"] == "件数不足" and second["ロット"] == "情報なし"
    wb.close()


def test_A14b_findings_file(analyzed):
    out, _ = analyzed
    j = json.loads((out / "combos" / "X100__P-001" / "findings.json").read_text(encoding="utf-8"))
    assert j["n_findings"] > 0 and j["run_date"] == "2025-07-10"


def test_U07_analyze_command(tmp_path, codebook, capsys):
    cls = tmp_path / "classified"
    write_classified(cls, balanced(), codebook.code_name)
    out = tmp_path / "out"
    rc = main(["--classified", str(cls), "--run-date", "2025-07-10", "--output-dir", str(out),
               "--codebook", str(__import__("tests.conftest", fromlist=["x"]).CODEBOOK_TEST_YAML)])
    text = capsys.readouterr().out
    assert rc == 0, text
    assert "組み合わせ: 1 件" in text and "注目点 0" in text
    md = (out / "combos" / "X100__P-001" / "report.md").read_text(encoding="utf-8")
    assert "目立った差は見られません" in md and "(分類済みのデータに結合済み)" not in md
    assert main(["--classified", str(cls), "--output-dir", str(out)]) == 1        # 空でない出力先には書かない


def test_U07_analyze_command_errors(tmp_path, capsys):
    assert main(["--classified", str(tmp_path / "none"), "--output-dir", str(tmp_path / "o")]) == 1
    assert "NG:" in capsys.readouterr().out
    assert main(["--classified", str(tmp_path), "--run-date", "2025/07/10"]) == 1
