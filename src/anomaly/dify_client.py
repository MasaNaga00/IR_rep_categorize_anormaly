"""Dify ワークフロー API の呼び出し(要件定義書 E-08, E-10)。

v2 の dify_client.py をコピーしたもの。変更点: API キーの環境変数名を指定できるようにした
(分類用 DIFY_API_KEY_CLASSIFY / 要約用 DIFY_API_KEY_SUMMARY)。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests


class DifyAPIError(Exception):
    def __init__(self, status_code: int | None, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Dify API エラー(status={status_code}): {message}")


@dataclass
class DifyResponse:
    text: str
    total_tokens: int | None = None
    raw: dict = field(default_factory=dict)


RETRY_STATUS = {429, 500, 502, 503, 504}
KEY_CLASSIFY = "DIFY_API_KEY_CLASSIFY"   # 分類用ワークフロー
KEY_SUMMARY = "DIFY_API_KEY_SUMMARY"     # 要約用ワークフロー
PLACEHOLDERS = ("your-dify-host", "app-xxxxxxxx", "app-yyyyyyyy")   # init で作る .env の初期値(local_config.ENV_TEMPLATE)


class DifyClient:
    """Dify ワークフローを blocking モードで実行する。

    base_url は `/v1` まで含めた URL(例: https://dify.example.com/v1)。
    社内の証明書は環境変数 REQUESTS_CA_BUNDLE で指定する(requests が自動で読む)。
    """

    def __init__(self, base_url: str, api_key: str, timeout: float = 180, max_retries: int = 3,
                 backoff: float = 2.0, output_key: str = "result", user: str = "anomaly"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.output_key = output_key
        self.user = user

    @classmethod
    def from_env(cls, env_file: str | Path | None = None, key_var: str = KEY_CLASSIFY, **kw) -> "DifyClient":
        if env_file is not None and Path(env_file).exists():
            from dotenv import load_dotenv
            load_dotenv(env_file, override=False)
        base, key = os.environ.get("DIFY_BASE_URL"), os.environ.get(key_var)
        if not base or not key:
            raise ValueError(f"DIFY_BASE_URL と {key_var} を .env または環境変数で設定してください")
        if any(p in base for p in PLACEHOLDERS) or any(p in key for p in PLACEHOLDERS):
            raise ValueError(f"DIFY_BASE_URL か {key_var} が、init で作った初期値のままです。実際の値を .env に書いてください")
        return cls(base, key, **kw)

    def run(self, inputs: dict) -> DifyResponse:
        url = f"{self.base_url}/workflows/run"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {"inputs": inputs, "response_mode": "blocking", "user": self.user}
        last: DifyAPIError | None = None
        for attempt in range(self.max_retries + 1):
            if attempt and self.backoff:
                time.sleep(self.backoff * (2 ** (attempt - 1)))
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=self.timeout)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last = DifyAPIError(None, f"{type(e).__name__}: {e}")
                continue
            if resp.status_code in RETRY_STATUS:
                last = DifyAPIError(resp.status_code, resp.text[:300])
                continue
            if resp.status_code >= 400:
                raise DifyAPIError(resp.status_code, resp.text[:300])
            try:
                payload = resp.json()
            except ValueError:
                raise DifyAPIError(resp.status_code, "応答が JSON ではありません")
            data = payload.get("data") or {}
            if data.get("status") != "succeeded":
                raise DifyAPIError(resp.status_code, f"ワークフローが失敗しました: {data.get('error')}")
            outputs = data.get("outputs") or {}
            if self.output_key not in outputs:
                raise DifyAPIError(resp.status_code, f"出力変数 {self.output_key!r} がありません: {list(outputs)}")
            return DifyResponse(str(outputs[self.output_key]), data.get("total_tokens"), payload)
        assert last is not None
        raise last
