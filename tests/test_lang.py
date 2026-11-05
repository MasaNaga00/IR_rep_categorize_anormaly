"""言語の判定(要件定義書 G-02)。"""
from __future__ import annotations

import pytest

from anomaly.input_loader import llm_comments, load_input
from anomaly.lang import detect_lang

from .conftest import SYNTH


@pytest.mark.parametrize("texts, expect", [
    (("전원이 켜지지 않음",), "ko"),
    (("電源が入らない",), "ja"),
    (("ﾃﾞﾝｹﾞﾝ ﾌﾘｮｳ",), "ja"),                 # 半角カナ
    (("无法开机",), "zh"),
    (("電源不良",), "zh"),                    # かなの無い漢字だけは zh(規則どおり)
    (("Camera does not power on",), "en"),
    (("ＡＦ ＮＧ",), "en"),                    # 全角英字
    (("",), "none"),
    (("123 !!",), "none"),
    ((None, ""), "none"),
    # 複数に当てはまる場合の優先順: ko → ja → zh → en
    (("LCD 화면 불량", "replaced"), "ko"),
    (("Replaced", "液晶が暗い"), "ja"),
    (("更换 LCD",), "zh"),
    (("電源不良", "メイン基板を交換"), "ja"),      # 列をまたいで判定する
])
def test_G02_detect_lang(texts, expect):
    assert detect_lang(*texts) == expect


def test_G02_synthetic_data(codebook, settings):
    res = load_input(SYNTH / "repairs.csv", codebook.product_ids())
    got = {r["rec_key"]: detect_lang(*llm_comments(r, settings.internal_excluded(r["sales_company"])).values())
           for r in res.records}
    assert got["000001"] == "ja"
    assert got["000002"] == "en"
    assert got["000004"] == "zh"
    assert got["000005"] == "ko"
    assert got["000006"] == "none"
