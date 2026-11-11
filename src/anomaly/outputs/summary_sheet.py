"""サマリーシート(要件定義書 O-07)。tables.xlsx の先頭に「サマリー」を置く。

上: 件数の表 / 左: ①使用期間で比べる、②時期で比べる、注目点の一覧、注記 / 右: 3つの棒グラフ(構成比)。
他のシートと違い、見るためのシートなので書式を付ける(結合セルは使わない)。
"""
from __future__ import annotations

from ..analysis.summary import (GROUP_ALL, GROUP_GE, GROUP_LT, GROUPS, PERIOD_ALL, PERIOD_RECENT, TOTAL_ROW,
                                Summary)

SHEET_NAME = "サマリー"
CHART_COL = "R"            # グラフを置く列(表②の右)
COLOR_TITLE = "1F4E79"
COLOR_SECTION = "2E75B6"
COLOR_HEAD = "DDEBF7"
COLOR_NOTABLE = "FCE4D6"   # 注目点(A-14)のセル
COLOR_SMALL = "7F7F7F"     # 件数が少ない区分の見出しの文字色
PCT_FMT = '0.0"%"'
DIFF_FMT = '+0.0;-0.0;0.0'


def finding_sentence(fr: dict) -> str:
    """注目点の一覧の1行(A-45)。数値と決まった文型だけで書く。"""
    p = fr["p値(補正後)"]
    ptxt = "p<0.0001" if p < 0.0001 else f"p={p:.4f}"
    return (f"{fr['観点']}: {fr['比較']} — {fr['故障の種類']} {fr['割合A(%)']:.1f}% と {fr['割合B(%)']:.1f}%"
            f"(差 {fr['差(ポイント)']:+.1f} ポイント、補正後 {ptxt})")


