"""分類の実行: バッチ → 1件ずつの再試行 → 分類不可(要件定義書 G-11、G-16〜G-23、G-02、U-05)。

各修理に次を書き込む(records を書き換える):
  lang, classify_status('ok' / 'failed'), failed_reason, primary_code, primary_name, info_level, failures, environment
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..codebook import Codebook
from ..constants import CLASSIFY_FAILED, CLASSIFY_FAILED_NAME, CLASSIFY_FAILED_STATUS, CLASSIFY_OK
from ..dify_client import DifyAPIError
from ..input_loader import has_text, llm_comments
from ..lang import detect_lang
from ..settings import Settings
from .cache import ClassifyCache, record_key
from .llm_request import Batch, build_batches, single_batch
from .llm_response import LLMParseError, ValidationIssue, parse_llm_response, validate_item


@dataclass
class ClassifyStats:
    total: int = 0
    no_text: int = 0           # G-11: Dify に送らず判定不能
    cached: int = 0            # G-16: キャッシュから
    sent: int = 0              # Dify に送った修理
    batches: int = 0
    calls: int = 0             # Dify の呼び出し回数(再試行を含む)
    retried: int = 0           # 1件ずつ送り直した修理
    ok: int = 0
    failed: int = 0
    tokens: int = 0
    failed_reasons: Counter = field(default_factory=Counter)

    def failed_ratio(self) -> float:
        return self.failed / self.total if self.total else 0.0

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("total", "no_text", "cached", "sent", "batches", "calls", "retried", "ok", "failed", "tokens")}
        d["failed_reasons"] = dict(self.failed_reasons)
        return d

    def summary_lines(self) -> list[str]:
        lines = [f"分類: 全 {self.total} 件(Dify に送った {self.sent} 件、キャッシュ {self.cached} 件、"
                 f"コメントが空欄 {self.no_text} 件)",
                 f"  結果: 分類できた {self.ok} 件、分類不可 {self.failed} 件"
                 f"(Dify の呼び出し {self.calls} 回、1件ずつ送り直した修理 {self.retried} 件)"]
        if self.failed_reasons:
            lines.append("  分類不可の理由: " + ", ".join(f"{k} {v}" for k, v in sorted(self.failed_reasons.items())))
        return lines


@dataclass
class ClassifyResult:
    stats: ClassifyStats
    issues: list[ValidationIssue]


def _set_ok(rec: dict, result: dict, codebook: Codebook) -> None:
    rec.update(classify_status=CLASSIFY_OK, failed_reason="", primary_code=result["primary_code"],
               primary_name=codebook.code_name(rec["product_type"], result["primary_code"]),
               info_level=result["info_level"], environment=result["environment"],
               failures=[{**f, "name": codebook.code_name(rec["product_type"], f["code"])} for f in result["failures"]])


def _set_failed(rec: dict, reason: str) -> None:
    rec.update(classify_status=CLASSIFY_FAILED_STATUS, failed_reason=reason, primary_code=CLASSIFY_FAILED,
               primary_name=CLASSIFY_FAILED_NAME, info_level="", failures=[], environment=[])


def no_text_result(codebook: Codebook, product_id: str) -> dict:
    """G-11: 渡すコメントがすべて空欄の修理。"""
    return {"failures": [], "primary_code": codebook.code_by_role(product_id, "unknown"), "environment": [], "info_level": "none"}


def _write_raw(raw_dir: Path | None, batch: Batch, request: dict, response) -> None:
    if raw_dir is None:
        return
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"batch_{batch.batch_id}_request.json").write_text(
        json.dumps({"product_id": batch.product_id, "rec_keys": batch.rec_keys, "inputs": request}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    if response is not None:
        (raw_dir / f"batch_{batch.batch_id}_response.json").write_text(
            json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_batch(batch: Batch, client, codebook: Codebook, stats: ClassifyStats, raw_dir: Path | None,
               issues: list[ValidationIssue]) -> tuple[dict[str, dict], dict[str, str]]:
    """1回の呼び出し。戻り値: (rec_key → 正規化した結果, rec_key → エラーの種類)。"""
    stats.calls += 1
    ok: dict[str, dict] = {}
    err: dict[str, str] = {}
    try:
        resp = client.run(batch.inputs)
    except DifyAPIError as e:
        _write_raw(raw_dir, batch, batch.inputs, {"error": str(e)})
        for k in batch.rec_keys:
            err[k] = "api_error"
            issues.append(ValidationIssue(k, "api_error", str(e)[:200], "error", batch.batch_id))
        return ok, err
    stats.tokens += resp.total_tokens or 0
    _write_raw(raw_dir, batch, batch.inputs, {"text": resp.text, "total_tokens": resp.total_tokens})
    try:
        items = parse_llm_response(resp.text)
    except LLMParseError as e:
        for k in batch.rec_keys:
            err[k] = "parse_error"
            issues.append(ValidationIssue(k, "parse_error", str(e), "error", batch.batch_id))
        return ok, err
    by_key = {}
    for it in items:
        k = str(it.get("rec_key", ""))
        if k in batch.rec_keys and k not in by_key:
            by_key[k] = it
    rr_of = {rr["rec_key"]: rr for rr in batch.request_records}
    for k in batch.rec_keys:
        it = by_key.get(k)
        if it is None:
            err[k] = "missing_record"
            issues.append(ValidationIssue(k, "missing_record", "結果に含まれていません", "error", batch.batch_id))
            continue
        result, iss = validate_item(it, rr_of[k], batch.product_id, codebook, batch.batch_id)
        issues.extend(iss)
        if result is None:
            err[k] = next(i.kind for i in iss if i.severity == "error")
        else:
            ok[k] = result
    return ok, err


def classify_records(records: list[dict], codebook: Codebook, settings: Settings, client, cache: ClassifyCache, *,
                     internal_mode: str = "default", raw_dir: str | Path | None = None,
                     progress: Callable[[int, int], None] | None = None, dry_run: bool = False) -> ClassifyResult:
    """records を分類する。client は DifyClient(run(inputs) → DifyResponse)。dry_run なら Dify を呼ばない。"""
    raw = Path(raw_dir) if raw_dir is not None else None
    stats = ClassifyStats(total=len(records))
    issues: list[ValidationIssue] = []
    by_rec = {r["rec_key"]: r for r in records}
    keys: dict[str, str] = {}
    to_send: list[tuple[dict, dict]] = []
    for r in records:
        comments = llm_comments(r, settings.internal_excluded(r["sales_company"], internal_mode))
        r["lang"] = detect_lang(*comments.values())
        if not has_text(comments):
            _set_ok(r, no_text_result(codebook, r["product_type"]), codebook)
            stats.no_text += 1
            continue
        k = record_key(r, comments)
        keys[r["rec_key"]] = k
        hit = cache.get(k)
        if hit is not None:
            _set_ok(r, hit, codebook)
            stats.cached += 1
            continue
        to_send.append((r, comments))

    batches = build_batches(to_send, codebook, settings.batch_size)
    stats.batches = len(batches)
    stats.sent = len(to_send)
    if dry_run:
        for b in batches:
            _write_raw(raw, b, b.inputs, None)
        return ClassifyResult(stats, issues)

    done = stats.no_text + stats.cached
    if progress:
        progress(done, stats.total)
    pending_errors: dict[str, str] = {}
    rr_all: dict[str, tuple[str, dict]] = {}
    for b in batches:
        for rr in b.request_records:
            rr_all[rr["rec_key"]] = (b.batch_id, rr)
        ok, err = _run_batch(b, client, codebook, stats, raw, issues)
        cache.put_many([(keys[k], by_rec[k], res) for k, res in ok.items()])
        for k, res in ok.items():
            _set_ok(by_rec[k], res, codebook)
        pending_errors.update(err)
        done += len(ok)
        if progress:
            progress(done, stats.total)

    # G-19: エラーの修理だけを1件ずつ、最大 single_retry_max 回送り直す
    for k in list(pending_errors):
        bid, rr = rr_all[k]
        stats.retried += 1
        for attempt in range(1, settings.single_retry_max + 1):
            sb = single_batch(f"{bid}_r{attempt}_{k}", by_rec[k]["product_type"], rr, codebook)
            ok, err = _run_batch(sb, client, codebook, stats, raw, issues)
            if k in ok:
                cache.put_many([(keys[k], by_rec[k], ok[k])])
                _set_ok(by_rec[k], ok[k], codebook)
                del pending_errors[k]
                done += 1
                if progress:
                    progress(done, stats.total)
                break
            pending_errors[k] = err.get(k, pending_errors[k])

    for k, reason in pending_errors.items():           # G-18: 分類不可として先へ進む
        _set_failed(by_rec[k], reason)
        stats.failed_reasons[reason] += 1
    stats.failed = len(pending_errors)
    stats.ok = stats.total - stats.failed
    if progress:
        progress(stats.total, stats.total)
    return ClassifyResult(stats, issues)
