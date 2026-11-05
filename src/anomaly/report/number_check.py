"""要約文の数値の照合(要件定義書 T-05)。

要約文(Dify)に出てくる数値が、注目点の一覧(findings.json)と定型文のレポートのどちらかに含まれるかを調べる。
含まれない数値は、LLM が作った(計算し直した・丸めた・でっち上げた)可能性があるため、警告として示す。
"""
from __future__ import annotations

import re
import unicodedata

_NUM = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def canonical(token: str) -> str:
    x = float(token.replace(",", ""))
    return f"{x:g}" if abs(x) < 1e15 else token


def numbers_in(text: str) -> set[str]:
    t = unicodedata.normalize("NFKC", text or "")
    return {canonical(m.group(0)) for m in _NUM.finditer(t)}


def _leaves(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _leaves(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _leaves(v)
    else:
        yield obj


def allowed_numbers(findings: dict, report_text: str) -> set[str]:
    allowed = numbers_in(report_text)
    for v in _leaves(findings):
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            allowed.add(canonical(str(v)))
            allowed.add(canonical(f"{v:.1f}"))
        elif isinstance(v, str):
            allowed |= numbers_in(v)
    return allowed


def unmatched_numbers(summary: str, findings: dict, report_text: str) -> list[str]:
    allowed = allowed_numbers(findings, report_text)
    return sorted(numbers_in(summary) - allowed, key=lambda s: float(s))
