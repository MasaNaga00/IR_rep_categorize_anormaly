"""settings.yaml(解析の既定値・閾値)の読み込みと検査(要件定義書 E-07、第12節)。

- 書かれていないキーは既定値を使う。
- 知らないキー、型・範囲の違反は SettingsError にまとめて送出する(違反はすべて列挙する)。
- 使われなくなったキー(DEPRECATED)はエラーにせず、Settings.warnings に知らせる文を入れる。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .constants import INTERNAL_MODES

# 第12節の既定値。
DEFAULTS: dict[str, Any] = {
    "japan_sales_companies": ["CMJ"],
    "internal_comment_excluded_sales_companies": ["CMJ"],
    "batch_size": 10,
    "single_retry_max": 2,
    "failed_warn_ratio": 0.05,
    "sample_per_language": 20,
    "random_seed": 42,
    "usage_max_months": 96,
    "rare_code_min": 5,
    "keep_top_codes": 5,
    "concentration_high_pct": 50,
    "concentration_low_pct": 30,
    "region_diff_note_pt": 20,
    "recent_months": 6,
    "recent_compare": "all_before",
    "usage_bins": [0, 7, 13, 25, 37],
    "usage_split_months": 24,
    "lot_neighbor": 3,
    "lot_spike_ratio": 2.0,
    "lot_min_n": 5,
    "lot_window": 3,
    "min_group_n": 30,
    "alpha": 0.05,
    "min_diff_pt": 10,
    "min_code_n": 5,
    "examples_per_finding": 3,
    "summary_top_n": 10,
}

# 使われなくなったキー → 知らせる文(エラーにはしない)
DEPRECATED: dict[str, str] = {
    "early_months": ("early_months は使われなくなりました(2026-11-12)。使用期間の区切りは usage_split_months"
                     "(この月数未満 / 以上で分ける。既定 24)で決まります。settings.yaml から early_months の行を消してください。"),
}

RECENT_COMPARE_VALUES = ("all_before", "same_length")


class SettingsError(Exception):
    def __init__(self, path, errors: list[str]):
        self.path = str(path) if path is not None else "(既定値)"
        self.errors = list(errors)
        super().__init__(f"{self.path}: {len(self.errors)} 件の問題があります\n" + "\n".join(f"- {e}" for e in self.errors))


@dataclass(frozen=True)
class Settings:
    japan_sales_companies: tuple[str, ...]
    internal_comment_excluded_sales_companies: tuple[str, ...]
    batch_size: int
    single_retry_max: int
    failed_warn_ratio: float
    sample_per_language: int
    random_seed: int
    usage_max_months: int
    rare_code_min: int
    keep_top_codes: int
    concentration_high_pct: float
    concentration_low_pct: float
    region_diff_note_pt: float
    recent_months: int
    recent_compare: str
    usage_bins: tuple[int, ...]
    usage_split_months: int
    lot_neighbor: int
    lot_spike_ratio: float
    lot_min_n: int
    lot_window: int
    min_group_n: int
    alpha: float
    min_diff_pt: float
    min_code_n: int
    examples_per_finding: int
    summary_top_n: int
    source: str = "(既定値)"
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        """run_meta.json 用(O-05)。settings.yaml と同じ形にする。"""
        d = {k: getattr(self, k) for k in DEFAULTS}
        return {k: list(v) if isinstance(v, tuple) else v for k, v in d.items()}

    def is_japan(self, sales_company: str) -> bool:
        return sales_company in self.japan_sales_companies

    def internal_excluded(self, sales_company: str, mode: str = "default") -> bool:
        """G-12, G-30: その販社の内部コメントを LLM に渡さないか。"""
        if mode not in INTERNAL_MODES:
            raise ValueError(f"内部コメントの指定が不正です: {mode!r}(default / all / none)")
        if mode == "all":
            return False
        if mode == "none":
            return True
        return sales_company in self.internal_comment_excluded_sales_companies


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return (isinstance(v, (int, float))) and not isinstance(v, bool)


def _check(data: dict) -> list[str]:
    errs: list[str] = []

    def int_min(key, lo):
        v = data[key]
        if not _is_int(v) or v < lo:
            errs.append(f"{key}: {lo} 以上の整数にしてください({v!r})")

    def num_range(key, lo, hi, lo_open=False):
        v = data[key]
        ok = _is_num(v) and (v > lo if lo_open else v >= lo) and v <= hi
        if not ok:
            errs.append(f"{key}: {'>' if lo_open else '≥'}{lo} かつ ≤{hi} の数にしてください({v!r})")

    for key in ("japan_sales_companies", "internal_comment_excluded_sales_companies"):
        v = data[key]
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            errs.append(f"{key}: 販社名(文字列)のリストにしてください({v!r})")
    int_min("batch_size", 1)
    int_min("single_retry_max", 0)
    num_range("failed_warn_ratio", 0, 1)
    int_min("sample_per_language", 1)
    int_min("random_seed", 0)
    int_min("usage_max_months", 1)
    int_min("rare_code_min", 1)
    int_min("keep_top_codes", 0)
    num_range("concentration_high_pct", 0, 100)
    num_range("concentration_low_pct", 0, 100)
    if _is_num(data["concentration_high_pct"]) and _is_num(data["concentration_low_pct"]) \
            and data["concentration_low_pct"] >= data["concentration_high_pct"]:
        errs.append("concentration_low_pct は concentration_high_pct より小さくしてください")
    num_range("region_diff_note_pt", 0, 100)
    int_min("recent_months", 1)
    if data["recent_compare"] not in RECENT_COMPARE_VALUES:
        errs.append(f"recent_compare: {' / '.join(RECENT_COMPARE_VALUES)} のどれかにしてください({data['recent_compare']!r})")
    bins = data["usage_bins"]
    if (not isinstance(bins, list) or len(bins) < 2 or not all(_is_int(b) and b >= 0 for b in bins)
            or bins[0] != 0 or any(b2 <= b1 for b1, b2 in zip(bins, bins[1:]))):
        errs.append(f"usage_bins: 0 から始まる、増えていく整数のリスト(2個以上)にしてください({bins!r})")
    int_min("usage_split_months", 1)
    int_min("lot_neighbor", 1)
    num_range("lot_spike_ratio", 1, 1000)
    int_min("lot_min_n", 1)
    int_min("lot_window", 1)
    int_min("min_group_n", 1)
    num_range("alpha", 0, 1, lo_open=True)
    num_range("min_diff_pt", 0, 100)
    int_min("min_code_n", 1)
    int_min("examples_per_finding", 0)
    int_min("summary_top_n", 1)
    return errs


def parse_settings(raw: dict | None, source: str = "(既定値)") -> Settings:
    data = copy.deepcopy(DEFAULTS)
    raw = raw or {}
    if not isinstance(raw, dict):
        raise SettingsError(source, ["settings.yaml の最上位が辞書ではありません"])
    errs = [f"不明な項目があります: {k}" for k in raw if k not in DEFAULTS and k not in DEPRECATED]
    warnings = tuple(DEPRECATED[k] for k in raw if k in DEPRECATED)
    for k, v in raw.items():
        if k in DEFAULTS:
            data[k] = v
    errs += _check(data)
    if errs:
        raise SettingsError(source, errs)
    return Settings(
        japan_sales_companies=tuple(x.strip() for x in data["japan_sales_companies"]),
        internal_comment_excluded_sales_companies=tuple(x.strip() for x in data["internal_comment_excluded_sales_companies"]),
        batch_size=data["batch_size"],
        single_retry_max=data["single_retry_max"],
        failed_warn_ratio=float(data["failed_warn_ratio"]),
        sample_per_language=data["sample_per_language"],
        random_seed=data["random_seed"],
        usage_max_months=data["usage_max_months"],
        rare_code_min=data["rare_code_min"],
        keep_top_codes=data["keep_top_codes"],
        concentration_high_pct=float(data["concentration_high_pct"]),
        concentration_low_pct=float(data["concentration_low_pct"]),
        region_diff_note_pt=float(data["region_diff_note_pt"]),
        recent_months=data["recent_months"],
        recent_compare=data["recent_compare"],
        usage_bins=tuple(data["usage_bins"]),
        usage_split_months=data["usage_split_months"],
        lot_neighbor=data["lot_neighbor"],
        lot_spike_ratio=float(data["lot_spike_ratio"]),
        lot_min_n=data["lot_min_n"],
        lot_window=data["lot_window"],
        min_group_n=data["min_group_n"],
        alpha=float(data["alpha"]),
        min_diff_pt=float(data["min_diff_pt"]),
        min_code_n=data["min_code_n"],
        examples_per_finding=data["examples_per_finding"],
        summary_top_n=data["summary_top_n"],
        source=source,
        warnings=warnings,
    )


def load_settings(path: str | Path | None) -> Settings:
    """settings.yaml を読む。path が無い・存在しない場合は既定値。"""
    if path is None or not Path(path).exists():
        return parse_settings(None)
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as e:
        raise SettingsError(path, [f"YAML として読めません: {e}"])
    return parse_settings(raw, source=str(path))
