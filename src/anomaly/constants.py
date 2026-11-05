"""共通の定数(要件定義書 docs/20261105_requirements.md)。"""
from __future__ import annotations

# --- コード体系(codebook.py が使う。v2 と同じ) ---
RESPONSIBILITY_VALUES = ("manufacturer", "user_or_unknown")
RECORD_TYPES = ("failure", "service", "unknown")
REQUIRED_ROLES = ("connected_device", "other", "unknown")
SPECIAL_ENV_KEYS = ("none", "unknown")  # LLM 出力では使わない

# --- 分類(G-06, G-20) ---
CLASSIFY_FAILED = "CLASSIFY_FAILED"      # 分類不可の primary_code(コード体系のコードと混ぜない)
CLASSIFY_FAILED_NAME = "分類不可"
SOURCE_VALUES = ("user", "repair", "internal")
INFO_LEVELS = ("sufficient", "thin", "none")
CLASSIFY_OK = "ok"
CLASSIFY_FAILED_STATUS = "failed"

# --- 言語(G-02)。判定の優先順 ---
LANG_VALUES = ("ko", "ja", "zh", "en", "none")

# --- コメントの列(I-01) ---
COMMENT_FIELDS = ("user_comment", "repair_comment", "internal_1", "internal_2")
INTERNAL_FIELDS = ("internal_1", "internal_2")

# --- 内部コメントの渡し方(G-30) ---
INTERNAL_MODES = ("default", "all", "none")

# --- シリアル変換結果(S-10〜S-14)。変換は別ツール(docs/20261105_serial-converter-requirements.md) ---
MFG_RESULT_COLUMNS = ("serial_normalized", "lot_code", "lot_order", "mfg_ym", "mfg_status", "mfg_warning")
MFG_STATUS_VALUES = ("ok", "prefix_only", "no_serial", "no_model_type", "not_in_table", "invalid")  # 変換ツールの SC-34
MFG_NOT_CONVERTED = "not_converted"     # 結合できなかった修理(この解析だけで使う値)
MFG_WARNING_VALUES = ("no_reference_date", "after_reference", "serial_was_number")  # 変換ツールの SC-35

SEP = ";"                                # 複数の値を連結する区切り
