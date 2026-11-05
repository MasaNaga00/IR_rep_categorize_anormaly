"""2つの群の比較(要件定義書 A-09)。

- 全体の構成の差: カイ二乗検定。期待度数 5 未満のセルが 20% を超える場合は「参考」の印を付ける。
- コードごとの差: 2×2 のフィッシャーの正確検定(両側)と、Benjamini-Hochberg の補正。
- 差の大きさ: 割合の差(ポイント)と比。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from scipy import stats as _st


def bh_adjust(pvalues: list[float | None]) -> list[float | None]:
    """Benjamini-Hochberg の補正。None はそのまま None。"""
    idx = [i for i, p in enumerate(pvalues) if p is not None]
    m = len(idx)
    out: list[float | None] = [None] * len(pvalues)
    if not m:
        return out
    order = sorted(idx, key=lambda i: pvalues[i])
    adj = [0.0] * m
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, pvalues[i] * m / rank)
        adj[rank - 1] = min(prev, 1.0)
    for rank, i in enumerate(order, start=1):
        out[i] = adj[rank - 1]
    return out


def fisher_p(a: int, na: int, b: int, nb: int) -> float | None:
    if na <= 0 or nb <= 0:
        return None
    return float(_st.fisher_exact([[a, na - a], [b, nb - b]], alternative="two-sided")[1])


@dataclass
class CategoryResult:
    code: str
    a: int
    b: int
    na: int
    nb: int
    p_raw: float | None = None
    p_adj: float | None = None

    @property
    def pct_a(self) -> float:
        return 100.0 * self.a / self.na if self.na else 0.0

    @property
    def pct_b(self) -> float:
        return 100.0 * self.b / self.nb if self.nb else 0.0

    @property
    def diff_pt(self) -> float:
        return self.pct_a - self.pct_b

    @property
    def ratio(self) -> float | None:
        return self.pct_a / self.pct_b if self.pct_b else None


@dataclass
class TwoGroupResult:
    na: int
    nb: int
    chi2_p: float | None
    chi2_reference: bool
    categories: list[CategoryResult] = field(default_factory=list)

    def by_code(self) -> dict[str, CategoryResult]:
        return {c.code: c for c in self.categories}


def chi2_test(rows: list[list[int]]) -> tuple[float | None, bool]:
    """群 × カテゴリの表のカイ二乗検定。検定できない場合は (None, False)。"""
    cols = [j for j in range(len(rows[0])) if sum(r[j] for r in rows)] if rows and rows[0] else []
    rows = [[r[j] for j in cols] for r in rows if sum(r[j] for j in cols)]
    if len(rows) < 2 or len(cols) < 2:
        return None, False
    _, p, _, expected = _st.chi2_contingency(rows)
    cells = [e for row in expected for e in row]
    reference = sum(1 for e in cells if e < 5) / len(cells) > 0.2
    return float(p), reference


def compare(cats_a: list[str], cats_b: list[str], categories: list[str], adjust: bool = True) -> TwoGroupResult:
    """cats_a / cats_b: 各修理のカテゴリ。categories: 表に出すカテゴリの順番。"""
    ca, cb = Counter(cats_a), Counter(cats_b)
    na, nb = len(cats_a), len(cats_b)
    p, ref = chi2_test([[ca[c] for c in categories], [cb[c] for c in categories]])
    res = TwoGroupResult(na, nb, p, ref)
    for c in categories:
        res.categories.append(CategoryResult(c, ca[c], cb[c], na, nb, fisher_p(ca[c], na, cb[c], nb)))
    if adjust:
        for cr, adj in zip(res.categories, bh_adjust([cr.p_raw for cr in res.categories])):
            cr.p_adj = adj
    return res
