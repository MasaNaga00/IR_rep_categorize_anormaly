"""Dify での分類(要件定義書 G-02、G-11、G-12、G-16〜G-30、U-05)。Dify は FakeDify で模擬する。"""
from __future__ import annotations

import json

import pytest

from anomaly.classify.cache import ClassifyCache, NullCache, cache_signature, record_key
from anomaly.classify.llm_request import build_batches
from anomaly.classify.llm_response import LLMParseError, parse_llm_response, validate_item
from anomaly.classify.runner import classify_records
from anomaly.input_loader import llm_comments, load_input
from anomaly.settings import parse_settings

from .conftest import SYNTH
from .fake_dify import FakeDify, api_error, ideal_response


@pytest.fixture
def recs(codebook):
    return load_input(SYNTH / "repairs.csv", codebook.product_ids()).records


def _by(recs):
    return {r["rec_key"]: r for r in recs}


def _items(recs, settings, mode="default"):
    return [(r, llm_comments(r, settings.internal_excluded(r["sales_company"], mode))) for r in recs]


# --- リクエスト(G-12, G-25, G-26) ---

def test_G25_G26_batches(recs, codebook, settings):
    batches = build_batches(_items(recs, settings), codebook, batch_size=3)
    assert [(b.batch_id, b.product_id, len(b.rec_keys)) for b in batches] == [
        ("001", "LENS", 1), ("002", "ML", 3), ("003", "ML", 3), ("004", "ML", 1)]
    rr = {r["rec_key"]: r for b in batches for r in json.loads(b.inputs["records"])}
    assert set(rr["000001"]) == {"rec_key", "user_comment", "repair_comment"}            # CMJ: 内部コメントはキーごと省く
    assert rr["000003"]["internal_2"] == "Main PCB replaced"
    # G-31: 交換部品などの情報は渡さない(コメントと rec_key だけ)
    allowed = {"rec_key", "user_comment", "repair_comment", "internal_1", "internal_2"}
    assert all(set(x) <= allowed for x in rr.values())
    assert all("P-001" not in b.inputs["records"] and "X100" not in b.inputs["records"] for b in batches)
    b = batches[1]
    assert b.inputs["product_name"] == "ミラーレスカメラ" and "M001 | 電源不良" in b.inputs["code_list"]
    assert "manufacturer" not in b.inputs["code_list"]
    with pytest.raises(ValueError):
        build_batches([], codebook, 0)


def test_G30_internal_all(recs, codebook, settings):
    batches = build_batches(_items(recs, settings, "all"), codebook, 10)
    rr = {r["rec_key"]: r for b in batches for r in json.loads(b.inputs["records"])}
    assert rr["000001"]["internal_1"] == "お客様へ見積連絡済み。"


# --- パース(G-28) ---

@pytest.mark.parametrize("raw", [
    '{"results": [{"rec_key": "1"}]}', '[{"rec_key": "1"}]', '```json\n{"results": [{"rec_key": "1"}]}\n```',
    'はい。\n{"results": [{"rec_key": "1"}]}\n以上です。', {"results": [{"rec_key": "1"}]},
])
def test_G28_parse_variants(raw):
    assert parse_llm_response(raw) == [{"rec_key": "1"}]


@pytest.mark.parametrize("raw", ["", "not json", '{"foo": 1}', '[1, 2]'])
def test_G28_parse_errors(raw):
    with pytest.raises(LLMParseError):
        parse_llm_response(raw)


# --- 検証(G-29) ---

RR = {"rec_key": "000002", "user_comment": "Camera does not power on", "repair_comment": "Replaced main board"}


def _item(**kw):
    base = {"rec_key": "000002", "failures": [{"code": "M001", "source": "user", "excerpt": "Camera does not power on",
                                               "excerpt_ja": "電源が入らない"}],
            "primary_code": "M001", "environment": [], "info_level": "sufficient"}
    base.update(kw)
    return base


def test_G29_valid(codebook):
    res, issues = validate_item(_item(), RR, "ML", codebook)
    assert issues == [] and res["primary_code"] == "M001" and res["failures"][0]["excerpt_ja"] == "電源が入らない"


@pytest.mark.parametrize("item, kind", [
    (_item(failures=[{"code": "L001", "source": "user", "excerpt": ""}], primary_code="L001"), "unknown_code"),
    (_item(failures=[{"code": "M001", "source": "customer", "excerpt": ""}]), "invalid_enum"),
    (_item(info_level="much"), "invalid_enum"),
    (_item(primary_code="M005"), "primary_mismatch"),
    (_item(environment=[{"factor": "fire", "source": "user"}]), "invalid_enum"),
    (_item(environment=[{"factor": "impact", "source": "someone"}]), "invalid_enum"),
    (_item(failures="M001"), "invalid_enum"),
])
def test_G29_errors(codebook, item, kind):
    res, issues = validate_item(item, RR, "ML", codebook)
    assert res is None
    assert kind in [i.kind for i in issues if i.severity == "error"]


