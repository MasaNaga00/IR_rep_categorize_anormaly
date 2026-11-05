"""入力 CSV の読み込み(要件定義書 I-01〜I-06、G-12)。

- 1行 = 1修理。修理IDが重複していても別の修理として扱い、行ごとに内部キー rec_key を付ける(I-02)。
- 必須の値が無い行、受付日が読めない行、コード体系に無い製品種別の行は除外し、理由を記録する(I-05)。
"""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

from .constants import COMMENT_FIELDS, INTERNAL_FIELDS

DEFAULT_COLUMNS = {
    "repair_id": "ID",
    "model": "製品名",
    "product_type": "事業コード",
    "part_no": "部品番号",
    "serial": "シリアル",
    "received_date": "受付日",
    "sales_company": "販社名",
    "user_comment": "user_comment",
    "repair_comment": "repair_comment",
    "internal_1": "Internal_1",
    "internal_2": "Internal_2",
}
FIELDS = tuple(DEFAULT_COLUMNS)
# 列の対応づけに書けるが、入力 CSV の列ではない項目(S-10: シリアル変換結果ファイルの列名)。書かなければ None
RESULT_COLUMN_KEYS = ("serial_result_model", "serial_result_reference_date")
OPTIONAL_COLUMNS = ("serial",)
REQUIRED_COLUMNS = tuple(k for k in FIELDS if k not in OPTIONAL_COLUMNS)
KEY_FIELDS = ("repair_id", "model", "product_type", "part_no", "sales_company")

# I-05: 除外の理由(判定する順)
REQUIRED_VALUES = ("repair_id", "model", "product_type", "part_no", "sales_company", "received_date")
EXCLUDE_REASONS = tuple(f"missing_{k}" for k in REQUIRED_VALUES) + ("invalid_received_date", "unknown_product")
EXCLUDE_REASON_NAMES = {
    "missing_repair_id": "修理IDが空",
    "missing_model": "製品名が空",
    "missing_product_type": "製品種別が空",
    "missing_part_no": "部品番号が空",
    "missing_sales_company": "販社名が空",
    "missing_received_date": "受付日が空",
    "invalid_received_date": "受付日が読めない",
    "unknown_product": "コード体系に無い製品種別",
}

_DATE_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T].*)?$")
_EXCEL_RE = re.compile(r"^\d{4,6}(?:\.\d+)?$")
_EXCEL_EPOCH = date(1899, 12, 30)
_EXCEL_MIN, _EXCEL_MAX = 20000, 80000   # 1954年〜2119年。これ以外の数字は日付とみなさない


class InputError(ValueError):
    """入力ファイル自体の問題(列が無いなど)。処理を続けられない。"""


@dataclass
class LoadResult:
    records: list[dict] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    total_rows: int = 0
    has_serial_column: bool = True

    def excluded_counts(self) -> dict[str, int]:
        c = Counter(e["reason"] for e in self.excluded)
        return {r: c[r] for r in EXCLUDE_REASONS if c[r]}

    def duplicate_repair_ids(self) -> dict[str, int]:
        """I-02: 読み込んだ修理のうち、修理IDが2行以上ある ID → 行数。"""
        c = Counter(r["repair_id"] for r in self.records)
        return {k: v for k, v in c.items() if v > 1}

    def duplicate_id_rows(self) -> int:
        return sum(self.duplicate_repair_ids().values())

    def combos(self) -> Counter:
        """I-03: (製品名, 部品番号) → 件数。"""
        return Counter((r["model"], r["part_no"]) for r in self.records)


