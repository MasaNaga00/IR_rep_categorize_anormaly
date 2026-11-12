"""組み合わせごとのデータ作成と、少数コードのまとめ(要件定義書 A-01〜A-04、G-20)。

分類済みの修理(1件 = dict)の主な項目:
  rec_key, repair_id, model, product_type, part_no, serial, received_date('YYYY-MM-DD'), sales_company,
  lang, classify_status('ok' / 'failed'), primary_code, info_level,
  failures: [{code, source, excerpt, excerpt_ja}], environment: [{factor, source, abnormal_use, excerpt}],
  lot_code, lot_order, mfg_ym, mfg_status, mfg_warning, usage_months, usage_status
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..codebook import Codebook
from ..constants import CLASSIFY_FAILED, CLASSIFY_FAILED_NAME, CLASSIFY_FAILED_STATUS

RARE_CODE = "OTHER_RARE"
RARE_NAME = "その他(少数)"
UNKNOWN_LABEL = "判定不能"
FAILED_LABEL = CLASSIFY_FAILED_NAME


def is_failed(r: dict) -> bool:
    """G-20: 分類不可(Dify の都合で分類結果が得られなかった)。"""
    return r.get("classify_status") == CLASSIFY_FAILED_STATUS or r.get("primary_code") == CLASSIFY_FAILED


def is_unknown(r: dict, unknown_code: str) -> bool:
    """G-20: 判定不能(読んだが故障の種類が読み取れない)。info_level が none、または主な故障が判定不能コード。"""
    return not is_failed(r) and (r.get("info_level") == "none" or r.get("primary_code") == unknown_code)


def failure_codes(r: dict) -> list[str]:
    """failures のコード(重複なし、出てきた順)。"""
    seen: list[str] = []
    for f in r.get("failures") or []:
        c = f.get("code")
        if c and c not in seen:
            seen.append(c)
    return seen


@dataclass
class ComboData:
    model: str
    part_no: str
    product_type: str
    records: list[dict]
    unknown_code: str
    valid: list[dict] = field(default_factory=list)       # 判定不能・分類不可を除く(A-02)
    unknown: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    raw_counts: Counter = field(default_factory=Counter)  # primary_code の件数(まとめる前。A-04)
    category_of: dict[str, str] = field(default_factory=dict)   # primary_code → 集計のカテゴリ
    categories: list[str] = field(default_factory=list)   # 集計のカテゴリ(件数の多い順。その他(少数)は最後)
    names: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.model, self.part_no)

    def category(self, r: dict) -> str:
        return self.category_of.get(r.get("primary_code"), RARE_CODE)

    def name(self, code: str) -> str:
        return self.names.get(code, code)


def build_combo(model: str, part_no: str, records: list[dict], codebook: Codebook, rare_code_min: int,
                keep_top: int = 0) -> ComboData:
    """A-04: rare_code_min 件未満のコードは「その他(少数)」にまとめる。ただし件数の多い上位 keep_top 種類は、
    少なくてもまとめない(keep_top_codes。2026-11-12 ユーザ決定)。"""
    product = records[0]["product_type"]
    unknown_code = codebook.code_by_role(product, "unknown")
    c = ComboData(model, part_no, product, records, unknown_code)
    for r in records:
        if is_failed(r):
            c.failed.append(r)
        elif is_unknown(r, unknown_code):
            c.unknown.append(r)
        else:
            c.valid.append(r)
    c.raw_counts = Counter(r["primary_code"] for r in c.valid)
    ranked = sorted(c.raw_counts, key=lambda code: (-c.raw_counts[code], code))
    frequent = [code for i, code in enumerate(ranked) if c.raw_counts[code] >= rare_code_min or i < keep_top]
    frequent.sort(key=lambda code: (-c.raw_counts[code], code))
    c.category_of = {code: (code if code in frequent else RARE_CODE) for code in c.raw_counts}
    c.categories = frequent + ([RARE_CODE] if any(v == RARE_CODE for v in c.category_of.values()) else [])
    names = {code: codebook.code_name(product, code) for code in c.raw_counts}
    for r in records:
        for code in failure_codes(r):
            names.setdefault(code, codebook.code_name(product, code))
    names[RARE_CODE] = RARE_NAME
    names[unknown_code] = codebook.code_name(product, unknown_code)
    names[CLASSIFY_FAILED] = CLASSIFY_FAILED_NAME
    c.names = names
    return c


def split_by_combo(records: list[dict], codebook: Codebook, rare_code_min: int, keep_top: int = 0) -> list[ComboData]:
    """A-01, I-03: 製品×部番ごとに分ける。件数の多い順(同数なら名前順)。"""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in records:
        groups[(r["model"], r["part_no"])].append(r)
    keys = sorted(groups, key=lambda k: (-len(groups[k]), k))
    return [build_combo(m, p, groups[(m, p)], codebook, rare_code_min, keep_top) for m, p in keys]