def test_G29_warnings_keep_result(codebook):
    item = _item(failures=[{"code": "M001", "source": "user", "excerpt": "camera does  not power on!!"},
                           {"code": "M001", "source": "repair", "excerpt": ""}])
    res, issues = validate_item(item, RR, "ML", codebook)
    assert res is not None and len(res["failures"]) == 1
    assert {i.kind for i in issues} == {"excerpt_not_found", "duplicate_code"}
    assert all(i.severity == "warning" for i in issues)


def test_G29_excerpt_whitespace_ignored(codebook):
    item = _item(failures=[{"code": "M001", "source": "user", "excerpt": "Camera  does not\npower on"}])
    assert validate_item(item, RR, "ML", codebook)[1] == []


def test_G08_unknown_primary_with_empty_failures(codebook):
    res, issues = validate_item(_item(failures=[], primary_code="M_UNK", info_level="none"), RR, "ML", codebook)
    assert issues == [] and res["failures"] == []


# --- 実行(G-11, G-18〜G-23, G-02) ---

def test_G11_G02_ideal_run(recs, codebook, settings):
    client = FakeDify()
    res = classify_records(recs, codebook, settings, client, NullCache())
    b = _by(recs)
    assert (res.stats.total, res.stats.no_text, res.stats.sent, res.stats.batches, res.stats.calls) == (8, 1, 7, 2, 2)
    assert res.stats.ok == 8 and res.stats.failed == 0
    assert all("000006" not in keys for keys in client.sent_keys)          # コメントが空欄の修理は送らない
    assert b["000006"]["primary_code"] == "M_UNK" and b["000006"]["info_level"] == "none" and b["000006"]["lang"] == "none"
    assert b["000001"]["primary_code"] == "M001" and b["000001"]["failures"][0]["source"] == "repair"
    assert b["000003"]["primary_code"] == "M017"                           # ユーザ申告だけ
    assert b["000012"]["primary_code"] == "L001" and b["000012"]["primary_name"] == "AF作動不具合"
    assert [b[k]["lang"] for k in ("000001", "000002", "000004", "000005")] == ["ja", "en", "zh", "ko"]
    assert b["000001"]["failures"][0]["name"] == "電源不良"


def test_G19_batch_error_then_single_retry(recs, codebook, settings):
    client = FakeDify(lambda n, recs_, ideal: api_error() if n == 0 else ideal)
    res = classify_records(recs, codebook, settings, client, NullCache())
    assert res.stats.failed == 0 and res.stats.retried == 1                 # LENS の1件(最初のバッチ)
    assert client.sent_keys[0] == ["000012"] and client.sent_keys[-1] == ["000012"]
    assert any(i.kind == "api_error" for i in res.issues)


def test_G19_missing_record_retried(recs, codebook, settings):
    def drop(n, recs_, ideal):
        if n == 1:                                                          # ML のバッチで1件落とす
            ideal["results"] = [x for x in ideal["results"] if x["rec_key"] != "000003"]
        return ideal
    client = FakeDify(drop)
    res = classify_records(recs, codebook, settings, client, NullCache())
    assert res.stats.failed == 0 and res.stats.retried == 1
    assert client.sent_keys[-1] == ["000003"]


def test_G19_parse_error_retried(recs, codebook, settings):
    client = FakeDify(lambda n, recs_, ideal: "申し訳ありません" if n < 2 else ideal)
    res = classify_records(recs, codebook, settings, client, NullCache())
    assert res.stats.failed == 0 and res.stats.retried == 7 and res.stats.calls == 2 + 7


def test_G18_G20_failed_after_retries(recs, codebook, settings):
    def bad(n, recs_, ideal):
        for x in ideal["results"]:
            if x["rec_key"] == "000004":
                x["primary_code"] = "Z999"
        return ideal
    client = FakeDify(bad)
    res = classify_records(recs, codebook, settings, client, NullCache())
    r = _by(recs)["000004"]
    assert res.stats.failed == 1 and res.stats.failed_reasons == {"primary_mismatch": 1}
    assert (r["classify_status"], r["primary_code"], r["failed_reason"], r["failures"]) == ("failed", "CLASSIFY_FAILED", "primary_mismatch", [])
    assert res.stats.calls == 2 + settings.single_retry_max                  # バッチ2回 + 1件ずつ2回
    assert [k for k in client.sent_keys[2:]] == [["000004"], ["000004"]]
    assert res.stats.ok == 7