def parse_date(value: str | None) -> date | None:
    """I-04: YYYY-MM-DD、YYYY/MM/DD、YYYY/M/D(時刻付きも可)、Excel の日付シリアル値。読めなければ None。"""
    s = unicodedata.normalize("NFKC", value or "").strip()
    if not s:
        return None
    m = _DATE_RE.match(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    if _EXCEL_RE.match(s):
        n = float(s)
        if _EXCEL_MIN <= n <= _EXCEL_MAX:
            return _EXCEL_EPOCH + timedelta(days=int(n))
    return None


def normalize_serial(value: str | None) -> str:
    """I-06: 全角→半角(NFKC)、前後の空白を除き、英字を大文字にそろえる。"""
    return unicodedata.normalize("NFKC", value or "").strip().upper()


def load_column_config(path: str | Path | None) -> dict:
    """input_columns.yaml を読む。知らない項目・空の列名はエラー。

    返す辞書には、入力 CSV の列(DEFAULT_COLUMNS のキー)と、RESULT_COLUMN_KEYS(未指定なら None)が入る。
    """
    cols: dict = dict(DEFAULT_COLUMNS)
    cols.update({k: None for k in RESULT_COLUMN_KEYS})
    if path is not None and Path(path).exists():
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig")) or {}
        if not isinstance(data, dict):
            raise ValueError("列の対応づけの最上位が辞書ではありません")
        errs = []
        for k, v in data.items():
            if k not in DEFAULT_COLUMNS and k not in RESULT_COLUMN_KEYS:
                errs.append(f"不明な項目があります: {k}")
            elif v is None or not str(v).strip():
                errs.append(f"{k}: 列名が空です")
            else:
                cols[k] = str(v).strip()
        if errs:
            raise ValueError(" / ".join(errs))
    return cols


def load_input(path: str | Path, product_ids, columns: dict | None = None) -> LoadResult:
    """入力 CSV を読む。product_ids はコード体系の製品種別(Codebook.product_ids())。"""
    cols = dict(DEFAULT_COLUMNS)
    cols.update({k: v for k, v in (columns or {}).items() if k in DEFAULT_COLUMNS})
    products = set(product_ids)
    res = LoadResult()
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = [h.strip() for h in (reader.fieldnames or [])]
        reader.fieldnames = header
        missing = [cols[k] for k in REQUIRED_COLUMNS if cols[k] not in header]
        if missing:
            raise InputError(f"入力 CSV に必須の列がありません: {', '.join(missing)}")
        res.has_serial_column = cols["serial"] in header
        for n, row in enumerate(reader, start=1):
            res.total_rows = n
            rec = {"rec_key": f"{n:06d}", "row_number": n}
            for k in KEY_FIELDS:
                rec[k] = (row.get(cols[k]) or "").strip()
            rec["serial"] = normalize_serial(row.get(cols["serial"])) if res.has_serial_column else ""
            raw_date = (row.get(cols["received_date"]) or "").strip()
            for k in COMMENT_FIELDS:
                rec[k] = row.get(cols[k]) or ""

            reason = next((f"missing_{k}" for k in REQUIRED_VALUES if not (raw_date if k == "received_date" else rec[k])), None)
            d = None
            if reason is None:
                d = parse_date(raw_date)
                if d is None:
                    reason = "invalid_received_date"
            if reason is None and rec["product_type"] not in products:
                reason = "unknown_product"
            if reason is not None:
                res.excluded.append({"rec_key": rec["rec_key"], "row_number": n, "repair_id": rec["repair_id"],
                                     "reason": reason, "value": raw_date if "date" in reason else ""})
                continue
            rec["received_date"] = d.isoformat()
            res.records.append(rec)
    return res


def llm_comments(record: dict, internal_excluded: bool) -> dict[str, str]:
    """G-12, G-25: LLM に渡すコメント。内部コメントを渡さない場合はキーごと省く。"""
    return {k: record.get(k) or "" for k in COMMENT_FIELDS if not (internal_excluded and k in INTERNAL_FIELDS)}


def has_text(comments: dict[str, str]) -> bool:
    """G-11: 渡すコメントに1文字でも中身があるか(空白だけは空とみなす)。"""
    return any((v or "").strip() for v in comments.values())
