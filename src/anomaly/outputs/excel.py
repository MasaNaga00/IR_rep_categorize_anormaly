"""tables.xlsx(組み合わせごと)と index.xlsx(全組み合わせの一覧)(要件定義書 O-02、O-04)。

どのシートも「1行1レコード、結合セルなし」で、そのままフィルターできる形にする。
"""
from __future__ import annotations

from pathlib import Path

from ..analysis.aspects import ComboAnalysis, profile_rows
from ..analysis.findings import JUDGEMENT_NAMES
from ..report.markdown import ASPECT_TITLES, all_examples, prerequisites
from ..settings import Settings

EMPTY = "(データなし)"


def write_workbook(path: Path, sheets: dict[str, list[dict]], before_save=None) -> Path:
    """before_save: 保存の前に wb を受け取って呼ぶ関数(サマリーシートを先頭に足すため。O-07)。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    wb.remove(wb.active)
    bold = Font(bold=True)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name[:31])
        if not rows:
            ws.append([EMPTY])
            continue
        headers: list[str] = []
        for r in rows:
            for k in r:
                if k not in headers:
                    headers.append(k)
        ws.append(headers)
        for c in ws[1]:
            c.font = bold
        for r in rows:
            ws.append([r.get(h) for h in headers])
        ws.freeze_panes = "A2"
        for i, h in enumerate(headers, start=1):
            width = max([len(str(h))] + [len(str(r.get(h) if r.get(h) is not None else "")) for r in rows[:200]])
            ws.column_dimensions[ws.cell(1, i).column_letter].width = min(60, max(8, width * 1.6))
    if before_save is not None:
        before_save(wb)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def combo_sheets(ca: ComboAnalysis, s: Settings, run_info: dict) -> dict[str, list[dict]]:
    """O-04: tables.xlsx のシート。"""
    sheets: dict[str, list[dict]] = {}
    sheets["前提"] = [{"項目": f"{i}", "内容": x} for i, x in enumerate(prerequisites(ca, s, run_info), start=1)]
    sheets["概要"] = [{"観点": ASPECT_TITLES[c.aspect], "比較": c.label, "群A": c.group_a, "件数A": c.na, "群B": c.group_b,
                     "件数B": c.nb, "判定": JUDGEMENT_NAMES[c.judgement], "注目点の数": len(c.findings),
                     "注記": " / ".join(c.notes)} for c in ca.comparisons()]
    for aspect in (ca.overall, ca.recent, ca.usage, ca.lot):
        for name, rows in aspect.tables.items():
            if name == "群の構成":
                sheets.setdefault("群の構成", []).extend(rows)
            else:
                sheets[name] = rows
    for c in ca.lot.comparisons:
        sheets.setdefault("群の構成", []).extend(profile_rows(c))
    sheets["代表事例"] = all_examples(ca, s)
    sheets["除外・変換不能"] = ca.exclusions
    order = ["前提", "概要", "全体", "偏りの指標", "コード別(まとめる前)", "記載割合", "環境要因", "販社別", "再修理の可能性",
             "期間比較", "月別推移", "使用期間_分布", "使用期間_コード別", "使用期間_クロス", "使用期間_比較",
             "ロット", "ロット_構成", "ロット_まとめ", "ロット_比較", "群の構成", "代表事例", "除外・変換不能"]
    return {k: sheets.get(k, []) for k in order}


def index_rows(results: list[tuple[ComboAnalysis, str]]) -> list[dict]:
    """O-02: 全組み合わせの一覧(件数の多い順)。"""
    rows = []
    for ca, folder in results:
        f = ca.overall.facts
        n = f["n_all"]
        mfg_ok = sum(1 for r in ca.combo.records if r.get("mfg_status") == "ok")
        rows.append({"製品名": ca.combo.model, "部品番号": ca.combo.part_no, "製品種別": ca.combo.product_type, "修理件数": n,
                     "判定不能(%)": round(100 * f["n_unknown"] / n, 1) if n else 0.0,
                     "分類不可(%)": round(100 * f["n_failed"] / n, 1) if n else 0.0,
                     "製造年月あり(%)": round(100 * mfg_ok / n, 1) if n else 0.0,
                     "最も多い故障": f["concentration"]["top1"]["name"] if f["concentration"]["top1"] else "",
                     "最も多い故障の割合(%)": f["concentration"]["top1"]["pct"] if f["concentration"]["top1"] else None,
                     "上位3つの割合(%)": f["concentration"]["top3_pct"],
                     "期間比較": JUDGEMENT_NAMES[ca.recent.judgement], "使用期間": JUDGEMENT_NAMES[ca.usage.judgement],
                     "ロット": JUDGEMENT_NAMES[ca.lot.judgement], "注目点の数": ca.n_findings(), "フォルダ": folder})
    rows.sort(key=lambda r: (-r["修理件数"], r["製品名"], r["部品番号"]))
    return rows
