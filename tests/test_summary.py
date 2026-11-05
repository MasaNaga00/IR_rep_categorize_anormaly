"""Dify での要約文と数値の照合(要件定義書 T-02 層2、T-03、T-05、U-03 段階7)。"""
from __future__ import annotations

from datetime import date

import pytest

from anomaly.analyze import analyze_records
from anomaly.cli import main as cli_main
from anomaly.dify_client import DifyAPIError, DifyResponse
from anomaly.local_config import init_local
from anomaly.report.number_check import allowed_numbers, numbers_in, unmatched_numbers
from anomaly.report.summary import (END, START, build_inputs, check_summary, has_summary, insert_summary, main,
                                    strip_summary, summarize_run)

from .conftest import FIXTURES, SYNTH
from .fake_dify import FakeDify
from .synth_classified import RUN_DATE, with_recent_excess
from .test_cli import Script


class FakeSummary:
    def __init__(self, text="全体では『電源不良』が 38.1% で最も多く、直近6か月では『異音』の割合が 37.1% と高くなっています。", fail=False):
        self.text, self.fail, self.calls = text, fail, []

    def run(self, inputs):
        self.calls.append(inputs)
        if self.fail:
            raise DifyAPIError(503, "unavailable")
        return DifyResponse(self.text, 50)


@pytest.fixture
def run_dir(tmp_path, codebook, settings):
    out = tmp_path / "run"
    analyze_records(with_recent_excess(), codebook, settings, RUN_DATE, out, {"internal_excluded": ["CMJ"], "serial_result": "x"})
    return out


# --- 数値の照合(T-05) ---

def test_T05_numbers_in():
    assert numbers_in("37.1%(78/210件)、1,050件、２５ポイント") == {"37.1", "78", "210", "1050", "25"}


def test_T05_allowed_and_unmatched():
    findings = {"a": [{"pct": 37.1, "n": 210}], "b": "2025-01〜2025-06", "flag": True}
    allowed = allowed_numbers(findings, "上位3つで 84.0% を占めます")
    assert {"37.1", "210", "2025", "1", "6", "3", "84"} <= allowed
    assert unmatched_numbers("割合は 37.1% で 3 ポイント、40% の差", findings, "上位3つで 84.0%") == ["40"]


def test_T05_T04_check_summary():
    w = check_summary("故障が増加しています。55.5% です。", {}, "50.0%")
    assert len(w) == 2 and "55.5" in w[0] and "増加" in w[1]
    assert check_summary("割合は 50% です。", {}, "50.0%") == []


# --- report.md への差し込み ---

def test_T02_insert_and_strip():
    md = "# タイトル\n\n製品種別: ML\n\n## 前提\n\n- a\n"
    out = insert_summary(md, "要約の文。", ["警告1"])
    assert out.index("## 要約(Dify による文章)") < out.index("## 前提")
    assert "## 要約の確認" in out and "- 警告1" in out
    assert has_summary(out) and not has_summary(md)
    assert strip_summary(out).strip() == md.strip()
    again = insert_summary(out, "新しい要約。", [])
    assert again.count(START) == 1 and "新しい要約。" in again and "要約の文。" not in again and "要約の確認" not in again


def test_T02_build_inputs_uses_base_report():
    md = insert_summary("# t\n\n## 前提\n", "古い要約", [])
    inp = build_inputs({"x": 1}, md)
    assert set(inp) == {"findings", "report"} and "古い要約" not in inp["report"] and '"x": 1' in inp["findings"]


# --- 実行フォルダへの適用 ---

def test_T02_summarize_run(run_dir):
    client = FakeSummary()
    st = summarize_run(run_dir, client)
    assert (st.done, st.skipped, st.failed, st.warned) == (1, 0, 0, 0)
    md = (run_dir / "combos" / "X100__P-001" / "report.md").read_text(encoding="utf-8")
    assert "## 要約(Dify による文章)" in md and "37.1%" in md and "要約の確認" not in md
    assert "『異音』の割合が 37.1%(78/210件)" in client.calls[0]["report"]
    st2 = summarize_run(run_dir, FakeSummary())
    assert st2.skipped == 1 and st2.done == 0                    # 作り済みは飛ばす
    st3 = summarize_run(run_dir, FakeSummary("全体の 99.9% が増加。"), force=True)
    assert st3.done == 1 and st3.warned == 1
    md = (run_dir / "combos" / "X100__P-001" / "report.md").read_text(encoding="utf-8")
    assert md.count(START) == 1 and "99.9" in md and "## 要約の確認" in md


def test_T02_summarize_errors(run_dir):
    st = summarize_run(run_dir, FakeSummary(fail=True))
    assert st.failed == 1 and "unavailable" in st.errors[0]
    md = (run_dir / "combos" / "X100__P-001" / "report.md").read_text(encoding="utf-8")
    assert not has_summary(md)                                  # 失敗したら report.md は変えない
    assert summarize_run(run_dir, FakeSummary(text="  ")).failed == 1


def test_summarize_command(run_dir, tmp_path, capsys, monkeypatch):
    assert main(["--run", str(run_dir)], client=FakeSummary()) == 0
    assert "作った 1 件" in capsys.readouterr().out
    assert main(["--run", str(tmp_path / "none")], client=FakeSummary()) == 1
    for k in ("DIFY_BASE_URL", "DIFY_API_KEY_SUMMARY"):
        monkeypatch.setenv(k, "x")
        monkeypatch.delenv(k)
    local = tmp_path / "local"
    init_local(local)
    assert main(["--run", str(run_dir), "--local-dir", str(local)]) == 1
    assert "DIFY_API_KEY_SUMMARY" in capsys.readouterr().out


# --- 対話式の段階7(U-03) ---

def test_U03_stage7_summary(tmp_path):
    local = tmp_path / "local"
    init_local(local)
    (local / "classification_codes.yaml").write_bytes((FIXTURES / "codebook_test.yaml").read_bytes())
    con = Script([str(SYNTH / "repairs.csv"), "y", "y", "", "y", "y", "y"])
    summ = FakeSummary("全体では特定の故障への偏りは見られません。")
    rc = cli_main(["--local-dir", str(local)], client=FakeDify(), console=con, run_date=date(2026, 4, 1), summary_client=summ)
    assert rc == 0, con.text
    assert "Dify で要約文も作りますか?(y/n)" in con.questions[-1]
    assert "要約: 作った 2 件" in con.text and len(summ.calls) == 2
