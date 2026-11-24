"""Excel(xlsx)に書けない制御文字の置き換え(要件定義書 O-09)。

xlsx には、タブ・改行(\\t \\n \\r)以外の制御文字(\\x00〜\\x08、\\x0b、\\x0c、\\x0e〜\\x1f)を入れられない。
openpyxl は書こうとすると IllegalCharacterError で止まる(2026-11-24 に会社PCの実データで発生)。
- 垂直タブ(\\x0b)は、元のデータでは改行のつもりで入っていることが多いので、改行に置き換える。
- ほかの使えない制御文字は、空白1文字に置き換える(ユーザ決定)。
入力の CSV と、CSV で出すファイルは変えない。Excel に書くときだけ置き換える。
"""
from __future__ import annotations

import re

# openpyxl.cell.cell.ILLEGAL_CHARACTERS_RE と同じ範囲(内部の名前に頼らないよう、ここで定義する)
ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def excel_safe(value):
    """文字列なら、Excel に書けない制御文字を置き換えて返す。文字列以外はそのまま。"""
    if not isinstance(value, str):
        return value
    return ILLEGAL_RE.sub(" ", value.replace("\x0b", "\n"))
