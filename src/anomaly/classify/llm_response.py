"""LLM 応答のパースと検証(要件定義書 G-06、G-08、G-28、G-29)。

パースは v2(IR_rep_categorize_v2)の llm_response.py と同じ方法(R-33):
`{"results": [...]}`、配列そのもの、```json で囲まれた文字列 のどれでも受け付ける。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from ..codebook import Codebook
from ..constants import INFO_LEVELS, SOURCE_VALUES

ENV_SOURCE_VALUES = ("user", "repair", "both")


class LLMParseError(Exception):
    pass


@dataclass
class ValidationIssue:
    rec_key: str
    kind: str
    message: str
    severity: str = "error"     # "error" / "warning"
    batch_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract(obj):
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("results", "data", "classifications", "records"):
            if isinstance(obj.get(key), list):
                return obj[key]
    raise LLMParseError("応答に results の配列がありません")


def parse_llm_response(raw) -> list[dict]:
    """G-28"""
    if isinstance(raw, (dict, list)):
        items = _extract(raw)
    else:
        text = (raw or "").strip()
        if not text:
            raise LLMParseError("応答が空です")
        candidates = [text] + [m.group(1).strip() for m in _FENCE.finditer(text)]
        for start, end in (("{", "}"), ("[", "]")):
            i, j = text.find(start), text.rfind(end)
            if 0 <= i < j:
                candidates.append(text[i:j + 1])
        items = None
        for c in candidates:
            try:
                obj = json.loads(c)
            except (json.JSONDecodeError, ValueError):
                continue
            try:
                items = _extract(obj)
                break
            except LLMParseError:
                continue
        if items is None:
            raise LLMParseError("応答を JSON として読めません")
    if not all(isinstance(x, dict) for x in items):
        raise LLMParseError("results の要素が辞書ではありません")
    return items


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _str(v) -> str:
    return "" if v is None else str(v)


def validate_item(item: dict, request_record: dict, product_id: str, codebook: Codebook,
                  batch_id: str = "") -> tuple[dict | None, list[ValidationIssue]]:
    """G-29: 1修理分の結果を検証する。エラーが無ければ正規化した結果を返す(エラーがあれば None)。

    正規化した結果: {"failures": [...], "primary_code", "environment": [...], "info_level"}
    """
    key = request_record["rec_key"]
    issues: list[ValidationIssue] = []

    def add(kind, msg, sev="error"):
        issues.append(ValidationIssue(key, kind, msg, sev, batch_id))

    source_text = _squash(" ".join(_str(v) for k, v in request_record.items() if k != "rec_key"))
    unknown = codebook.code_by_role(product_id, "unknown")
    factors = set(codebook.environment_factors(product_id))

    failures, seen = [], set()
    raw_fails = item.get("failures") or []
    if not isinstance(raw_fails, list):
        add("invalid_enum", "failures が配列ではありません")
        raw_fails = []
    for f in raw_fails:
        if not isinstance(f, dict):
            add("invalid_enum", "failures の要素が辞書ではありません")
            continue
        code, src = _str(f.get("code")).strip(), _str(f.get("source")).strip()
        if not codebook.is_valid_code(product_id, code):
            add("unknown_code", f"コード '{code}' は {product_id} のコード体系にありません")
            continue
        if src not in SOURCE_VALUES:
            add("invalid_enum", f"source '{src}' は定義にありません({' / '.join(SOURCE_VALUES)})")
            continue
        if code in seen:
            add("duplicate_code", f"コード '{code}' が2回あります(1つにまとめます)", "warning")
            continue
        seen.add(code)
        ex = _str(f.get("excerpt"))
        if ex and _squash(ex) not in source_text:
            add("excerpt_not_found", f"抜き出し '{ex[:30]}' が原文にありません", "warning")
        failures.append({"code": code, "source": src, "excerpt": ex, "excerpt_ja": _str(f.get("excerpt_ja"))})

    info = _str(item.get("info_level")).strip()
    if info not in INFO_LEVELS:
        add("invalid_enum", f"info_level '{info}' は定義にありません({' / '.join(INFO_LEVELS)})")

    primary = _str(item.get("primary_code")).strip()
    if primary != unknown and primary not in seen:
        add("primary_mismatch", f"primary_code '{primary}' が failures にも判定不能コードにも当てはまりません")

    env = []
    raw_env = item.get("environment") or []
    if not isinstance(raw_env, list):
        add("invalid_enum", "environment が配列ではありません")
        raw_env = []
    for e in raw_env:
        if not isinstance(e, dict):
            add("invalid_enum", "environment の要素が辞書ではありません")
            continue
        fac, src = _str(e.get("factor")).strip(), _str(e.get("source")).strip()
        if fac not in factors:
            add("invalid_enum", f"factor '{fac}' は定義にありません")
            continue
        if src not in ENV_SOURCE_VALUES:
            add("invalid_enum", f"environment の source '{src}' は定義にありません")
            continue
        env.append({"factor": fac, "source": src, "abnormal_use": bool(e.get("abnormal_use")), "excerpt": _str(e.get("excerpt"))})

    if any(i.severity == "error" for i in issues):
        return None, issues
    return {"failures": failures, "primary_code": primary, "environment": env, "info_level": info}, issues
