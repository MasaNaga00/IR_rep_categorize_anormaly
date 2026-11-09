"""classify コマンドと、入力 → 分類 → 解析の通し(要件定義書 O-01、O-05、G-22、U-07)。Dify は FakeDify で模擬する。"""
from __future__ import annotations

import json

from anomaly.analyze import main as analyze_main
from anomaly.classify_cmd import main
from anomaly.local_config import init_local
from anomaly.outputs.files import read_classified

from .conftest import CODEBOOK_TEST_YAML, FIXTURES, SYNTH, read_csv
from .fake_dify import FakeDify, api_error

CB = ["--codebook", str(CODEBOOK_TEST_YAML)]


def test_O01_classify_writes_run_folder(tmp_path, capsys):
    local = tmp_path / "local"
    init_local(local)
    out = tmp_path / "run1"
    client = FakeDify()
    rc = main(["--input", str(SYNTH / "repairs.csv"), "--local-dir", str(local), "--output-dir", str(out),
               "--serial-result", str(FIXTURES / "serial_results" / "converted_repairs.csv")] + CB, client=client)
    text = capsys.readouterr().out
    assert rc == 0, text
    for name in ("classifications.csv", "failures.csv", "environments.csv", "classify_failed.csv",
                 "validation_issues.csv", "run_meta.json"):
        assert (out / name).exists(), name
    assert list((out / "raw").glob("batch_*_request.json"))
    recs = {r["rec_key"]: r for r in read_classified(out)}
    assert recs["000001"]["primary_code"] == "M001" and recs["000001"]["mfg_ym"] == "2018-06"
    assert recs["000001"]["usage_months"] == 91 and recs["000004"]["lang"] == "zh"
    meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["counts"]["loaded"] == 8 and meta["counts"]["duplicate_repair_id_rows"] == 2
    assert meta["classify"]["ok"] == 8 and meta["classify"]["calls"] == 2
    assert meta["serial_merge"]["mfg_status"]["ok"] == 5
    assert len(meta["codebook"]["sha256"]) == 64 and len(meta["classify_prompt"]["sha256"]) == 64
    assert meta["internal_comments"] == "default"
    assert "分類できた 8 件、分類不可 0 件" in text and "次: python anomaly_cli.py analyze" in text
    assert list((local / "cache").glob("classify_*.csv"))                     # キャッシュはローカル作業フォルダ


def test_O01_end_to_end_classify_then_analyze(tmp_path, capsys):
    local = tmp_path / "local"
    init_local(local)
    out = tmp_path / "run"
    assert main(["--input", str(SYNTH / "repairs.csv"), "--local-dir", str(local), "--output-dir", str(out)] + CB,
                client=FakeDify()) == 0
    assert analyze_main(["--classified", str(out), "--local-dir", str(local), "--run-date", "2026-04-01"] + CB) == 0
    assert (out / "index.xlsx").exists()
    md = (out / "combos" / "X100__P-001" / "report.md").read_text(encoding="utf-8")
    assert "修理件数: 7 件" in md and "シリアルの変換結果は指定されていません" not in md
    assert analyze_main(["--classified", str(out), "--local-dir", str(local)] + CB) == 1    # 2回目は上書きしない
    capsys.readouterr()


def test_classify_cache_second_run(tmp_path, capsys):
    local = tmp_path / "local"
    init_local(local)
    args = ["--input", str(SYNTH / "repairs.csv"), "--local-dir", str(local)] + CB
    assert main(args + ["--output-dir", str(tmp_path / "a")], client=FakeDify()) == 0
    client = FakeDify()
    assert main(args + ["--output-dir", str(tmp_path / "b")], client=client) == 0
    assert client.calls == []
    assert "キャッシュ 7 件" in capsys.readouterr().out
    client2 = FakeDify()
    assert main(args + ["--output-dir", str(tmp_path / "c"), "--internal-comments", "all"], client=client2) == 0
    assert client2.calls                                                       # 渡し方が違えば別のキャッシュ
    capsys.readouterr()


def test_G22_failed_warning(tmp_path, capsys):
    local = tmp_path / "local"
    init_local(local)
    out = tmp_path / "run"
    client = FakeDify(lambda n, recs, ideal: api_error())
    assert main(["--input", str(SYNTH / "repairs.csv"), "--local-dir", str(local), "--output-dir", str(out)] + CB,
                client=client) == 0
    text = capsys.readouterr().out
    assert "分類不可 7 件" in text and "注意: 分類不可が" in text
    failed = read_csv(out / "classify_failed.csv")
    assert len(failed) == 7 and {r["failed_reason"] for r in failed} == {"api_error"}
    issues = read_csv(out / "validation_issues.csv")
    assert issues and issues[0]["kind"] == "api_error"
    assert not list((local / "cache").glob("classify_*.csv"))                 # 分類不可はキャッシュしない


def test_dry_run_without_env(tmp_path, capsys):
    local = tmp_path / "local"
    init_local(local)
    out = tmp_path / "run"
    assert main(["--input", str(SYNTH / "repairs.csv"), "--local-dir", str(local), "--output-dir", str(out), "--dry-run",
                 "--batch-size", "3"] + CB) == 0
    text = capsys.readouterr().out
    assert "dry-run" in text and "リクエスト 3 件(修理 7 件)" in text      # 空欄の1件は送らない
    assert not (out / "classifications.csv").exists()
    # I-05a: dry-run でも除外の内訳を画面に出し、一覧を書く
    assert "除外: コード体系に無い製品種別 1 件(値の例: 'XX' 1件)" in text and "excluded_rows.csv" in text
    rows = read_csv(out / "excluded_rows.csv")
    assert len(rows) == 7 and {r["reason"] for r in rows} >= {"unknown_product", "invalid_received_date"}
    assert json.loads((out / "run_meta.json").read_text(encoding="utf-8"))["dry_run"] is True


def test_errors(tmp_path, capsys, monkeypatch):
    local = tmp_path / "local"
    init_local(local)
    for k in ("DIFY_BASE_URL", "DIFY_API_KEY_CLASSIFY"):
        monkeypatch.setenv(k, "x")
        monkeypatch.delenv(k)
    (local / ".env").write_text("", encoding="utf-8")
    assert main(["--input", str(SYNTH / "repairs.csv"), "--local-dir", str(local)] + CB) == 1    # 接続情報が無い
    assert "DIFY_API_KEY_CLASSIFY" in capsys.readouterr().out
    assert main(["--input", str(tmp_path / "none.csv"), "--local-dir", str(local), "--dry-run"] + CB) == 1
    assert main(["--input", str(SYNTH / "repairs.csv"), "--local-dir", str(local), "--dry-run", "--batch-size", "0"] + CB) == 1
    out = tmp_path / "run"
    assert main(["--input", str(SYNTH / "repairs.csv"), "--local-dir", str(local), "--output-dir", str(out)] + CB,
                client=FakeDify()) == 0
    assert main(["--input", str(SYNTH / "repairs.csv"), "--local-dir", str(local), "--output-dir", str(out)] + CB,
                client=FakeDify()) == 1                                         # 同じ実行フォルダには書かない
    capsys.readouterr()