def add_summary_sheet(wb, sm: Summary) -> None:
    from openpyxl.chart import BarChart, Reference
    from openpyxl.chart.series import SeriesLabel
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    ws = wb.create_sheet(SHEET_NAME, 0)
    wb.active = 0
    fill = lambda c: PatternFill("solid", fgColor=c)          # noqa: E731
    thin = Side(style="thin", color="BFBFBF")
    border = Border(bottom=thin)
    head_font, white = Font(bold=True), Font(bold=True, color="FFFFFF")
    small_font = Font(bold=True, color=COLOR_SMALL)

    def put(row, col, value, *, font=None, bg=None, fmt=None, align=None):
        c = ws.cell(row, col, value)
        if font:
            c.font = font
        if bg:
            c.fill = fill(bg)
        if fmt:
            c.number_format = fmt
        if align:
            c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=True)
        return c

    def section(row, text, width=18):
        for col in range(1, width + 1):
            put(row, col, None, bg=COLOR_SECTION)
        put(row, 1, text, font=white, bg=COLOR_SECTION)

    gname, pname = sm.group_names, sm.period_names

    def label(g, p, suffix=""):
        t = f"{gname[g]}\n{pname[p]}{suffix}"
        return t + ("\n(参考: 件数少)" if sm.small(g, p) else "")

    # --- 見出しと条件 ---
    for col in range(1, 19):
        put(1, col, None, bg=COLOR_TITLE)
    put(1, 1, f"{sm.model} × {sm.part_no} 修理傾向サマリー", font=Font(bold=True, size=14, color="FFFFFF"), bg=COLOR_TITLE)
    put(2, 1, f"全期間: {sm.first_date or '-'} 〜 {sm.last_date or '-'}(受付日) / {pname[PERIOD_RECENT]}: "
              f"{sm.recent_from} 〜 {sm.recent_to}(実行日 {sm.run_date} の前月まで) / "
              f"使用期間 = 受付日の年月 − 製造年月({sm.split_months}か月未満 / 以上で分ける)")
    put(3, 1, "※ 構成比(割合)の比較で、故障率ではありません。構成比の分母は分類不可を除く修理(判定不能を含む)。"
              "色のセルは、決まった条件(A-14)で注目点になったもの。")

    # --- 件数(A-44) ---
    r = 5
    section(r, "件数")
    r += 1
    heads = ["区分", f"{pname[PERIOD_ALL]}", f"{pname[PERIOD_RECENT]}", "環境要因の記述あり\n全期間", "環境要因の記述あり\n直近"]
    for j, h in enumerate(heads, start=1):
        put(r, j, h, font=head_font, bg=COLOR_HEAD, align="center")
    side = [("製造年月不明", sm.n_no_mfg), ("日付矛盾", sm.n_negative),
            ("判定不能", sm.cells[(GROUP_ALL, PERIOD_ALL)].n_unknown), ("分類不可", sm.cells[(GROUP_ALL, PERIOD_ALL)].n_failed),
            ("製造年月が分かった割合", sm.mfg_known_pct)]
    for j, (h, _) in enumerate(side, start=7):
        put(r, j, h, font=head_font, bg=COLOR_HEAD, align="center")
    for i, g in enumerate(GROUPS, start=1):
        put(r + i, 1, gname[g], font=head_font)
        for j, p in enumerate((PERIOD_ALL, PERIOD_RECENT), start=2):
            put(r + i, j, sm.cells[(g, p)].n, align="center")
        for j, p in enumerate((PERIOD_ALL, PERIOD_RECENT), start=4):
            c = sm.cells[(g, p)]
            put(r + i, j, round(100.0 * c.env_any / c.base, 1) if c.base else 0.0, fmt=PCT_FMT, align="center")
    for j, (h, v) in enumerate(side, start=7):
        put(r + 1, j, v, fmt=PCT_FMT if h.endswith("割合") else None, align="center", font=Font(size=12))
    put(r + 4, 7, "製造年月不明・日付矛盾は全件に含め、使用期間の群からは除いています。")
    r += 6

    # --- ① 使用期間で比べる(A-42)---
    section(r, f"① 使用期間で比べる({pname[PERIOD_ALL]}): {gname[GROUP_LT]} / {gname[GROUP_GE]} / 全件")
    r += 1
    heads = ["故障の種類", "全件\n件数", "全件\n構成比",
             f"{gname[GROUP_LT]}\n件数", f"{gname[GROUP_LT]}\n構成比" + ("\n(参考: 件数少)" if sm.small(GROUP_LT, PERIOD_ALL) else ""),
             "未満 − 全件\n(ポイント)",
             f"{gname[GROUP_GE]}\n件数", f"{gname[GROUP_GE]}\n構成比" + ("\n(参考: 件数少)" if sm.small(GROUP_GE, PERIOD_ALL) else ""),
             "以上 − 全件\n(ポイント)", "注目点\n(未満 と 以上)"]
    for j, h in enumerate(heads, start=1):
        put(r, j, h, font=small_font if "件数少" in h else head_font, bg=COLOR_HEAD, align="center")
    ws.row_dimensions[r].height = 48
    r += 1
    for row in sm.rows:
        total = row.key == TOTAL_ROW
        f = head_font if total else None
        a, lt, ge = (GROUP_ALL, PERIOD_ALL), (GROUP_LT, PERIOD_ALL), (GROUP_GE, PERIOD_ALL)
        vals = [row.name, row.counts[a], row.pcts[a], row.counts[lt], row.pcts[lt],
                None if total else round(row.pcts[lt] - row.pcts[a], 1), row.counts[ge], row.pcts[ge],
                None if total else round(row.pcts[ge] - row.pcts[a], 1), "○" if row.notable_a else ""]
        fmts = [None, None, PCT_FMT, None, PCT_FMT, DIFF_FMT, None, PCT_FMT, DIFF_FMT, None]
        for j, (v, fm) in enumerate(zip(vals, fmts), start=1):
            c = put(r, j, v, font=f, fmt=fm, align=None if j == 1 else "center")
            c.border = border
            if row.notable_a and j in (5, 8, 10):
                c.fill = fill(COLOR_NOTABLE)
        r += 1
    r += 1

    # --- ② 時期で比べる(A-43)---
    section(r, f"② 時期で比べる: {pname[PERIOD_RECENT]} / {pname[PERIOD_ALL]}(全件・{gname[GROUP_LT]}・{gname[GROUP_GE]})")
    r += 1
    put(r, 1, "故障の種類", font=head_font, bg=COLOR_HEAD, align="center")
    block_cols = {}
    for k, g in enumerate(GROUPS):
        c0 = 2 + k * 5
        block_cols[g] = c0
        subs = [f"{gname[g]}\n{pname[PERIOD_ALL]} 件数", f"{pname[PERIOD_RECENT]}\n件数", f"{pname[PERIOD_ALL]}\n構成比",
                f"{pname[PERIOD_RECENT]}\n構成比" + ("\n(参考: 件数少)" if sm.small(g, PERIOD_RECENT) else ""), "直近 − 全期間\n(ポイント)"]
        for j, h in enumerate(subs):
            put(r, c0 + j, h, font=small_font if "件数少" in h else head_font, bg=COLOR_HEAD, align="center")
    ws.row_dimensions[r].height = 48
    r += 1
    b_first = r
    for row in sm.rows:
        total = row.key == TOTAL_ROW
        put(r, 1, row.name, font=head_font if total else None).border = border
        for g in GROUPS:
            c0, ga, gr = block_cols[g], (g, PERIOD_ALL), (g, PERIOD_RECENT)
            vals = [row.counts[ga], row.counts[gr], row.pcts[ga], row.pcts[gr],
                    None if total else round(row.pcts[gr] - row.pcts[ga], 1)]
            for j, (v, fm) in enumerate(zip(vals, [None, None, PCT_FMT, PCT_FMT, DIFF_FMT])):
                c = put(r, c0 + j, v, font=head_font if total else None, fmt=fm, align="center")
                c.border = border
                if row.notable_b.get(g) and j in (3, 4):
                    c.fill = fill(COLOR_NOTABLE)
        r += 1
    b_last = r - 2                       # 合計の行はグラフに入れない
    put(r, 1, f"色: 群ごとに「{pname[PERIOD_RECENT]} と {sm.axis_b[GROUP_ALL].group_b}」を比べ、3つの群をまとめて補正したうえで、"
              "注目点になったもの。")
    r += 2

    # --- 注目点の一覧(A-45)---
    section(r, "注目点の一覧(すべての観点。決まった条件 A-14 による)")
    r += 1
    if sm.findings:
        for fr in sm.findings:
            put(r, 1, finding_sentence(fr))
            r += 1
    else:
        put(r, 1, "注目点はありません(目立った差は見られません)。")
        r += 1
    if sm.lot_spikes:
        put(r, 1, f"参考: 件数が前後のロットより多いロット {len(sm.lot_spikes)} 個(「ロット」シート。生産台数が無いため参考)。")
        r += 1
    r += 1

    # --- 注記(A-45)---
    section(r, "注記")
    r += 1
    for t in sm.notes or ["特になし"]:
        put(r, 1, f"・{t}")
        r += 1
    put(r, 1, "・詳しい表は、このファイルの他のシート(全体・期間比較・使用期間_比較・ロット など)を参照してください。")

    # --- 列幅 ---
    ws.column_dimensions["A"].width = 38
    for col in "BCDEFGHIJKLMNOPQ":
        ws.column_dimensions[col].width = 11.5
    ws.freeze_panes = "B5"
    # 印刷・PDF: A3 横、幅を1ページに合わせる
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0

    # --- グラフ(構成比。全期間 vs 直近)---
    n_rows = b_last - b_first + 1
    height_cm = max(9.0, 3.0 + 0.6 * n_rows)
    anchor_row = 5
    for g in GROUPS:
        c0 = block_cols[g]
        ch = BarChart()
        ch.type = "bar"
        ch.style = 10
        ch.title = f"{gname[g]}(構成比 %)"
        ch.y_axis.title = "構成比(%)"
        ch.y_axis.number_format = "0"
        ch.y_axis.majorGridlines = None
        ch.x_axis.scaling.orientation = "maxMin"       # 表と同じ順(上から件数の多い順)
        ch.x_axis.delete = False
        ch.x_axis.tickLblSkip = 1                       # 故障の種類の名前を省かずに出す
        ch.y_axis.delete = False
        for col, title in ((c0 + 2, pname[PERIOD_ALL]), (c0 + 3, pname[PERIOD_RECENT])):
            data = Reference(ws, min_col=col, min_row=b_first, max_row=b_last)
            ch.add_data(data, titles_from_data=False)
            ch.series[-1].tx = SeriesLabel(v=title)
        ch.set_categories(Reference(ws, min_col=1, min_row=b_first, max_row=b_last))
        ch.legend.position = "b"
        ch.width, ch.height = 16, height_cm
        ws.add_chart(ch, f"{CHART_COL}{anchor_row}")
        anchor_row += int(height_cm / 0.53) + 2
