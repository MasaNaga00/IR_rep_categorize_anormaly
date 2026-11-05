"""Dify へのリクエストの組み立て(要件定義書 G-11、G-12、G-25、G-26)。"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..codebook import Codebook


@dataclass
class Batch:
    batch_id: str               # 例: "001"、1件ずつの再試行は "001_r1_000123"
    product_id: str
    rec_keys: list[str]
    request_records: list[dict]
    inputs: dict


def request_record(rec_key: str, comments: dict[str, str]) -> dict:
    """G-25: records の1要素。渡さないコメントは、comments にキーが無い(llm_comments で省いてある)。"""
    return {"rec_key": rec_key, **comments}


def make_inputs(product_id: str, request_records: list[dict], codebook: Codebook) -> dict:
    return {
        "product_name": codebook.product_name(product_id),
        "code_list": codebook.code_list_text(product_id),
        "records": json.dumps(request_records, ensure_ascii=False),
    }


def build_batches(items: list[tuple[dict, dict]], codebook: Codebook, batch_size: int) -> list[Batch]:
    """G-26: 製品種別ごとに batch_size 件ずつ。items は (修理, LLM に渡すコメント) の組。"""
    if batch_size < 1:
        raise ValueError("batch_size は 1 以上にしてください")
    by_product: dict[str, list[tuple[dict, dict]]] = {}
    for rec, comments in items:
        by_product.setdefault(rec["product_type"], []).append((rec, comments))
    out: list[Batch] = []
    n = 1
    for pid in sorted(by_product):
        chunk_all = by_product[pid]
        for i in range(0, len(chunk_all), batch_size):
            chunk = chunk_all[i:i + batch_size]
            rrs = [request_record(r["rec_key"], c) for r, c in chunk]
            out.append(Batch(f"{n:03d}", pid, [r["rec_key"] for r, _ in chunk], rrs, make_inputs(pid, rrs, codebook)))
            n += 1
    return out


def single_batch(batch_id: str, product_id: str, rr: dict, codebook: Codebook) -> Batch:
    """G-19: 1件ずつの再試行用。"""
    return Batch(batch_id, product_id, [rr["rec_key"]], [rr], make_inputs(product_id, [rr], codebook))
