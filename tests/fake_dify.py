"""テスト用の Dify の代わり。キーワードで分類する「理想的な LLM」と、エラーの差し込みができる。"""
from __future__ import annotations

import json
import re

from anomaly.dify_client import DifyAPIError, DifyResponse

KEYWORDS = [
    (r"power|電源|开机|전원", {"ML": "M001"}),
    (r"LCD|液晶", {"ML": "M017"}),
    (r"\bAF\b|对焦|ピント", {"ML": "M005", "LENS": "L001"}),
    (r"noise|異音|异响|소리", {"ML": "M009"}),
]
FIELDS = (("repair_comment", "repair"), ("internal_1", "internal"), ("internal_2", "internal"), ("user_comment", "user"))


def ideal_item(rr: dict, inputs: dict) -> dict:
    unknown = next(line.split("|")[0].strip() for line in inputs["code_list"].splitlines() if "判定不能" in line)
    product = {"M": "ML", "L": "LENS", "D": "DC", "S": "STB"}[unknown[0]]
    failures, seen = [], set()
    for field, src in FIELDS:
        text = rr.get(field) or ""
        for pat, codes in KEYWORDS:
            code = codes.get(product)
            if code and code not in seen and re.search(pat, text):
                seen.add(code)
                failures.append({"code": code, "source": src, "excerpt": text, "excerpt_ja": f"訳:{text}"})
    finding = [f for f in failures if f["source"] != "user"]
    primary = (finding or failures)[0]["code"] if failures else unknown
    return {"rec_key": rr["rec_key"], "failures": failures, "primary_code": primary, "environment": [],
            "info_level": "sufficient" if failures else "none"}


def ideal_response(inputs: dict) -> dict:
    return {"results": [ideal_item(rr, inputs) for rr in json.loads(inputs["records"])]}


class FakeDify:
    """behavior(call_no, records, ideal) → 応答(dict / str)か、送出する例外。既定は理想の応答。"""

    def __init__(self, behavior=None):
        self.calls: list[list[dict]] = []
        self.behavior = behavior or (lambda n, recs, ideal: ideal)

    def run(self, inputs: dict) -> DifyResponse:
        recs = json.loads(inputs["records"])
        n = len(self.calls)
        self.calls.append(recs)
        out = self.behavior(n, recs, ideal_response(inputs))
        if isinstance(out, Exception):
            raise out
        return DifyResponse(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False), 100)

    @property
    def sent_keys(self) -> list[list[str]]:
        return [[r["rec_key"] for r in c] for c in self.calls]


def api_error(msg="503 unavailable"):
    return DifyAPIError(503, msg)
