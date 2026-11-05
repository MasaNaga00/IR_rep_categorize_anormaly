"""テスト用の、分類済みの修理(架空)を作る。段階3(解析)のテストで使う。

balanced(): どの群で分けても(受付月・使用期間・ロット)、故障の種類の構成がまったく同じになるデータ。
  製造ロット(12か月)× 使用期間(4種類)の組み合わせごとに、同じ並びの修理(27件)を作るため、
  受付月・使用期間・ロットのどれで分けても、各群の構成は同じになる。→ 注目点が出てはいけない(中立性の確認)。
"""
from __future__ import annotations

from datetime import date

# 1ブロックの並び: (主な故障, 販社, 言語)。判定不能と分類不可を1件ずつ含む
PATTERN = ([("M001", "CUSA", "en")] * 6 + [("M001", "CCN", "zh")] * 4 + [("M005", "CEU", "en")] * 6
           + [("M008", "CMJ", "ja")] * 5 + [("M009", "CKR", "ko")] * 3 + [("M002", "CUSA", "en")]
           + [("UNK", "CEU", "en"), ("FAILED", "CUSA", "en")])
OFFSETS = (1, 6, 14, 30)                 # 使用期間(月)
LOT_START = (2022, 1)                    # 製造ロット 2022-01 〜 2022-12
N_LOTS = 12
RUN_DATE = date(2025, 7, 10)             # 直近6か月 = 2025-01 〜 2025-06
TEXT = {"M001": ("Camera does not power on", "電源が入らない"), "M005": ("AF not working", "AF が動かない"),
        "M008": ("画像が暗い", "画像が暗い"), "M009": ("이상한 소리가 남", "異音がする"), "M002": ("Error 70", "エラー70"),
        "M020": ("Flash does not fire", "ストロボが光らない")}


def idx(y, m):
    return y * 12 + m - 1


def ym(i):
    return f"{i // 12}-{i % 12 + 1:02d}"


def make(n: int, code: str, received_idx: int, *, model="X100", part="P-001", product="ML", sales="CUSA", lang="en",
         mfg_idx: int | None = None, lot_code: str | None = None, mfg_status: str | None = None, day: int = 15,
         serial: str | None = None, env: list | None = None, max_usage: int = 96) -> dict:
    rec = {"rec_key": f"{n:06d}", "repair_id": f"R{n:06d}", "model": model, "product_type": product, "part_no": part,
           "serial": serial if serial is not None else f"S{n:07d}", "received_date": f"{ym(received_idx)}-{day:02d}",
           "sales_company": sales, "lang": lang, "user_comment": "", "repair_comment": "", "internal_1": "", "internal_2": "",
           "classify_status": "ok", "failed_reason": "", "info_level": "sufficient", "primary_code": code,
           "failures": [], "environment": env or [], "mfg_warning": ""}
    unknown = f"{product[0]}_UNK" if product != "LENS" else "L_UNK"
    if code == "UNK":
        rec.update(primary_code=unknown, info_level="none")
    elif code == "FAILED":
        rec.update(primary_code="CLASSIFY_FAILED", classify_status="failed", failed_reason="api_error", info_level="")
    else:
        en, ja = TEXT.get(code, (f"text {code}", f"記載 {code}"))
        rec["failures"] = [{"code": code, "source": "repair", "excerpt": en, "excerpt_ja": ja}]
    if mfg_idx is not None:
        u = received_idx - mfg_idx
        rec.update(mfg_ym=ym(mfg_idx), lot_code=lot_code or f"{mfg_idx % 96 + 1:02d}", lot_order=mfg_idx,
                   mfg_status=mfg_status or "ok", usage_months=u,
                   usage_status="ok" if 0 <= u <= max_usage else ("negative" if u < 0 else "over_max"))
    elif lot_code is not None:
        rec.update(mfg_ym="", lot_code=lot_code, lot_order=int(lot_code) if lot_code.isdigit() else None,
                   mfg_status=mfg_status or "prefix_only", usage_months=None, usage_status="no_mfg_ym")
    else:
        rec.update(mfg_ym="", lot_code="", lot_order=None, mfg_status=mfg_status or "not_converted",
                   usage_months=None, usage_status="no_mfg_ym")
    return rec


def balanced(start: int = 1, with_mfg: bool = True, **kw) -> list[dict]:
    recs = []
    n = start
    lot0 = idx(*LOT_START)
    for lot in range(lot0, lot0 + N_LOTS):
        for off in OFFSETS:
            for code, sales, lang in PATTERN:
                recs.append(make(n, code, lot + off, sales=sales, lang=lang, mfg_idx=lot if with_mfg else None, **kw))
                n += 1
    return recs


def with_recent_excess(code: str = "M009", count: int = 60, start: int = 50000) -> list[dict]:
    """balanced() に、直近6か月(2025-02〜2025-05 の受付、新しいロット 2025-01)の code の修理を足す。"""
    recs = balanced()
    lot = idx(2025, 1)
    for i in range(count):
        recs.append(make(start + i, code, lot + 1 + i % 4, sales="CUSA", lang="en", mfg_idx=lot))
    return recs
