"""対話式の実行(要件定義書 U-01〜U-08)。画面の入力は台本(answers)で模擬し、Dify は FakeDify で模擬する。"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date

import pytest

from anomaly.cli import Console, clean_path, main
from anomaly.local_config import init_local
from anomaly.state import find_unfinished, load_state

from .conftest import FIXTURES, REPO_ROOT, SYNTH
from .fake_dify import FakeDify, api_error

CSV = str(SYNTH / "repairs.csv")
SERIAL = str(FIXTURES / "serial_results" / "converted_repairs.csv")
RUN_DATE = date(2026, 4, 1)


class Script(Console):
    def __init__(self, answers, auto_yes=False):
        self.answers = list(answers)
        self.out: list[str] = []
        self.questions: list[str] = []
        super().__init__(auto_yes=auto_yes, input_fn=self._input, print_fn=self.out.append)

    def _input(self, prompt):
        self.questions.append(prompt)
        if not self.answers:
            raise AssertionError(f"台本に無い質問: {prompt}")
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a

    @property
    def text(self) -> str:
        return "\n".join(self.out)


@pytest.fixture
def local(tmp_path):
    d = tmp_path / "local"
    init_local(d)
    (d / "classification_codes.yaml").write_bytes((FIXTURES / "codebook_test.yaml").read_bytes())
    return d


def _run(local, answers, client=None, extra=(), auto=False):
    con = Script(answers, auto_yes=auto)
    rc = main(["--local-dir", str(local), *extra], client=client or FakeDify(), console=con, run_date=RUN_DATE)
    return rc, con


def _only_run(local):
    runs = [p for p in (local / "outputs").iterdir() if p.is_dir()]
    assert len(runs) == 1
    return runs[0]


def test_U02_clean_path():
    assert clean_path(' "C:\\data\\a b.csv" ') == "C:\\data\\a b.csv"
    assert clean_path("'x.csv'") == "x.csv"
    assert clean_path("x.csv") == "x.csv"


def test_U01_U03_full_interactive(local):
    rc, con = _run(local, [f'"{CSV}"', "y", "y", SERIAL, "y", "y"])
    assert rc == 0, con.text
    run = _only_run(local)
    t = con.text
    for s in ("[1] 入力:", "除外: 受付日が読めない 1 件(値の例: '2026-13-01' 1件)", "製品×部番: 2 個", "言語別:", "修理IDが重複している行: 2 行", "[2] 分類の準備:",
              "Dify に送る 7 件", "[3] 分類を始めます", "分類できた 8 件", "[4] シリアル変換結果の結合", "ok 5",
              "[5] 解析の設定", "[6] 解析しています", "組み合わせ: 2 件", "[8] 完了しました"):
        assert s in t, s
    for name in ("excluded_rows.csv", "classifications.csv", "index.xlsx", "tableau_long.csv", "run_meta.json", "run.log", "state.json"):
        assert (run / name).exists(), name
    st = load_state(run)
    assert st.done and st.input == CSV and st.serial_result == SERIAL
    meta = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["serial_merge"]["mfg_status"]["ok"] == 5
    log = (run / "run.log").read_text(encoding="utf-8")
    assert "[質問]" in log and "[8] 完了しました" in log                        # U-08: 詳しい内容は run.log
    assert find_unfinished(local / "outputs") is None


def test_U04_no_then_resume(local):
    rc, con = _run(local, [CSV, "y", "n"])                                       # 段階2で n
    assert rc == 1 and "段階2で終了します" in con.text and "次回の起動で続きから再開できます" in con.text
    run = _only_run(local)
    assert load_state(run).completed == 1
    client = FakeDify()
    rc, con = _run(local, ["y", "y", "y", "", "y", "y"], client=client)          # 再開 → 段階1から(入力は覚えている)
    assert rc == 0, con.text
    assert "途中で終わった実行があります" in con.text
    assert _only_run(local) == run and load_state(run).done
    assert not any("修理データの CSV のパス" in q for q in con.questions)        # 入力ファイルは聞き直さない
    assert "シリアル変換結果: 指定なし" in con.text


def test_U04_resume_after_classification_no_dify_calls(local):
    rc, _ = _run(local, [CSV, "y", "y", "", "n"])                                # 段階4で n
    assert rc == 1
    run = _only_run(local)
    assert load_state(run).completed == 4
    client = FakeDify()
    rc, con = _run(local, ["y", "y"], client=client)                             # 再開 → 段階5から
    assert rc == 0, con.text
    assert client.calls == []
    assert "[1]" not in con.text and "[5] 解析の設定" in con.text


def test_U04_resume_declined_starts_new(local):
    _run(local, [CSV, "n"])
    rc, con = _run(local, ["n", CSV, "n"])
    assert len([p for p in (local / "outputs").iterdir() if p.is_dir()]) == 2


def test_G22_retry_choice(local):
    calls = {"n": 0}

    def flaky(n, recs, ideal):
        calls["n"] += 1
        return api_error() if calls["n"] <= 4 else ideal                         # 最初のバッチ2回 + 1件ずつの再試行の一部が失敗
    rc, con = _run(local, [CSV, "y", "y", "1", "", "y", "y"], client=FakeDify(flaky))
    assert rc == 0, con.text
    assert "注意: 分類不可が" in con.text and "再試行の結果: 分類できた 8 件、分類不可 0 件" in con.text


def test_G22_abort_choice(local):
    rc, con = _run(local, [CSV, "y", "y", "3"], client=FakeDify(lambda n, r, i: api_error()))
    assert rc == 1 and "段階3で中断します" in con.text
    run = _only_run(local)
    assert load_state(run).completed == 2 and (run / "classify_failed.csv").exists()


def test_U03_stage5_change_settings(local):
    answers = [CSV, "y", "y", "", "y", "n", "abc", "3", "", "", "", "", "", "", "", "", ""]
    rc, con = _run(local, answers)
    assert rc == 0, con.text
    assert "値が正しくありません" in con.text and "変更後: recent_months=3" in con.text
    run = _only_run(local)
    assert load_state(run).overrides == {"recent_months": 3}
    md = (run / "combos" / "X100__P-001" / "report.md").read_text(encoding="utf-8")
    assert "直近3か月(2026-01〜2026-03)" in md


def test_U03_invalid_answer_asked_again(local):
    rc, con = _run(local, [CSV, "maybe", "y", "y", "", "y", "y"])
    assert rc == 0 and "y か n で答えてください" in con.text


def test_U03_bad_input_then_other_file(local, tmp_path):
    rc, con = _run(local, [str(tmp_path / "none.csv"), "y", CSV, "y", "y", "", "y", "y"])
    assert rc == 0, con.text
    assert "NG: 入力ファイルを読めません" in con.text and "対処:" in con.text


def test_U03_bad_serial_then_skip(local, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    rc, con = _run(local, [CSV, "y", "y", str(bad), "", "y", "y"])
    assert rc == 0 and "シリアル変換結果を読めません" in con.text


def test_U06_yes(local):
    rc, con = _run(local, [], extra=["--input", CSV, "--yes"], auto=True)
    assert rc == 0, con.text
    assert con.questions == []
    assert load_state(_only_run(local)).done


def test_U06_yes_with_serial(local):
    rc, con = _run(local, [], extra=["--input", CSV, "--serial-result", SERIAL, "--yes"], auto=True)
    assert rc == 0 and "ok 5" in con.text


def test_U06_yes_requires_input(local):
    rc, con = _run(local, [], extra=["--yes"], auto=True)
    assert rc == 1 and "--input" in con.text


def test_U08_missing_local_dir(tmp_path):
    con = Script([])
    assert main(["--local-dir", str(tmp_path / "none")], console=con) == 1
    assert "anomaly_cli.py init" in con.text


def test_U08_missing_dify_env(local, monkeypatch):
    for k in ("DIFY_BASE_URL", "DIFY_API_KEY_CLASSIFY"):
        monkeypatch.setenv(k, "x")
        monkeypatch.delenv(k)
    con = Script([CSV, "y", "y"])
    rc = main(["--local-dir", str(local)], console=con, run_date=RUN_DATE)
    assert rc == 1 and "DIFY_API_KEY_CLASSIFY" in con.text and "対処:" in con.text
    assert load_state(_only_run(local)).completed == 2


def test_U05_keyboard_interrupt(local):
    rc, con = _run(local, [CSV, KeyboardInterrupt()])
    assert rc == 130 and "中断しました" in con.text
    assert load_state(_only_run(local)).completed == 0


def test_U02_cli_dispatch_interactive(local):
    """anomaly_cli.py にサブコマンドを付けないと対話式になる(Dify の接続情報が無いので段階3で止まる)。"""
    r = subprocess.run([sys.executable, str(REPO_ROOT / "anomaly_cli.py"), "--local-dir", str(local), "--input", CSV, "--yes"],
                       capture_output=True, text=True, encoding="utf-8",
                       env={"PATH": "", "SYSTEMROOT": "", "PYTHONDONTWRITEBYTECODE": "1"})
    assert r.returncode == 1
    assert "[2] 分類の準備" in r.stdout and "DIFY_API_KEY_CLASSIFY" in r.stdout


def test_U02_cli_help():
    r = subprocess.run([sys.executable, str(REPO_ROOT / "anomaly_cli.py"), "--help"], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0 and "対話式" in r.stdout
