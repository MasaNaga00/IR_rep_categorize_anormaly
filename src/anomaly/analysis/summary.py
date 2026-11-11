"""サマリーシートの集計(要件定義書 A-40〜A-45)。

3つの群(全件 / 使用期間 N か月未満 / N か月以上)× 2つの期間(全期間 / 直近)の6つの区分で、
故障の種類(主な故障)の件数と構成比を出す。

- 使用期間 = 受付日の年月 − 製造年月(S-08)。usage_status が ok と over_max の修理を、N か月未満 / 以上に分ける。
  製造年月が無い(no_mfg_ym・結合なし)と日付矛盾(negative)は、全件には入れ、使用期間の群には入れない(A-40)。
- 構成比の分母は、分類不可を除く修理。判定不能は1行として分母に入れる(A-41)。
- 色付け(注目点)は決まったルール(A-14)だけで決める。直近も使用期間の群も全件の一部なので、
  「全件との差」「全期間との差」は検定しない。代わりに、重ならない2群を検定する(A-42、A-43)。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from ..settings import Settings
from .aspects import ComboAnalysis, pct, profile, sales_note, ym_index, ym_str
from .findings import INSUFFICIENT, Comparison, judge
from .prepare import RARE_CODE, UNKNOWN_LABEL, ComboData, is_failed, is_unknown
from .stats import bh_adjust, compare

GROUP_ALL, GROUP_LT, GROUP_GE = "all", "lt", "ge"
GROUPS = (GROUP_ALL, GROUP_LT, GROUP_GE)
PERIOD_ALL, PERIOD_RECENT = "all", "recent"
PERIODS = (PERIOD_ALL, PERIOD_RECENT)
USAGE_GROUP_STATUSES = ("ok", "over_max")          # 使用期間の群に入れる usage_status(A-40)
UNKNOWN_ROW = "__UNKNOWN__"
OTHERS_ROW = "__OTHERS__"
TOTAL_ROW = "__TOTAL__"


@dataclass
class CellStat:
    """1つの区分(群 × 期間)。"""
    n: int = 0                 # 修理件数
    n_failed: int = 0          # 分類不可
    n_unknown: int = 0         # 判定不能
    env_any: int = 0           # 環境要因の記述がある修理(分類不可を除く)
    counts: Counter = field(default_factory=Counter)   # 行のキー → 件数(分類不可を除く)

    @property
    def base(self) -> int:
        """構成比の分母(分類不可を除く。A-41)。"""
        return self.n - self.n_failed


@dataclass
class SummaryRow:
    key: str                   # コード / OTHERS_ROW / UNKNOWN_ROW / TOTAL_ROW
    name: str
    counts: dict = field(default_factory=dict)     # (群, 期間) → 件数
    pcts: dict = field(default_factory=dict)       # (群, 期間) → 構成比(%)
    notable_a: bool = False                        # 軸A(使用期間)で注目点
    notable_b: dict = field(default_factory=dict)  # 群 → 軸B(時期)で注目点


@dataclass
class Summary:
    model: str
    part_no: str
    run_date: str
    recent_months: int
    split_months: int
    recent_from: str
    recent_to: str
    first_date: str | None
    last_date: str | None
    group_names: dict
    period_names: dict
    cells: dict                       # (群, 期間) → CellStat
    rows: list[SummaryRow]
    others_count: int                 # 「上記以外」にまとめた種類の数
    n_no_mfg: int
    n_negative: int
    mfg_known_pct: float
    axis_a: Comparison
    axis_b: dict                      # 群 → Comparison
    findings: list[dict]              # 注目点の一覧(A-45)
    lot_spikes: list
    notes: list[str]
    min_group_n: int

    def small(self, g: str, p: str) -> bool:
        """A-44: 構成比の分母が min_group_n 未満の区分(参考扱い)。"""
        return self.cells[(g, p)].base < self.min_group_n


def usage_group(r: dict, split: int) -> str | None:
    """A-40: GROUP_LT / GROUP_GE / None(使用期間の群に入らない)。"""
    if r.get("usage_status") not in USAGE_GROUP_STATUSES or r.get("usage_months") is None:
        return None
    return GROUP_LT if int(r["usage_months"]) < split else GROUP_GE


def _row_key(r: dict, combo: ComboData) -> str:
    return UNKNOWN_ROW if is_unknown(r, combo.unknown_code) else combo.category(r)


def _cell(records: list[dict], combo: ComboData) -> CellStat:
    c = CellStat(n=len(records))
    for r in records:
        if is_failed(r):
            c.n_failed += 1
            continue
        if is_unknown(r, combo.unknown_code):
            c.n_unknown += 1
        c.counts[_row_key(r, combo)] += 1
        if any(e.get("factor") for e in (r.get("environment") or [])):
            c.env_any += 1
    return c


def _valid(records: list[dict], combo: ComboData) -> list[dict]:
    return [r for r in records if not is_failed(r) and not is_unknown(r, combo.unknown_code)]


def _comparison(aspect: str, label: str, name_a: str, name_b: str, recs_a: list[dict], recs_b: list[dict],
                combo: ComboData, adjust: bool) -> Comparison:
    va, vb = _valid(recs_a, combo), _valid(recs_b, combo)
    res = compare([combo.category(r) for r in va], [combo.category(r) for r in vb], combo.categories, adjust=adjust)
    comp = Comparison(aspect, label, name_a, name_b, res)
    comp.profiles = {"a": profile(recs_a, combo), "b": profile(recs_b, combo)}
    comp.members = {"a": va, "b": vb}
    return comp


def _finish(comp: Comparison, s: Settings, combo: ComboData) -> Comparison:
    judge(comp, s, comp.na >= s.min_group_n and comp.nb >= s.min_group_n, combo.names)
    note = sales_note(comp.profiles["a"], comp.profiles["b"], s.region_diff_note_pt)
    if note and comp.judgement != INSUFFICIENT:
        comp.notes.append(f"{comp.label}: {note}")
    return comp


def finding_rows(label_prefix: str, comps: list[Comparison]) -> list[dict]:
    rows = []
    for c in comps:
        for f in c.findings:
            rows.append({"観点": label_prefix, "比較": c.label, "故障の種類": f.name,
                         "群A": c.group_a, "割合A(%)": round(f.pct_a, 1), "群B": c.group_b, "割合B(%)": round(f.pct_b, 1),
                         "差(ポイント)": round(f.diff_pt, 1), "p値(補正後)": round(f.p_adj, 4)})
    return rows


ASPECT_LABELS = {"recent": "時期(全件)", "usage": "使用期間", "lot": "製造ロット"}


def build_summary(ca: ComboAnalysis, s: Settings, run_date: date) -> Summary:
    combo = ca.combo
    split, n_recent = s.usage_split_months, s.recent_months
    run_idx = run_date.year * 12 + run_date.month - 1
    hi, lo = run_idx - 1, run_idx - n_recent
    before_lo = lo - n_recent if s.recent_compare == "same_length" else None

    def in_recent(r):
        return lo <= ym_index(r["received_date"]) <= hi

    def in_before(r):
        i = ym_index(r["received_date"])
        return i < lo and (before_lo is None or i >= before_lo)

    group_recs = {GROUP_ALL: list(combo.records), GROUP_LT: [], GROUP_GE: []}
    n_no_mfg = n_negative = 0
    for r in combo.records:
        g = usage_group(r, split)
        if g:
            group_recs[g].append(r)
        elif r.get("usage_status") == "negative":
            n_negative += 1
        else:
            n_no_mfg += 1
    group_names = {GROUP_ALL: "全件", GROUP_LT: f"製造後{split}か月未満", GROUP_GE: f"製造後{split}か月以上"}
    period_names = {PERIOD_ALL: "全期間", PERIOD_RECENT: f"直近{n_recent}か月"}
    cells = {}
    for g in GROUPS:
        cells[(g, PERIOD_ALL)] = _cell(group_recs[g], combo)
        cells[(g, PERIOD_RECENT)] = _cell([r for r in group_recs[g] if in_recent(r)], combo)

    # 軸A(A-42): 全期間の N か月未満 vs 以上
    axis_a = _finish(_comparison("summary_usage", f"{group_names[GROUP_LT]} と {group_names[GROUP_GE]}(全期間)",
                                 group_names[GROUP_LT], group_names[GROUP_GE], group_recs[GROUP_LT], group_recs[GROUP_GE],
                                 combo, adjust=True), s, combo)
    # 軸B(A-43): 群ごとに 直近 vs それ以前。3つの比較をまとめて補正する
    before_name = (f"その前の{n_recent}か月" if before_lo is not None else "それ以前")
    axis_b = {}
    for g in GROUPS:
        recs = group_recs[g]
        axis_b[g] = _comparison("summary_recent", f"{group_names[g]}: {period_names[PERIOD_RECENT]} と {before_name}",
                                period_names[PERIOD_RECENT], before_name,
                                [r for r in recs if in_recent(r)], [r for r in recs if in_before(r)], combo, adjust=False)
    pooled = [cr for g in GROUPS if axis_b[g].na >= s.min_group_n and axis_b[g].nb >= s.min_group_n
              for cr in axis_b[g].result.categories]
    for cr, adj in zip(pooled, bh_adjust([cr.p_raw for cr in pooled])):
        cr.p_adj = adj
    for g in GROUPS:
        _finish(axis_b[g], s, combo)

    # 行(A-41): 全件・全期間の件数が多い順に上位 N。注目点になった種類は N を超えても出す
    base_counts = cells[(GROUP_ALL, PERIOD_ALL)].counts
    ranked = sorted([c for c in combo.categories if c != RARE_CODE], key=lambda c: (-base_counts[c], c))
    notable_a = {f.code for f in axis_a.findings}
    notable_b = {g: {f.code for f in axis_b[g].findings} for g in GROUPS}
    must = notable_a.union(*notable_b.values())
    shown = [c for i, c in enumerate(ranked) if i < s.summary_top_n or c in must]
    rest = [c for c in combo.categories if c not in shown]
    keys = shown + ([OTHERS_ROW] if rest else []) + [UNKNOWN_ROW]

    def row_count(key, cell: CellStat) -> int:
        if key == OTHERS_ROW:
            return sum(cell.counts[c] for c in rest)
        return cell.counts[key]

    names = {OTHERS_ROW: f"上記以外({len(rest)}種類)", UNKNOWN_ROW: UNKNOWN_LABEL,
             TOTAL_ROW: "合計(分類不可を除く)"}
    rows = []
    for key in keys + [TOTAL_ROW]:
        row = SummaryRow(key, names.get(key) or combo.name(key))
        for gp, cell in cells.items():
            n = cell.base if key == TOTAL_ROW else row_count(key, cell)
            row.counts[gp] = n
            row.pcts[gp] = pct(n, cell.base)
        row.notable_a = key in notable_a
        row.notable_b = {g: key in notable_b[g] for g in GROUPS}
        rows.append(row)

    # 注目点の一覧(A-45): 4つの観点の比較 + サマリーの比較
    findings = []
    for a in ca.aspects():
        findings += finding_rows(ASPECT_LABELS.get(a.name, a.name), a.comparisons)
    findings += finding_rows("サマリー①(使用期間)", [axis_a])
    findings += finding_rows("サマリー②(時期)", [axis_b[g] for g in GROUPS])

    notes = []
    for comp in [axis_a] + [axis_b[g] for g in GROUPS]:
        notes += comp.notes
    after = sum(1 for r in combo.records if ym_index(r["received_date"]) > hi)
    if after:
        notes.append(f"実行日の月({ym_str(run_idx)})以降の受付 {after} 件は、全期間には含め、直近には含めていません。")
    small = [f"{group_names[g]}・{period_names[p]}" for g in GROUPS for p in PERIODS if cells[(g, p)].base < s.min_group_n]
    if small:
        notes.append(f"件数が {s.min_group_n} 件未満の区分は、構成比が数件で大きく変わるため参考扱いです: {'、'.join(small)}")
    if rest:
        notes.append(f"「上記以外」は、件数の少ない {len(rest)} 種類をまとめたものです。内訳は「全体」シートを参照してください。")
    if must - set(ranked[:s.summary_top_n]):
        notes.append(f"上位 {s.summary_top_n} 種類に入らないが、注目点になった故障の種類も表に出しています。")
    n_all = len(combo.records)
    known = len(group_recs[GROUP_LT]) + len(group_recs[GROUP_GE])
    return Summary(
        model=combo.model, part_no=combo.part_no, run_date=run_date.isoformat(), recent_months=n_recent,
        split_months=split, recent_from=ym_str(lo), recent_to=ym_str(hi),
        first_date=min((r["received_date"] for r in combo.records), default=None),
        last_date=max((r["received_date"] for r in combo.records), default=None),
        group_names=group_names, period_names=period_names, cells=cells, rows=rows, others_count=len(rest),
        n_no_mfg=n_no_mfg, n_negative=n_negative, mfg_known_pct=pct(known, n_all),
        axis_a=axis_a, axis_b=axis_b, findings=findings, lot_spikes=list(ca.lot.facts.get("spikes", [])),
        notes=notes, min_group_n=s.min_group_n)
