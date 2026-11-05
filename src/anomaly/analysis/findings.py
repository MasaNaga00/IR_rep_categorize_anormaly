"""注目点の判定(要件定義書 A-14〜A-14b)。

注目点 = 次をすべて満たす差:
  1. 比べる2群の件数がどちらも min_group_n 以上(ロットの比較では、そのロットが lot_min_n 以上、他のロットが min_group_n 以上)
  2. 補正後の p 値 < alpha
  3. 割合の差が min_diff_pt 以上、かつ割合が高い側の群でそのコードの件数が min_code_n 以上
「その他(少数)」は複数のコードの寄せ集めのため、注目点にしない。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..settings import Settings
from .prepare import RARE_CODE
from .stats import CategoryResult, TwoGroupResult

NOTABLE = "notable"
NO_DIFFERENCE = "no_difference"
INSUFFICIENT = "insufficient"
NOT_AVAILABLE = "not_available"      # 比べるデータが無い(製造年月の情報なし など)
JUDGEMENT_NAMES = {
    NOTABLE: "注目点あり",
    NO_DIFFERENCE: "目立った差なし",
    INSUFFICIENT: "件数不足",
    NOT_AVAILABLE: "情報なし",
}


@dataclass
class Finding:
    code: str
    name: str
    a: int
    na: int
    pct_a: float
    b: int
    nb: int
    pct_b: float
    diff_pt: float
    ratio: float | None
    p_adj: float

    @property
    def higher(self) -> str:
        return "a" if self.diff_pt > 0 else "b"

    def as_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "a_count": self.a, "a_n": self.na, "a_pct": round(self.pct_a, 1),
                "b_count": self.b, "b_n": self.nb, "b_pct": round(self.pct_b, 1), "diff_pt": round(self.diff_pt, 1),
                "ratio": None if self.ratio is None else round(self.ratio, 2), "p_adj": self.p_adj}


@dataclass
class Comparison:
    aspect: str                       # recent / usage / lot
    label: str                        # 比較の名前
    group_a: str
    group_b: str
    result: TwoGroupResult | None
    judgement: str = NOT_AVAILABLE
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    profiles: dict = field(default_factory=dict)   # 群ごとの構成(A-22)
    lot_code: str | None = None
    lot_kind: str | None = None                       # ロットの比較: "single"(1ロット)/ "window"(隣り合うロットのまとまり)
    members: dict = field(default_factory=dict, repr=False)   # 群ごとの修理("a" / "b")。代表事例(T-08)に使う

    @property
    def na(self) -> int:
        return self.result.na if self.result else 0

    @property
    def nb(self) -> int:
        return self.result.nb if self.result else 0

    def as_dict(self) -> dict:
        return {
            "aspect": self.aspect, "label": self.label,
            "group_a": {"name": self.group_a, "n": self.na}, "group_b": {"name": self.group_b, "n": self.nb},
            "judgement": self.judgement,
            "chi2": None if self.result is None else {"p": self.result.chi2_p, "reference": self.result.chi2_reference},
            "findings": [f.as_dict() for f in self.findings],
            "notes": list(self.notes),
            **({"lot_code": self.lot_code} if self.lot_code is not None else {}),
            **({"lot_kind": self.lot_kind} if self.lot_kind is not None else {}),
        }


def is_notable(cr: CategoryResult, s: Settings) -> bool:
    if cr.code == RARE_CODE or cr.p_adj is None:
        return False
    higher_count = cr.a if cr.diff_pt > 0 else cr.b
    return cr.p_adj < s.alpha and abs(cr.diff_pt) >= s.min_diff_pt and higher_count >= s.min_code_n


def judge(comp: Comparison, s: Settings, size_ok: bool, names: dict[str, str]) -> Comparison:
    """A-14, A-14a: 判定を comp に書き込んで返す。"""
    if comp.result is None:
        comp.judgement = NOT_AVAILABLE
        return comp
    if not size_ok:
        comp.judgement = INSUFFICIENT
        return comp
    comp.findings = [
        Finding(cr.code, names.get(cr.code, cr.code), cr.a, cr.na, cr.pct_a, cr.b, cr.nb, cr.pct_b, cr.diff_pt,
                cr.ratio, cr.p_adj)
        for cr in comp.result.categories if is_notable(cr, s)
    ]
    comp.findings.sort(key=lambda f: -abs(f.diff_pt))
    comp.judgement = NOTABLE if comp.findings else NO_DIFFERENCE
    return comp


def aspect_judgement(comps: list[Comparison]) -> str:
    """観点全体の判定(ロットのように比較が複数ある場合)。"""
    js = {c.judgement for c in comps}
    if NOTABLE in js:
        return NOTABLE
    if NO_DIFFERENCE in js:
        return NO_DIFFERENCE
    if INSUFFICIENT in js:
        return INSUFFICIENT
    return NOT_AVAILABLE
