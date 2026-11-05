"""Dify ワークフロー API の呼び出し(通信はモック)。要件定義書 E-08, E-10。

v2 の tests/v2/test_dify_client.py をコピーしたもの。テスト名の R-xx は v2 の要件 ID。
"""
from __future__ import annotations

import pytest
import requests

from anomaly.dify_client import KEY_SUMMARY, DifyAPIError, DifyClient, DifyResponse

BASE = "https://dify.example.local/v1"
URL = BASE + "/workflows/run"


def _ok(text='{"results": []}', tokens=321):
    return {"json": {"workflow_run_id": "w1", "data": {"status": "succeeded", "outputs": {"result": text}, "total_tokens": tokens, "error": None}}, "status_code": 200}


@pytest.fixture
def client():
    return DifyClient(BASE, "app-test", timeout=5, max_retries=3, backoff=0)


def test_r52_success(requests_mock, client):
    requests_mock.post(URL, **_ok('{"results": [1]}'))
    r = client.run({"product_name": "x", "code_list": "y", "records": "[]"})
    assert isinstance(r, DifyResponse)
    assert r.text == '{"results": [1]}' and r.total_tokens == 321
    req = requests_mock.request_history[0]
    assert req.headers["Authorization"] == "Bearer app-test"
    body = req.json()
    assert body["inputs"] == {"product_name": "x", "code_list": "y", "records": "[]"}
    assert body["response_mode"] == "blocking"
    assert body["user"]


def test_r52_trailing_slash_in_base_url(requests_mock):
    requests_mock.post(URL, **_ok())
    DifyClient(BASE + "/", "app-test", backoff=0).run({})
    assert requests_mock.call_count == 1


def test_r53_retry_on_5xx_then_success(requests_mock, client):
    requests_mock.post(URL, [{"status_code": 502, "text": "bad gateway"}, {"status_code": 500, "text": "err"}, _ok()])
    assert client.run({}).text == '{"results": []}'
    assert requests_mock.call_count == 3


def test_r53_retry_on_timeout(requests_mock, client):
    requests_mock.post(URL, [{"exc": requests.exceptions.ConnectTimeout}, _ok()])
    client.run({})
    assert requests_mock.call_count == 2


def test_r53_gives_up_after_max_retries(requests_mock, client):
    requests_mock.post(URL, status_code=503, text="unavailable")
    with pytest.raises(DifyAPIError) as ei:
        client.run({})
    assert ei.value.status_code == 503
    assert requests_mock.call_count == 4   # 初回 + 3回リトライ


@pytest.mark.parametrize("code", [401, 403])
def test_r53_no_retry_on_auth_error(requests_mock, client, code):
    requests_mock.post(URL, status_code=code, text="unauthorized")
    with pytest.raises(DifyAPIError) as ei:
        client.run({})
    assert ei.value.status_code == code
    assert requests_mock.call_count == 1


def test_r53_workflow_failed_status(requests_mock, client):
    requests_mock.post(URL, json={"data": {"status": "failed", "outputs": None, "error": "LLM error"}}, status_code=200)
    with pytest.raises(DifyAPIError) as ei:
        client.run({})
    assert "LLM error" in str(ei.value)


def test_E08_from_env_classify_key(monkeypatch):
    monkeypatch.setenv("DIFY_BASE_URL", BASE)
    monkeypatch.setenv("DIFY_API_KEY_CLASSIFY", "app-classify")
    monkeypatch.setenv("DIFY_API_KEY_SUMMARY", "app-summary")
    c = DifyClient.from_env()
    assert c.base_url == BASE and c.api_key == "app-classify"


def test_E08_from_env_summary_key(monkeypatch):
    monkeypatch.setenv("DIFY_BASE_URL", BASE)
    monkeypatch.setenv("DIFY_API_KEY_CLASSIFY", "app-classify")
    monkeypatch.setenv("DIFY_API_KEY_SUMMARY", "app-summary")
    c = DifyClient.from_env(key_var=KEY_SUMMARY)
    assert c.api_key == "app-summary"


def test_E08_from_env_missing(monkeypatch):
    monkeypatch.delenv("DIFY_BASE_URL", raising=False)
    monkeypatch.delenv("DIFY_API_KEY_CLASSIFY", raising=False)
    with pytest.raises(ValueError) as ei:
        DifyClient.from_env(env_file=None)
    assert "DIFY_API_KEY_CLASSIFY" in str(ei.value)


def test_E08_from_env_file(tmp_path, monkeypatch):
    for k in ("DIFY_BASE_URL", "DIFY_API_KEY_CLASSIFY"):   # 終了後に、.env で読んだ値が残らないようにする
        monkeypatch.setenv(k, "x")
        monkeypatch.delenv(k)
    env = tmp_path / ".env"
    env.write_text(f"DIFY_BASE_URL={BASE}\nDIFY_API_KEY_CLASSIFY=app-file\n", encoding="utf-8")
    c = DifyClient.from_env(env_file=env)
    assert c.api_key == "app-file"


def test_E08_placeholder_values_rejected(monkeypatch):
    """init で作った .env の初期値のままなら、実際に接続しに行かずにエラーにする。"""
    monkeypatch.setenv("DIFY_BASE_URL", "https://your-dify-host/v1")
    monkeypatch.setenv("DIFY_API_KEY_CLASSIFY", "app-xxxxxxxx")
    with pytest.raises(ValueError) as ei:
        DifyClient.from_env()
    assert "初期値のまま" in str(ei.value)