def test_G19_retry_count_setting(recs, codebook):
    s = parse_settings({"single_retry_max": 0})
    client = FakeDify(lambda n, recs_, ideal: api_error())
    res = classify_records(recs, codebook, s, client, NullCache())
    assert res.stats.failed == 7 and res.stats.calls == 2 and res.stats.failed_reasons == {"api_error": 7}


def test_U05_progress_callback(recs, codebook, settings):
    seen = []
    classify_records(recs, codebook, settings, FakeDify(), NullCache(), progress=lambda d, t: seen.append((d, t)))
    assert seen[0] == (1, 8) and seen[-1] == (8, 8) and seen == sorted(seen)


def test_dry_run_writes_requests_only(tmp_path, recs, codebook, settings):
    client = FakeDify()
    res = classify_records(recs, codebook, settings, client, NullCache(), raw_dir=tmp_path, dry_run=True)
    assert client.calls == [] and res.stats.batches == 2
    req = json.loads((tmp_path / "batch_002_request.json").read_text(encoding="utf-8"))
    assert req["product_id"] == "ML" and set(req["inputs"]) == {"product_name", "code_list", "records"}
    assert not list(tmp_path.glob("*_response.json"))


def test_raw_files_written(tmp_path, recs, codebook, settings):
    client = FakeDify(lambda n, recs_, ideal: api_error() if n == 0 else ideal)
    classify_records(recs, codebook, settings, client, NullCache(), raw_dir=tmp_path)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert "batch_001_request.json" in names and "batch_001_response.json" in names
    assert "batch_001_r1_000012_request.json" in names
    assert "error" in json.loads((tmp_path / "batch_001_response.json").read_text(encoding="utf-8"))


# --- キャッシュ(G-16, G-23) ---

def test_G16_cache_reused(tmp_path, recs, codebook, settings):
    sig = cache_signature(codebook.sha256, "p" * 64, "default:CMJ")
    classify_records(recs, codebook, settings, FakeDify(), ClassifyCache(tmp_path, sig))
    again = load_input(SYNTH / "repairs.csv", codebook.product_ids()).records
    client = FakeDify()
    res = classify_records(again, codebook, settings, client, ClassifyCache(tmp_path, sig))
    assert client.calls == [] and res.stats.cached == 7 and res.stats.ok == 8
    assert _by(again)["000001"]["primary_code"] == "M001"


def test_G23_failed_not_cached(tmp_path, recs, codebook, settings):
    sig = cache_signature(codebook.sha256, "p" * 64, "default:CMJ")
    bad = FakeDify(lambda n, recs_, ideal: {"results": [x for x in ideal["results"] if x["rec_key"] != "000004"]})
    classify_records(recs, codebook, settings, bad, ClassifyCache(tmp_path, sig))
    again = load_input(SYNTH / "repairs.csv", codebook.product_ids()).records
    client = FakeDify()
    res = classify_records(again, codebook, settings, client, ClassifyCache(tmp_path, sig))
    assert client.sent_keys == [["000004"]] and res.stats.cached == 6 and res.stats.failed == 0


def test_G16_signature_changes(codebook):
    a = cache_signature(codebook.sha256, "p" * 64, "default:CMJ")
    assert a != cache_signature(codebook.sha256, "q" * 64, "default:CMJ")       # プロンプト
    assert a != cache_signature("x" * 64, "p" * 64, "default:CMJ")              # コード体系
    assert a != cache_signature(codebook.sha256, "p" * 64, "all")               # 内部コメントの渡し方


def test_G16_record_key(recs, settings):
    r = _by(recs)["000003"]
    k = record_key(r, llm_comments(r, False))
    assert k == record_key(dict(r), llm_comments(r, False))
    assert k != record_key(r, llm_comments(r, True))                                # 渡すコメントが違う
    assert k != record_key({**r, "sales_company": "CEU"}, llm_comments(r, False))   # 修理IDが同じでも販社が違う


def test_G16_cache_broken_line_skipped(tmp_path):
    c = ClassifyCache(tmp_path, "sig")
    c.put_many([("k1", {"rec_key": "1"}, {"primary_code": "M001"})])
    with open(c.path, "a", encoding="utf-8") as f:
        f.write('k2,2,R2,"{broken\n')
    c2 = ClassifyCache(tmp_path, "sig")
    assert c2.get("k1") == {"primary_code": "M001"} and c2.get("k2") is None


def test_ideal_response_shape(recs, codebook, settings):
    b = build_batches(_items(recs, settings), codebook, 10)[1]
    out = ideal_response(b.inputs)
    assert len(out["results"]) == len(b.rec_keys)
