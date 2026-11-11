"""settings.yaml の読み込みと検査(要件定義書 E-07、第12節、G-12、G-30)。"""
from __future__ import annotations

import pytest
import yaml

from anomaly.settings import DEFAULTS, SettingsError, load_settings, parse_settings

from .conftest import REPO_ROOT


def _write(tmp_path, data):
    p = tmp_path / "settings.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


def test_E07_defaults_match_section12():
    s = parse_settings(None)
    assert s.japan_sales_companies == ("CMJ",)
    assert s.internal_comment_excluded_sales_companies == ("CMJ",)
    assert (s.batch_size, s.single_retry_max, s.failed_warn_ratio) == (10, 2, 0.05)
    assert (s.sample_per_language, s.random_seed, s.examples_per_finding) == (20, 42, 3)
    assert s.usage_max_months == 96
    assert (s.rare_code_min, s.region_diff_note_pt) == (5, 20)
    assert (s.concentration_high_pct, s.concentration_low_pct) == (50, 30)
    assert (s.recent_months, s.recent_compare) == (6, "all_before")
    assert s.usage_bins == (0, 7, 13, 25, 37) and s.usage_split_months == 24
    assert s.summary_top_n == 10 and s.warnings == ()
    assert (s.lot_neighbor, s.lot_spike_ratio, s.lot_min_n, s.lot_window) == (3, 2.0, 5, 3)
    assert (s.min_group_n, s.alpha, s.min_diff_pt, s.min_code_n) == (30, 0.05, 10, 5)


def test_E07_repository_settings_yaml_equals_defaults():
    """リポジトリ同梱の config/settings.yaml は、コードの既定値と同じ内容であること。"""
    s = load_settings(REPO_ROOT / "config" / "settings.yaml")
    assert s.as_dict() == parse_settings(None).as_dict()
    raw = yaml.safe_load((REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert set(raw) == set(DEFAULTS)   # すべての項目が書かれている


def test_A40_deprecated_early_months_is_warning_not_error(tmp_path):
    """古いローカル作業フォルダの settings.yaml に early_months が残っていても、エラーにせず知らせる。"""
    s = load_settings(_write(tmp_path, {"early_months": 12}))
    assert s.usage_split_months == 24
    assert len(s.warnings) == 1 and "usage_split_months" in s.warnings[0]


def test_E07_missing_file_uses_defaults(tmp_path):
    assert load_settings(tmp_path / "none.yaml").batch_size == 10
    assert load_settings(None).batch_size == 10


def test_E07_partial_override(tmp_path):
    s = load_settings(_write(tmp_path, {"recent_months": 3, "usage_split_months": 6}))
    assert s.recent_months == 3 and s.usage_split_months == 6
    assert s.batch_size == 10              # 書かなかった項目は既定値
    assert s.source.endswith("settings.yaml")


@pytest.mark.parametrize("data, expect", [
    ({"batch_size": 0}, "batch_size"),
    ({"batch_size": "10"}, "batch_size"),
    ({"single_retry_max": True}, "single_retry_max"),
    ({"failed_warn_ratio": 1.5}, "failed_warn_ratio"),
    ({"alpha": 0}, "alpha"),
    ({"recent_compare": "previous"}, "recent_compare"),
    ({"usage_bins": [1, 7, 13]}, "usage_bins"),
    ({"usage_bins": [0, 13, 7]}, "usage_bins"),
    ({"usage_bins": [0]}, "usage_bins"),
    ({"japan_sales_companies": "CMJ"}, "japan_sales_companies"),
    ({"internal_comment_excluded_sales_companies": [""]}, "internal_comment_excluded_sales_companies"),
    ({"serial": {"type2_cycle_start_ym": "2018-01"}}, "serial"),   # 変換は別ツール。この解析の設定には無い
    ({"unknown_key": 1}, "unknown_key"),
    ({"concentration_high_pct": 30, "concentration_low_pct": 40}, "concentration_low_pct"),
    ({"concentration_high_pct": 120}, "concentration_high_pct"),
])
def test_E07_validation_errors(tmp_path, data, expect):
    with pytest.raises(SettingsError) as ei:
        load_settings(_write(tmp_path, data))
    assert expect in str(ei.value)


def test_E07_all_errors_listed(tmp_path):
    with pytest.raises(SettingsError) as ei:
        load_settings(_write(tmp_path, {"batch_size": 0, "alpha": 2, "typo_key": 1}))
    assert len(ei.value.errors) == 3


def test_E07_broken_yaml(tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text("batch_size: [1,\n", encoding="utf-8")
    with pytest.raises(SettingsError) as ei:
        load_settings(p)
    assert "YAML" in str(ei.value)


def test_G12_internal_excluded_default(settings):
    assert settings.internal_excluded("CMJ") is True
    assert settings.internal_excluded("CUSA") is False


def test_G30_internal_modes(settings):
    assert settings.internal_excluded("CMJ", "all") is False
    assert settings.internal_excluded("CUSA", "none") is True
    with pytest.raises(ValueError):
        settings.internal_excluded("CMJ", "some")


def test_G04_is_japan(settings):
    assert settings.is_japan("CMJ") and not settings.is_japan("CCN")


def test_O05_as_dict_roundtrip():
    s = parse_settings({"recent_months": 4})
    assert parse_settings(s.as_dict()).as_dict() == s.as_dict()
