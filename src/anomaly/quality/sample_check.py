"""抜き取り確認用 Excel と言語別の指標(要件定義書 G-14)。

海外データには正解データが無いため、正解を作る代わりに、
- 言語ごとに無作為に抜き出した修理を、原文・日本語訳・付いたコードと並べて人が見る(sample_check.xlsx)
- 言語別に、分類がうまくいっていない兆候(判定不能・分類不可・抜き出しが原文に無い割合など)を数字で見る(language_metrics.csv)
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from pathlib import Path

from ..analysis.prepare import is_failed
from ..codebook import Codebook
from ..settings import Settings

LANG_ORDER = ("ja", "en", "zh", "ko", "none")
HELP = [
    "抜き取り確認用ファイル(要件定義書 G-14)",
    "",
    "言語ごとに、分類した修理を無作為に抜き出しています(乱数の種は run_meta.json の random_seed)。",
    "原文・日本語訳・付いたコードを見て、明らかにおかしいものの「確認」欄に × を付けてください。正解を作る必要はありません。",
    "日本語訳は LLM が作ったもので、訳が間違っている可能性があります。",
    "× の数と、架空の例に言い換えた短いメモを Mac 側に伝えてください(実データの内容は書かない)。",
]


def _is_unknown(r: dict, codebook: Codebook) -> bool:
    return not is_failed(r) and (r.get("info_level") == "none"
                                 or r.get("primary_code") == codebook.code_by_role(r["product_type"], "unknown"))


def _lang_key(lang: str) -> tuple:
    return (LANG_ORDER.index(lang) if lang in LANG_ORDER else len(LANG_ORDER), lang)


def sample_records(records: list[dict], s: Settings) -> list[dict]:
    """言語ごとに sample_per_language 件まで(コメントが空欄の修理=lang none は除く)。"""
    by_lang: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("lang") and r["lang"] != "none":
            by_lang[r["lang"]].append(r)
    out = []
    for lang in sorted(by_lang, key=_lang_key):
        pool = sorted(by_lang[lang], key=lambda r: r["rec_key"])
        rng = random.Random(f"{s.random_seed}|sample|{lang}")
        picked = rng.sample(pool, min(s.sample_per_language, len(pool)))
        out.extend(sorted(picked, key=lambda r: r["rec_key"]))
    return out


def sample_rows(records: list[dict], codebook: Codebook, s: Settings) -> list[dict]:
    rows = []
    for r in sample_records(records, s):
        p = r["product_type"]
        fails = "\n".join(f"{f['code']} {codebook.code_name(p, f['code'])}({f.get('source', '')}): "
                          f"{f.get('excerpt_ja') or f.get('excerpt') or ''}" for f in r.get("failures") or [])
        rows.append({"確認(×)": "", "メモ": "", "言語": r.get("lang"), "rec_key": r["rec_key"], "修理ID": r.get("repair_id"),
                     "販社": r.get("sales_company"), "製品名": r.get("model"), "部品番号": r.get("part_no"),
                     "ユーザコメント": r.get("user_comment"), "修理者コメント": r.get("repair_comment"),
                     "内部コメント1": r.get("internal_1"), "内部コメント2": r.get("internal_2"),
                     "主な故障": "分類不可" if is_failed(r) else f"{r.get('primary_code')} {codebook.code_name(p, r.get('primary_code'))}",
                     "故障の種類(日本語訳)": fails, "情報の量": r.get("info_level", ""),
                     "分類の状態": r.get("classify_status", ""), "分類不可の理由": r.get("failed_reason", "")})
    return rows


def language_metrics(records: list[dict], codebook: Codebook, issues: list[dict] | None = None) -> list[dict]:
    """言語別の指標。issues は validation_issues の dict(rec_key, kind)。"""
    not_found = Counter(i["rec_key"] for i in issues or [] if i.get("kind") == "excerpt_not_found")
    by_lang: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_lang[r.get("lang") or "none"].append(r)
    rows = []
    for lang in sorted(by_lang, key=_lang_key):
        recs = by_lang[lang]
        n = len(recs)
        ok = [r for r in recs if not is_failed(r)]
        info = Counter(r.get("info_level") for r in ok)
        n_fail_items = sum(len(r.get("failures") or []) for r in ok)
        n_nf = sum(not_found[r["rec_key"]] for r in recs)

        def p(x, d):
            return round(100.0 * x / d, 1) if d else 0.0
        rows.append({"言語": lang, "件数": n,
                     "判定不能(%)": p(sum(1 for r in recs if _is_unknown(r, codebook)), n),
                     "分類不可(%)": p(n - len(ok), n),
                     "情報量 sufficient(%)": p(info["sufficient"], len(ok)), "情報量 thin(%)": p(info["thin"], len(ok)),
                     "情報量 none(%)": p(info["none"], len(ok)),
                     "故障の種類の平均件数": round(n_fail_items / len(ok), 2) if ok else 0.0,
                     "抜き出しが原文に無い割合(%)": p(n_nf, n_fail_items)})
    return rows


def write_quality_files(out_dir: Path, records: list[dict], codebook: Codebook, s: Settings,
                        issues: list[dict] | None = None) -> None:
    from ..outputs.excel import write_workbook
    from ..outputs.files import write_csv

    write_workbook(Path(out_dir) / "sample_check.xlsx",
                   {"説明": [{"説明": x} for x in HELP], "抜き取り": sample_rows(records, codebook, s)})
    rows = language_metrics(records, codebook, issues)
    write_csv(Path(out_dir) / "language_metrics.csv", list(rows[0]) if rows else ["言語"], rows)
