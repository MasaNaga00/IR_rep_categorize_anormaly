"""注目点ごとの代表事例(要件定義書 T-08)。乱数の種を固定して抽出する。"""
from __future__ import annotations

import random

from ..analysis.findings import Comparison, Finding


def pick_examples(comp: Comparison, f: Finding, k: int, seed: int, combo_key: tuple[str, str]) -> list[dict]:
    """割合が高い側の群から、そのコードが記載された修理を k 件まで選ぶ(主な故障がそのコードの修理を優先)。"""
    group = comp.members.get(f.higher) or []
    primary = [r for r in group if r.get("primary_code") == f.code]
    others = [r for r in group if r.get("primary_code") != f.code and any(x.get("code") == f.code for x in r.get("failures") or [])]
    rng = random.Random(f"{seed}|{combo_key[0]}|{combo_key[1]}|{comp.label}|{f.code}")
    chosen = rng.sample(primary, min(k, len(primary)))
    if len(chosen) < k:
        chosen += rng.sample(others, min(k - len(chosen), len(others)))
    out = []
    for r in chosen:
        fx = next((x for x in r.get("failures") or [] if x.get("code") == f.code), {})
        out.append({"rec_key": r.get("rec_key"), "repair_id": r.get("repair_id"), "sales_company": r.get("sales_company"),
                    "lang": r.get("lang", ""), "source": fx.get("source", ""), "excerpt": fx.get("excerpt", ""),
                    "excerpt_ja": fx.get("excerpt_ja", "")})
    return out
