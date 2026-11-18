"""分類タグ付きの修理データ tagged_repairs.xlsx(要件定義書 O-08)。

組み合わせ(製品×部番)ごとに、1行 = 1修理で、元のデータの主な列と分類のタグを並べる。
ドキュメント(サマリーなど)を読んだあとに、元の修理を確かめるためのもの。
- 列の見出しは、元の CSV の列名(input_columns.yaml)にする。並びは元の CSV の行の順(rec_key)。
- 区切りの月数・直近の月数は settings.yaml に合わせる。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from ..analysis.aspects import LOT_STATUSES, ComboAnalysis, ym_index
from ..analysis.environment import env_entries, exclusion_factors
from ..analysis.prepare import is_failed, is_unknown
from ..analysis.summary import GROUP_LT, recent_window, usage_group
from ..codebook import Codebook
from ..constants import MFG_NO_USAGE_STATUSES
from ..input_loader import DEFAULT_COLUMNS
from ..settings import Settings

FILE_NAME = "tagged_repairs.xlsx"
SHEET_NAME = "修理データ"
SOURCE_NAMES = {"user": "ユーザ", "repair": "修理者", "internal": "内部", "both": "両方"}
# 元のデータの列(内部の名前)。見出しは元の CSV の列名にする
DATA_KEYS = ("repair_id", "sales_company", "received_date", "model", "part_no", "product_type", "serial",
             "user_comment", "repair_comment", "internal_1", "internal_2")
COMMENT_KEYS = ("user_comment", "repair_comment", "internal_1", "internal_2")
MARK = "○"


def tag_headers(s: Settings) -> list[tuple[str, str]]:
    """(見出し, 説明)。分類のタグの列。"""
    n, sp = s.recent_months, s.usage_split_months
    return [
        ("主な故障", "修理1件につき1つ。修理者・内部の所見の最初 → ユーザ申告の最初 → 判定不能(G-08)。サマリーで数えているもの"),
        ("主な故障コード", "主な故障のコード"),
        ("書かれていた故障すべて", "コメントに書かれていた故障の種類すべて。( )は誰の記述か(ユーザ / 修理者 / 内部)"),
        ("主な故障の根拠(原文)", "主な故障の根拠として LLM が抜き出した部分(原文のまま)"),
        ("主な故障の根拠(日本語訳)", "上の日本語訳"),
        ("分類の状態", "分類できた / 判定不能(コメントから読み取れない)/ 分類不可(Dify のエラー)"),
        ("環境要因", "使用環境・取り扱いの記述。( )は誰の記述か。通常では考えづらい使用なら、その旨も書く"),
        ("環境要因で除いた修理", f"{MARK} = 「サマリー(環境要因除く)」で除いた修理(env_exclude_mode = {s.env_exclude_mode})"),
        ("製造年月", "シリアルから求めた製造年月(使用期間の区分がサービス部品のときは参考。解析には使わない)"),
        ("使用期間(月)", "受付日の年月 − 製造年月(月単位)"),
        ("使用期間の区分", f"製造後{sp}か月未満 / 製造後{sp}か月以上 / 製造年月不明 / 日付矛盾 / サービス部品"),
        (f"直近{n}か月", f"{MARK} = 受付日が直近{n}か月(実行日の月を含めず前月まで)"),
        ("ロット", "シリアルの頭桁(製造ロット)"),
        ("注目点のロット", f"{MARK} = 「ロット_比較」で注目点になったロット(サマリーの ★)"),
    ]


def _usage_label(r: dict, s: Settings) -> str:
    g = usage_group(r, s.usage_split_months)
    if g:
        return f"製造後{s.usage_split_months}か月{'未満' if g == GROUP_LT else '以上'}"
    if r.get("usage_status") == "negative":
        return "日付矛盾"
    if r.get("mfg_status") in MFG_NO_USAGE_STATUSES:
        return "サービス部品"
    return "製造年月不明"


def _status_label(r: dict, unknown_code: str) -> str:
    if is_failed(r):
        return "分類不可"
    return "判定不能" if is_unknown(r, unknown_code) else "分類できた"


def _to_date(v: str):
    try:
        return date.fromisoformat(v)
    except (TypeError, ValueError):
        return v


def _notable_lots(ca: ComboAnalysis) -> set:
    """注目点になったロットの比較(1ロット・まとまり)に含まれるロット。(製造の順, 頭桁) で表す(A-46 の ★ と同じ)。"""
    keys = set()
    for comp in ca.lot.comparisons:
        if comp.findings:
            keys |= {(r.get("lot_order"), r.get("lot_code")) for r in comp.members.get("a", [])}
    return keys


def tagged_rows(ca: ComboAnalysis, codebook: Codebook, s: Settings, run_date: date) -> list[dict]:
    """1行 = 1修理。キーは DATA_KEYS(内部の名前)と、tag_headers() の見出し。"""
    combo = ca.combo
    p = combo.product_type
    factors = codebook.environment_factors(p)
    lo, hi = recent_window(s, run_date)
    lots = _notable_lots(ca)
    n_head = f"直近{s.recent_months}か月"
    rows = []
    for r in sorted(combo.records, key=lambda x: x["rec_key"]):
        failed = is_failed(r)
        primary = "" if failed else (r.get("primary_code") or "")
        fs = [] if failed else (r.get("failures") or [])
        basis = next((f for f in fs if f.get("code") == primary), {})
        envs = []
        for e in env_entries(r):
            name = factors[e["factor"]].name if e["factor"] in factors else e["factor"]
            extra = SOURCE_NAMES.get(e.get("source"), e.get("source") or "")
            if e.get("abnormal_use"):
                extra += "、通常では考えづらい使用"
            envs.append(f"{name}({extra})" if extra else name)
        lot_ok = bool(r.get("lot_code")) and r.get("mfg_status") in LOT_STATUSES
        row = {"行番号": int(r["rec_key"]) if str(r["rec_key"]).isdigit() else r["rec_key"]}
        row.update({k: r.get(k, "") for k in DATA_KEYS})
        row["received_date"] = _to_date(r.get("received_date"))
        row.update({
            "主な故障": "分類不可" if failed else combo.name(primary),
            "主な故障コード": primary,
            "書かれていた故障すべて": "; ".join(
                f"{codebook.code_name(p, f.get('code'))}({SOURCE_NAMES.get(f.get('source'), f.get('source') or '')})"
                for f in fs if f.get("code")),
            "主な故障の根拠(原文)": basis.get("excerpt", ""),
            "主な故障の根拠(日本語訳)": basis.get("excerpt_ja", ""),
            "分類の状態": _status_label(r, combo.unknown_code),
            "環境要因": "; ".join(envs),
            "環境要因で除いた修理": MARK if exclusion_factors(r, factors, s.env_exclude_mode) else "",
            "製造年月": r.get("mfg_ym") or "",
            "使用期間(月)": r.get("usage_months") if r.get("usage_status") in ("ok", "over_max") else None,
            "使用期間の区分": _usage_label(r, s),
            n_head: MARK if lo <= ym_index(r["received_date"]) <= hi else "",
            "ロット": r.get("lot_code") or "",
            "注目点のロット": MARK if lot_ok and (r.get("lot_order"), r.get("lot_code")) in lots else "",
        })
        rows.append(row)
    return rows


def write_tagged_repairs(path: Path, ca: ComboAnalysis, codebook: Codebook, s: Settings, run_date: date,
                         columns: dict | None = None) -> Path:
    """O-08: tagged_repairs.xlsx(シート「修理データ」と「列の説明」)。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    cols = dict(DEFAULT_COLUMNS)
    cols.update({k: v for k, v in (columns or {}).items() if k in DEFAULT_COLUMNS and v})
    tags = tag_headers(s)
    keys = ["行番号"] + list(DATA_KEYS) + [h for h, _ in tags]
    heads = ["行番号"] + [cols[k] for k in DATA_KEYS] + [h for h, _ in tags]
    rows = tagged_rows(ca, codebook, s, run_date)

    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    ws.append(heads)
    head_fill, tag_fill = PatternFill("solid", fgColor="DDEBF7"), PatternFill("solid", fgColor="FCE4D6")
    n_data = 1 + len(DATA_KEYS)
    for j, c in enumerate(ws[1], start=1):
        c.font = Font(bold=True)
        c.fill = head_fill if j <= n_data else tag_fill
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for row in rows:
        ws.append([row.get(k) for k in keys])
    wrap = Alignment(wrap_text=True, vertical="top")
    widths = {"行番号": 8, "received_date": 12, "主な故障": 18, "主な故障コード": 10, "書かれていた故障すべて": 30,
              "主な故障の根拠(原文)": 30, "主な故障の根拠(日本語訳)": 30, "分類の状態": 10, "環境要因": 24,
              "環境要因で除いた修理": 10, "製造年月": 10, "使用期間(月)": 9, "使用期間の区分": 16, "ロット": 8,
              "注目点のロット": 9}
    for j, k in enumerate(keys, start=1):
        letter = ws.cell(1, j).column_letter
        ws.column_dimensions[letter].width = 40 if k in COMMENT_KEYS else widths.get(k, 12)
        if k in COMMENT_KEYS or k in ("書かれていた故障すべて", "主な故障の根拠(原文)", "主な故障の根拠(日本語訳)", "環境要因"):
            for i in range(2, len(rows) + 2):
                ws.cell(i, j).alignment = wrap
        if k == "received_date":
            for i in range(2, len(rows) + 2):
                ws.cell(i, j).number_format = "yyyy-mm-dd"
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions

    doc = wb.create_sheet("列の説明")
    doc.append(["列", "説明"])
    for c in doc[1]:
        c.font = Font(bold=True)
    doc.append(["行番号", "元の CSV の何行目の修理か(見出しの次の行を 1 とする。内部キー rec_key)"])
    names = {"repair_id": "修理ID", "sales_company": "販社名", "received_date": "受付日", "model": "機種名",
             "part_no": "部品番号", "product_type": "事業コード", "serial": "シリアル(全角→半角、大文字にそろえたもの)",
             "user_comment": "ユーザコメント", "repair_comment": "修理コメント", "internal_1": "内部コメント1",
             "internal_2": "内部コメント2"}
    for k in DATA_KEYS:
        doc.append([cols[k], f"元のデータ: {names[k]}"])
    for h, desc in tags:
        doc.append([h, desc])
    doc.column_dimensions["A"].width = 26
    doc.column_dimensions["B"].width = 100
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
