"""環境要因(使用環境・取り扱い)で修理を分ける(要件定義書 A-47)。

- 「該当なし」「不明」(コード体系の特別な要因)は、環境要因の記述として数えない。
- env_exclude_mode = user_caused(既定): ユーザ起因とみなせる記述がある修理を除く。
    コード体系で always_user_caused の要因(落下・衝撃、水濡れ)は常に、それ以外は abnormal_use(通常では考えづらい使用)のときだけ。
  env_exclude_mode = any: 6つの要因のどれかの記述がある修理をすべて除く。
- 誰の記述か(ユーザ / 修理者 / 両方)は問わない(ユーザだけの記述でも除く。2026-11-12 ユーザ決定)。
"""
from __future__ import annotations

from collections import Counter

SPECIAL_FACTORS = ("none", "unknown")
ENV_EXCLUDE_MODES = ("user_caused", "any")


def env_entries(r: dict) -> list[dict]:
    """環境要因の記述(「該当なし」「不明」を除く)。"""
    return [e for e in (r.get("environment") or []) if e.get("factor") and e["factor"] not in SPECIAL_FACTORS]


def has_env(r: dict) -> bool:
    return bool(env_entries(r))


def exclusion_factors(r: dict, factors: dict, mode: str) -> list[str]:
    """この修理を除く理由になった要因(重複なし、出てきた順)。空なら除かない。factors: Codebook.environment_factors()。"""
    out: list[str] = []
    for e in env_entries(r):
        f = e["factor"]
        info = factors.get(f)
        if mode == "any" or (info is not None and info.always_user_caused) or bool(e.get("abnormal_use")):
            if f not in out:
                out.append(f)
    return out


def split_by_environment(records: list[dict], factors: dict, mode: str) -> tuple[list[dict], list[dict], list[tuple[str, int]]]:
    """(残す修理, 除く修理, [(要因の名前, 件数)])。1件に複数の要因がある場合は、要因ごとに数える。"""
    kept, removed, by = [], [], Counter()
    for r in records:
        fs = exclusion_factors(r, factors, mode)
        if fs:
            removed.append(r)
            by.update(fs)
        else:
            kept.append(r)
    named = [(factors[f].name if f in factors else f, n) for f, n in sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))]
    return kept, removed, named
