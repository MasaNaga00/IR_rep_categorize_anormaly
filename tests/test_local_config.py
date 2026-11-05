"""ローカル作業フォルダ(要件定義書 E-06)。"""
from __future__ import annotations

from anomaly.local_config import DEFAULTS, ENV_VAR, init_local, resolve_paths

from .conftest import REPO_ROOT


def test_E06_defaults_without_local_dir():
    p = resolve_paths()
    assert p.local_dir is None
    assert p.codebook == REPO_ROOT / "config" / "classification_codes.yaml"
    assert p.columns == REPO_ROOT / "config" / "input_columns.yaml"
    assert p.settings == REPO_ROOT / "config" / "settings.yaml"
    assert p.outputs_dir == REPO_ROOT / "outputs"
    assert p.cache_dir == REPO_ROOT / "cache"
    assert p.name_map is None


def test_E06_repository_defaults_exist():
    for src in DEFAULTS.values():
        assert src.exists(), src


def test_E06_init_local_copies_without_overwrite(tmp_path):
    local = tmp_path / "anomaly_local"
    created = init_local(local)
    for rel in DEFAULTS:
        assert (local / rel).exists()
    assert not (local / "serial").exists()          # シリアル変換表は別ツールで管理する
    for sub in ("data", "outputs", "cache", "prompts"):
        assert (local / sub).is_dir()
    env = (local / ".env").read_text(encoding="utf-8")
    assert "DIFY_API_KEY_CLASSIFY" in env and "DIFY_API_KEY_SUMMARY" in env
    assert (local / "eval_name_map.csv").exists()
    assert created

    (local / "settings.yaml").write_text("recent_months: 3\n", encoding="utf-8")
    again = init_local(local)
    assert again == []                                                   # 2回目は何も作らない
    assert (local / "settings.yaml").read_text(encoding="utf-8") == "recent_months: 3\n"   # 上書きしない


def test_E06_resolution_order(tmp_path, monkeypatch):
    local = tmp_path / "local"
    init_local(local)
    p = resolve_paths(local_dir=local)
    assert p.settings == local / "settings.yaml"
    assert p.env_file == local / ".env"
    assert p.outputs_dir == local / "outputs" and p.cache_dir == local / "cache"
    assert p.name_map == local / "eval_name_map.csv"

    explicit = tmp_path / "my_settings.yaml"
    assert resolve_paths(local_dir=local, settings=explicit).settings == explicit   # 明示指定が最優先

    (local / "input_columns.yaml").unlink()                                       # ローカルに無ければ初期値
    assert resolve_paths(local_dir=local).columns == REPO_ROOT / "config" / "input_columns.yaml"

    monkeypatch.setenv(ENV_VAR, str(local))                                       # 環境変数でも指定できる
    assert resolve_paths().local_dir == local
