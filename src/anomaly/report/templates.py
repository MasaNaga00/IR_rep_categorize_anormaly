"""定型文(説明文の層1)(要件定義書 T-02、T-04、T-07)。

- 判定(A-14a)に応じた決まった文型で書く。原因の推測はしない。
- 「増加」「多発」などの語は使わず、「割合が高い」「構成比が異なる」と書く(T-04)。
"""
from __future__ import annotations

from ..analysis.findings import INSUFFICIENT, NO_DIFFERENCE, NOT_AVAILABLE, NOTABLE, Comparison
from ..settings import Settings

COMPOSITION_NOTE = ("注記: 割合(構成比)の比較のため、ある故障の割合が高い群では、ほかの故障の割合が低くなります。"
                    "低くなっている故障は、その影響の可能性があります。")
FORBIDDEN_WORDS = ("増加", "多発", "急増", "激増", "悪化")   # T-04: 定型文で使わない語(テストで確認する)


def p1(x: float) -> str:
    return f"{x:.1f}%"


def comparison_sentences(comp: Comparison, s: Settings, min_a: int | None = None) -> list[str]:
    """A-14a の判定に応じた文。min_a はロットの比較で、そのロット側に必要な件数。"""
    a, b, na, nb = comp.group_a, comp.group_b, comp.na, comp.nb
    if comp.judgement == NOT_AVAILABLE:
        return ["比べるデータがありません。"]
    if comp.judgement == INSUFFICIENT:
        need_a = min_a if min_a is not None else s.min_group_n
        short = []
        if na < need_a:
            short.append(f"{a}が {na} 件")
        if nb < s.min_group_n:
            short.append(f"{b}が {nb} 件")
        detail = "、".join(short) if short else f"{a}が {na} 件、{b}が {nb} 件"
        return [f"{detail}と少ないため(比較には各 {s.min_group_n} 件以上が必要)、比較による判断はできません。"]
    if comp.judgement == NO_DIFFERENCE:
        return [f"{a}(n={na})と{b}(n={nb})で、故障の種類の構成に目立った差は見られません。"]
    out = []
    for f in comp.findings:
        direction = "高く" if f.diff_pt > 0 else "低く"
        out.append(f"{a}では『{f.name}』の割合が {p1(f.pct_a)}({f.a}/{f.na}件)で、"
                   f"{b}の {p1(f.pct_b)}({f.b}/{f.nb}件)より {abs(f.diff_pt):.1f} ポイント{direction}なっています。")
    out.append("上記以外の故障の種類には、目立った差は見られません。")
    if any(f.diff_pt > 0 for f in comp.findings) and any(f.diff_pt < 0 for f in comp.findings):
        out.append(COMPOSITION_NOTE)
    return out


def note_sentences(notes: list[str]) -> list[str]:
    return [f"注記: {n}" for n in notes]


def _lot_group_sentences(comps: list[Comparison], s: Settings, what: str) -> list[str]:
    out = []
    tested = [c for c in comps if c.judgement in (NOTABLE, NO_DIFFERENCE)]
    if not tested:
        return out
    notable = [c for c in comps if c.judgement == NOTABLE]
    if not notable:
        out.append(f"件数が {s.lot_min_n} 件以上の{what} {len(tested)} 個について、それぞれ他のロットと比べましたが、"
                   "故障の種類の構成に目立った偏りは見られません。")
        return out
    out.append(f"件数が {s.lot_min_n} 件以上の{what} {len(tested)} 個のうち、{len(notable)} 個で、"
               "故障の種類の構成に他のロットとの差が見られます。")
    for c in notable:
        out.extend(x for x in comparison_sentences(c, s, min_a=s.lot_min_n)
                   if not x.startswith("上記以外") and x != COMPOSITION_NOTE)
    if any(any(f.diff_pt > 0 for f in c.findings) and any(f.diff_pt < 0 for f in c.findings) for c in notable):
        out.append(COMPOSITION_NOTE)
    return out


def lot_summary_sentences(comps: list[Comparison], judgement: str, s: Settings, spikes: list[tuple[str, int]]) -> list[str]:
    """A-15〜A-15d: 1ロットごとの比較と、隣り合うロットのまとまりの比較。"""
    if judgement == NOT_AVAILABLE:
        return ["製造ロットの情報(シリアルの変換結果)が無いため、ロットの解析はできません。"]
    singles = [c for c in comps if c.lot_kind != "window"]
    windows = [c for c in comps if c.lot_kind == "window"]
    out = _lot_group_sentences(singles, s, "ロット")
    if s.lot_window > 1:
        out += _lot_group_sentences(windows, s, f"まとまり(製造の順で隣り合う {s.lot_window} ロットずつ)")
    if not out:
        out.append(f"件数が {s.lot_min_n} 件以上のロット(まとまり)が無い、または比べる他のロットの件数が足りないため、"
                   "ロットごとの比較による判断はできません。")
    if spikes:
        lst = "、".join(f"{name}({n}件)" for name, n in spikes)
        out.append(f"参考: 製造の順で前後のロットと比べて、修理の件数が多いロットがあります: {lst}。"
                   "ただし、生産台数のデータがないため、件数の偏りが生産量によるものかは判断できません。")
    return out
