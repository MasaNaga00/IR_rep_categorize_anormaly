"""Tableau 用の縦持ち CSV(要件定義書 O-03)。

failures の1件を1行とする。failures が空の修理も1行出し、コードは判定不能コードまたは CLASSIFY_FAILED。
is_primary(主な故障か)の列で、主な故障だけの集計もできる。
"""
from __future__ import annotations

from pathlib import Path

from ..analysis.aspects import bin_labels, bin_of
from ..analysis.prepare import is_failed
from ..codebook import Codebook
from ..constants import CLASSIFY_FAILED, CLASSIFY_FAILED_NAME
from ..settings import Settings
from .files import write_csv

FIELDS = ("rec_key", "repair_id", "model", "part_no", "product_type", "sales_company", "received_date", "received_ym",
          "lang", "classify_status", "info_level", "primary_code", "primary_name", "code", "code_name", "source",
          "is_primary", "mfg_ym", "lot_code", "lot_order", "mfg_status", "usage_months", "usage_status", "usage_bin")


def tableau_rows(records: list[dict], codebook: Codebook, s: Settings):
    labels = bin_labels(s.usage_bins)
    for r in records:
        p = r["product_type"]
        failed = is_failed(r)
        primary = CLASSIFY_FAILED if failed else (r.get("primary_code") or codebook.code_by_role(p, "unknown"))
        pname = CLASSIFY_FAILED_NAME if failed else codebook.code_name(p, primary)
        base = {k: r.get(k) for k in FIELDS if k in r}
        base.update({"received_ym": r["received_date"][:7], "primary_code": primary, "primary_name": pname,
                     "usage_bin": labels[bin_of(r["usage_months"], s.usage_bins)]
                     if r.get("usage_status") == "ok" and r.get("usage_months") is not None else ""})
        fs = [] if failed else (r.get("failures") or [])
        if not fs:
            yield {**base, "code": primary, "code_name": pname, "source": "", "is_primary": True}
            continue
        for f in fs:
            yield {**base, "code": f.get("code"), "code_name": codebook.code_name(p, f.get("code")),
                   "source": f.get("source", ""), "is_primary": f.get("code") == primary}


def write_tableau(path: Path, records: list[dict], codebook: Codebook, s: Settings) -> Path:
    return write_csv(path, FIELDS, tableau_rows(records, codebook, s))
