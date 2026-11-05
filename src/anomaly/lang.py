"""言語の判定(要件定義書 G-02)。

LLM に渡すコメントの文字種から判定する。複数に当てはまる場合は次の順で先に当てはまったもの。
  ハングル → ko / ひらがな・カタカナ → ja / 漢字(かな無し)→ zh / ラテン文字 → en / 文字が無い → none
半角カナ・全角英字は NFKC で正規化してから判定する。
"""
from __future__ import annotations

import re
import unicodedata

_HANGUL = re.compile("[ᄀ-ᇿ㄰-㆏가-힯]")
_KANA = re.compile("[ぁ-ゖァ-ヺーㇰ-ㇿ]")
_HAN = re.compile("[㐀-䶿一-鿿豈-﫿]")
_LATIN = re.compile("[A-Za-z]")


def detect_lang(*texts: str | None) -> str:
    s = unicodedata.normalize("NFKC", " ".join(t for t in texts if t))
    if _HANGUL.search(s):
        return "ko"
    if _KANA.search(s):
        return "ja"
    if _HAN.search(s):
        return "zh"
    if _LATIN.search(s):
        return "en"
    return "none"
