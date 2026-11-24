"""check コマンドと起動用スクリプト(要件定義書 U-07、U-09)。"""
from __future__ import annotations

from anomaly.check import main as check_main
from anomaly.local_config import init_local

from .conftest import run_cli


def test_U09_repository_defaults_pass(capsys):
    assert check_main([]) == 0
    out = capsys.readouterr().out
    for name in ("コード体系", "列の対応づけ", "解析の設定"):
        assert f"OK: {name}" in out
    assert "結果: 問題なし" in out


def test_U09_local_dir_after_init(tmp_path, capsys):
    local = tmp_path / "local"
    init_local(local)
    assert check_main(["--local-dir", str(local)]) == 0
    assert str(local) in capsys.readouterr().out


def test_U09_reports_all_ng(tmp_path, capsys):
    local = tmp_path / "local"
    init_local(local)
    (local / "settings.yaml").write_text("batch_size: 0\n", encoding="utf-8")
    (local / "input_columns.yaml").write_text("unknown: X\n", encoding="utf-8")
    assert check_main(["--local-dir", str(local)]) == 1
    out = capsys.readouterr().out
    assert "NG: 解析の設定" in out and "batch_size" in out
    assert "NG: 列の対応づけ" in out and "unknown" in out
    assert "OK: コード体系" in out
    assert "NG 2 件" in out
    assert "シリアル変換表" not in out          # 変換表の検査は別ツールで行う


def test_U07_cli_dispatch(tmp_path):
    local = tmp_path / "local"
    r = run_cli(["init", "--local-dir", str(local)])
    assert r.returncode == 0, r.stderr
    assert (local / "settings.yaml").exists()
    r = run_cli(["check", "--local-dir", str(local)])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "結果: 問題なし" in r.stdout


def test_U07_cli_unknown_command_shows_help():
    r = run_cli(["nope"])
    assert r.returncode == 1
    assert "anomaly_cli.py init" in r.stdout
