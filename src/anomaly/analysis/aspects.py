"""4つの観点の集計表と比較(要件定義書 A-05〜A-24)。

各観点は AspectResult を返す。tables は「シート名 → 行(dict)のリスト」で、Excel にそのまま書ける形(O-04)。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from statistics import median

import numpy as np

from ..codebook import Codebook
from ..constants import SEP
from ..settings import Settings
from .findings import INSUFFICIENT, NOT_AVAILABLE, Comparison, aspect_judgement, judge
from .prepare import FAILED_LABEL, UNKNOWN_LABEL, ComboData, failure_codes, is_failed, is_unknown
from .stats import bh_adjust, compare

LOT_STATUSES = ("ok", "prefix_only")


def pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def ym_index(ym: str) -> int:
    y, m = ym[:7].split("-")
    return int(y) * 12 + int(m) - 1


def ym_str(i: int) -> str:
    return f"{i // 12}-{i % 12 + 1:02d}"


@dataclass
class AspectResult:
    name: str
    judgement: str = NOT_AVAILABLE
    comparisons: list[Comparison] = field(default_factory=list)
    tables: dict[str, list[dict]] = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 共通

def profile(records: list[dict], combo: ComboData) -> dict:
    """A-22: 群の構成(販社別の割合、判定不能・分類不可の割合)。"""
    n = len(records)
    n_failed = sum(1 for r in records if is_failed(r))
    n_unknown = sum(1 for r in records if is_unknown(r, combo.unknown_code))
    sales = Counter(r["sales_company"] for r in records)
    return {"n": n, "valid": n - n_failed - n_unknown, "unknown": n_unknown, "failed": n_failed,
            "unknown_pct": pct(n_unknown, n), "failed_pct": pct(n_failed, n),
            "sales": {k: pct(v, n) for k, v in sorted(sales.items(), key=lambda kv: (-kv[1], kv[0]))}}


def sales_note(pa: dict, pb: dict, threshold: float) -> str | None:
    """A-23: 販社の割合の差が閾値以上なら注記。"""
    companies = set(pa["sales"]) | set(pb["sales"])
    diffs = {c: abs(pa["sales"].get(c, 0.0) - pb["sales"].get(c, 0.0)) for c in companies}
    if diffs and max(diffs.values()) >= threshold:
        c = max(diffs, key=lambda k: (diffs[k], k))
        return (f"2つの群で販社の構成が異なります(例: {c} の割合 {pa['sales'].get(c, 0.0)}% と {pb['sales'].get(c, 0.0)}%)。"
                f"差の一部はそれによる可能性があります。")
    return None


def comparison_rows(comp: Comparison, names: dict[str, str]) -> list[dict]:
    if comp.result is None:
        return []
    notable = {f.code for f in comp.findings}
    rows = []
    for cr in comp.result.categories:
        rows.append({
            "比較": comp.label, "群A": comp.group_a, "群B": comp.group_b, "コード": cr.code, "名称": names.get(cr.code, cr.code),
            "件数A": cr.a, "割合A(%)": round(cr.pct_a, 1), "件数B": cr.b, "割合B(%)": round(cr.pct_b, 1),
            "差(ポイント)": round(cr.diff_pt, 1), "比": None if cr.ratio is None else round(cr.ratio, 2),
            "p値(補正後)": None if cr.p_adj is None else round(cr.p_adj, 4),
            "注目点": "○" if cr.code in notable else "",
        })
    return rows


def profile_rows(comp: Comparison) -> list[dict]:
    rows = []
    for side, name in (("a", comp.group_a), ("b", comp.group_b)):
        p = comp.profiles.get(side)
        if not p:
            continue
        base = {"比較": comp.label, "群": name}
        rows.append({**base, "項目": "修理件数", "件数": p["n"], "割合(%)": 100.0 if p["n"] else 0.0})
        rows.append({**base, "項目": UNKNOWN_LABEL, "件数": p["unknown"], "割合(%)": p["unknown_pct"]})
        rows.append({**base, "項目": FAILED_LABEL, "件数": p["failed"], "割合(%)": p["failed_pct"]})
        for c, share in p["sales"].items():
            rows.append({**base, "項目": f"販社: {c}", "件数": None, "割合(%)": share})
    return rows


def _two_group(aspect: str, label: str, name_a: str, name_b: str, recs_a: list[dict], recs_b: list[dict],
               combo: ComboData, s: Settings) -> Comparison:
    va = [r for r in recs_a if not is_failed(r) and not is_unknown(r, combo.unknown_code)]
    vb = [r for r in recs_b if not is_failed(r) and not is_unknown(r, combo.unknown_code)]
    res = compare([combo.category(r) for r in va], [combo.category(r) for r in vb], combo.categories)
    comp = Comparison(aspect, label, name_a, name_b, res)
    comp.profiles = {"a": profile(recs_a, combo), "b": profile(recs_b, combo)}
    comp.members = {"a": va, "b": vb}
    judge(comp, s, len(va) >= s.min_group_n and len(vb) >= s.min_group_n, combo.names)
    note = sales_note(comp.profiles["a"], comp.profiles["b"], s.region_diff_note_pt)
    if note and comp.judgement != INSUFFICIENT:
        comp.notes.append(note)
    return comp


# ---------------------------------------------------------------- 観点1: 全体

def concentration(combo: ComboData) -> dict:
    """A-05a: 全体の偏りの指標。まとめる前のコード(主な故障)で数える。"""
    n = len(combo.valid)
    ranked = sorted(combo.raw_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if not n:
        return {"n_valid": 0, "n_codes": 0, "top1": None, "top3_pct": 0.0, "codes_for_80": 0}
    cum, k80 = 0, 0
    for _, c in ranked:
        cum += c
        k80 += 1
        if cum * 100 >= 80 * n:
            break
    top1_code, top1_n = ranked[0]
    return {"n_valid": n, "n_codes": len(ranked),
            "top1": {"code": top1_code, "name": combo.name(top1_code), "n": top1_n, "pct": pct(top1_n, n)},
            "top3_pct": pct(sum(c for _, c in ranked[:3]), n), "codes_for_80": k80}


def overall(combo: ComboData, codebook: Codebook) -> AspectResult:
    """A-02〜A-06, A-05a, A-19。"""
    ar = AspectResult("overall")
    n_all, n_valid = len(combo.records), len(combo.valid)
    cat_counts = Counter(combo.category(r) for r in combo.valid)
    rows = [{"コード": c, "名称": combo.name(c), "件数": cat_counts[c], "割合(%)": pct(cat_counts[c], n_valid),
             "参考: すべての修理に対する割合(%)": pct(cat_counts[c], n_all)} for c in combo.categories]
    rows.append({"コード": combo.unknown_code, "名称": UNKNOWN_LABEL, "件数": len(combo.unknown), "割合(%)": None,
                 "参考: すべての修理に対する割合(%)": pct(len(combo.unknown), n_all)})
    rows.append({"コード": "", "名称": FAILED_LABEL, "件数": len(combo.failed), "割合(%)": None,
                 "参考: すべての修理に対する割合(%)": pct(len(combo.failed), n_all)})
    ar.tables["全体"] = rows

    raw = sorted(combo.raw_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    conc = concentration(combo)
    ar.tables["偏りの指標"] = [] if not conc["n_valid"] else [
        {"指標": "分類できた修理の件数", "値": conc["n_valid"]},
        {"指標": "故障の種類の数(主な故障)", "値": conc["n_codes"]},
        {"指標": "最も多い故障の種類", "値": f"{conc['top1']['code']} {conc['top1']['name']}"},
        {"指標": "最も多い故障の種類の割合(%)", "値": conc["top1"]["pct"]},
        {"指標": "上位3つの割合(%)", "値": conc["top3_pct"]},
        {"指標": "全体の80%になるまでの種類の数", "値": conc["codes_for_80"]},
    ]
    ar.tables["コード別(まとめる前)"] = [
        {"コード": c, "名称": combo.name(c), "件数": n, "まとめた先": combo.name(combo.category_of[c])} for c, n in raw]

    mention = Counter()
    for r in combo.valid:
        mention.update(failure_codes(r))
    ar.tables["記載割合"] = [
        {"コード": c, "名称": combo.name(c), "記載のある修理": n, "記載割合(%)": pct(n, n_valid),
         "主な故障としての件数": combo.raw_counts.get(c, 0)}
        for c, n in sorted(mention.items(), key=lambda kv: (-kv[1], kv[0]))]

    read = [r for r in combo.records if not is_failed(r)]
    factors = codebook.environment_factors(combo.product_type)
    env_counts, env_any = Counter(), 0
    for r in read:
        fs = {e.get("factor") for e in (r.get("environment") or []) if e.get("factor")}
        env_counts.update(fs)
        env_any += bool(fs)
    ar.tables["環境要因"] = (
        [{"要因": "(いずれかの記述あり)", "名称": "", "件数": env_any, "割合(%)": pct(env_any, len(read))}]
        + [{"要因": k, "名称": factors[k].name if k in factors else k, "件数": v, "割合(%)": pct(v, len(read))}
           for k, v in sorted(env_counts.items(), key=lambda kv: (-kv[1], kv[0]))])

    by_company: dict[str, list[dict]] = defaultdict(list)
    for r in combo.records:
        by_company[r["sales_company"]].append(r)
    srows = []
    for comp_name in sorted(by_company, key=lambda k: (-len(by_company[k]), k)):
        recs = by_company[comp_name]
        p = profile(recs, combo)
        cc = Counter(combo.category(r) for r in recs if not is_failed(r) and not is_unknown(r, combo.unknown_code))
        for c in combo.categories:
            if cc[c]:
                srows.append({"販社": comp_name, "修理件数": p["n"], "判定不能": p["unknown"], "分類不可": p["failed"],
                              "コード": c, "名称": combo.name(c), "件数": cc[c], "割合(%)": pct(cc[c], p["valid"])})
        if not cc:
            srows.append({"販社": comp_name, "修理件数": p["n"], "判定不能": p["unknown"], "分類不可": p["failed"],
                          "コード": "", "名称": "", "件数": 0, "割合(%)": None})
    ar.tables["販社別"] = srows

    serials = defaultdict(list)
    for r in combo.records:
        if r.get("serial"):
            serials[r["serial"]].append(r["rec_key"])
    repeats = {k: v for k, v in serials.items() if len(v) > 1}
    ar.tables["再修理の可能性"] = [{"シリアル": k, "件数": len(v), "rec_key": SEP.join(v)}
                             for k, v in sorted(repeats.items(), key=lambda kv: (-len(kv[1]), kv[0]))]

    top = [(c, combo.name(c), cat_counts[c], pct(cat_counts[c], n_valid)) for c in combo.categories if c != "OTHER_RARE"][:3]
    ar.facts = {"n_all": n_all, "n_valid": n_valid, "n_unknown": len(combo.unknown), "n_failed": len(combo.failed),
                "top": top, "env_any": env_any, "env_any_pct": pct(env_any, len(read)),
                "env": [(factors[k].name if k in factors else k, v, pct(v, len(read))) for k, v in env_counts.most_common()],
                "n_companies": len(by_company), "repeat_serials": len(repeats), "concentration": conc}
    return ar


# ---------------------------------------------------------------- 観点2: 最近の修理 vs それ以前

def recent(combo: ComboData, s: Settings, run_date: date) -> AspectResult:
    """A-07, A-08, A-10。基準日は実行日。直近は実行日の月を含めず、前月までの recent_months か月。"""
    ar = AspectResult("recent")
    run_idx = run_date.year * 12 + run_date.month - 1
    hi = run_idx - 1
    lo = run_idx - s.recent_months
    b_hi = lo - 1
    b_lo = lo - s.recent_months if s.recent_compare == "same_length" else None
    name_a = f"直近{s.recent_months}か月({ym_str(lo)}〜{ym_str(hi)})"
    name_b = (f"その前の{s.recent_months}か月({ym_str(b_lo)}〜{ym_str(b_hi)})" if b_lo is not None
              else f"それ以前(〜{ym_str(b_hi)})")
    recs_a, recs_b, current, older = [], [], 0, 0
    for r in combo.records:
        i = ym_index(r["received_date"])
        if lo <= i <= hi:
            recs_a.append(r)
        elif i > hi:
            current += 1
        elif b_lo is None or i >= b_lo:
            recs_b.append(r)
        else:
            older += 1
    comp = _two_group("recent", f"{name_a} と {name_b}", name_a, name_b, recs_a, recs_b, combo, s)
    if current:
        comp.notes.append(f"実行日の月({ym_str(run_idx)})以降の受付 {current} 件は、比較に含めていません。")
    if older:
        comp.notes.append(f"比べる期間より前の受付 {older} 件は、比較に含めていません。")
    ar.comparisons = [comp]
    ar.judgement = comp.judgement
    ar.tables["期間比較"] = comparison_rows(comp, combo.names)
    ar.tables["群の構成"] = profile_rows(comp)

    months: dict[int, list[dict]] = defaultdict(list)
    for r in combo.records:
        months[ym_index(r["received_date"])].append(r)
    mrows = []
    for i in sorted(months):
        recs = months[i]
        p = profile(recs, combo)
        mrows.append({"年月": ym_str(i), "コード": "", "名称": "修理件数(合計)", "件数": p["n"], "割合(%)": None})
        mrows.append({"年月": ym_str(i), "コード": "", "名称": UNKNOWN_LABEL, "件数": p["unknown"], "割合(%)": p["unknown_pct"]})
        mrows.append({"年月": ym_str(i), "コード": "", "名称": FAILED_LABEL, "件数": p["failed"], "割合(%)": p["failed_pct"]})
        cc = Counter(combo.category(r) for r in recs if not is_failed(r) and not is_unknown(r, combo.unknown_code))
        for c in combo.categories:
            if cc[c]:
                mrows.append({"年月": ym_str(i), "コード": c, "名称": combo.name(c), "件数": cc[c], "割合(%)": pct(cc[c], p["valid"])})
    ar.tables["月別推移"] = mrows
    ar.facts = {"name_a": name_a, "name_b": name_b, "current_excluded": current,
                "latest": max((r["received_date"] for r in combo.records), default=None),
                "first": min((r["received_date"] for r in combo.records), default=None)}
    return ar


# ---------------------------------------------------------------- 観点3: 使用期間

def bin_labels(bins: tuple[int, ...]) -> list[str]:
    labels = [f"{a}〜{b - 1}か月" for a, b in zip(bins, bins[1:])]
    return labels + [f"{bins[-1]}か月以上"]


def bin_of(months: int, bins: tuple[int, ...]) -> int:
    k = 0
    for j, b in enumerate(bins):
        if months >= b:
            k = j
    return k


def _quartiles(values: list[int]) -> tuple[float, float, float]:
    q1, q2, q3 = np.percentile(values, [25, 50, 75])
    return round(float(q1), 1), round(float(q2), 1), round(float(q3), 1)


def usage(combo: ComboData, s: Settings) -> AspectResult:
    """A-11〜A-13c。"""
    ar = AspectResult("usage")
    usable = [r for r in combo.records if r.get("usage_status") == "ok" and r.get("usage_months") is not None]
    ar.facts = {"n_usable": len(usable), "n_all": len(combo.records)}
    if not usable:
        ar.judgement = NOT_AVAILABLE
        return ar
    labels = bin_labels(s.usage_bins)
    months_all = [r["usage_months"] for r in usable]
    dist = Counter(bin_of(m, s.usage_bins) for m in months_all)
    ar.tables["使用期間_分布"] = [{"区切り": labels[k], "件数": dist[k], "割合(%)": pct(dist[k], len(usable))}
                             for k in range(len(labels))]
    q1, q2, q3 = _quartiles(months_all)
    valid = [r for r in usable if not is_failed(r) and not is_unknown(r, combo.unknown_code)]
    by_cat: dict[str, list[int]] = defaultdict(list)
    for r in valid:
        by_cat[combo.category(r)].append(r["usage_months"])
    rows = []
    for c in combo.categories:
        if by_cat[c]:
            a, b, d = _quartiles(by_cat[c])
            rows.append({"コード": c, "名称": combo.name(c), "件数": len(by_cat[c]), "中央値(か月)": b,
                         "第1四分位(か月)": a, "第3四分位(か月)": d})
    ar.tables["使用期間_コード別"] = rows
    cross = []
    for k, lab in enumerate(labels):
        recs = [r for r in valid if bin_of(r["usage_months"], s.usage_bins) == k]
        cc = Counter(combo.category(r) for r in recs)
        for c in combo.categories:
            if cc[c]:
                cross.append({"区切り": lab, "コード": c, "名称": combo.name(c), "件数": cc[c], "割合(%)": pct(cc[c], len(recs))})
    ar.tables["使用期間_クロス"] = cross

    e = s.usage_split_months
    name_a, name_b = f"使用期間{e}か月未満", f"使用期間{e}か月以上"
    comp = _two_group("usage", f"{name_a} と {name_b}", name_a, name_b,
                      [r for r in usable if r["usage_months"] < e], [r for r in usable if r["usage_months"] >= e], combo, s)
    ar.comparisons = [comp]
    ar.judgement = comp.judgement
    ar.tables["使用期間_比較"] = comparison_rows(comp, combo.names)
    ar.tables["群の構成"] = profile_rows(comp)
    ar.facts.update({"median": q2, "q1": q1, "q3": q3, "name_a": name_a, "name_b": name_b})
    return ar


# ---------------------------------------------------------------- 観点4: 製造ロット

def lots(combo: ComboData, s: Settings) -> AspectResult:
    """A-15〜A-15d, A-24, S-18。"""
    ar = AspectResult("lot")
    lot_recs = [r for r in combo.records if r.get("lot_code") and r.get("mfg_status") in LOT_STATUSES]
    ar.facts = {"n_lot_records": len(lot_recs), "n_lots": 0, "n_tested": 0, "spikes": [], "prefix_only": False}
    if not lot_recs:
        ar.judgement = NOT_AVAILABLE
        return ar
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in lot_recs:
        groups[(r.get("lot_order"), r["lot_code"])].append(r)
    keys = sorted(groups, key=lambda k: (k[0] is None, k[0] if k[0] is not None else 0, k[1]))
    total = len(lot_recs)

    def lot_name(k):
        ym = groups[k][0].get("mfg_ym")
        return f"{ym}(頭桁 {k[1]})" if ym else f"頭桁 {k[1]}"

    counts = [len(groups[k]) for k in keys]
    spikes = set()
    for i, k in enumerate(keys):
        nb = counts[max(0, i - s.lot_neighbor):i] + counts[i + 1:i + 1 + s.lot_neighbor]
        if nb and counts[i] >= s.lot_min_n and counts[i] >= s.lot_spike_ratio * median(nb):
            spikes.add(k)

    def valid_of(recs):
        return [r for r in recs if not is_failed(r) and not is_unknown(r, combo.unknown_code)]

    def compare_units(units: list[tuple[str, str, list[dict]]], kind: str) -> list[Comparison]:
        """units: (表示名, 頭桁など, 修理)。各まとまり vs 他のロットすべて。補正は同じ種類の比較をまとめて行う。"""
        comps, pending = [], []
        for name, code, recs in units:
            unit_valid = valid_of(recs)
            if len(unit_valid) < s.lot_min_n:
                continue
            ids = {id(r) for r in recs}
            others = [r for r in lot_recs if id(r) not in ids]
            other_valid = valid_of(others)
            res = compare([combo.category(r) for r in unit_valid], [combo.category(r) for r in other_valid],
                          combo.categories, adjust=False)
            comp = Comparison("lot", f"ロット {name} と 他のロット", f"ロット {name}", "他のロット", res,
                              lot_code=code, lot_kind=kind)
            comp.profiles = {"a": profile(recs, combo), "b": profile(others, combo)}
            comp.members = {"a": unit_valid, "b": other_valid}
            comps.append(comp)
            pending.append((comp, len(other_valid) >= s.min_group_n))
        # ロット × コードの検定は、同じ種類の比較をすべてまとめて補正する(ロットが多いと偶然の差が出やすいため)
        allcats = [cr for comp in comps for cr in comp.result.categories]
        for cr, adj in zip(allcats, bh_adjust([cr.p_raw for cr in allcats])):
            cr.p_adj = adj
        for comp, others_ok in pending:
            judge(comp, s, others_ok, combo.names)
            note = sales_note(comp.profiles["a"], comp.profiles["b"], s.region_diff_note_pt)
            if note and comp.judgement != INSUFFICIENT:
                comp.notes.append(note)
        return comps

    singles = compare_units([(lot_name(k), k[1], groups[k]) for k in keys], "single")
    tested = {(c.lot_code, c.group_a) for c in singles}

    # A-15d: 製造の順で隣り合う lot_window ロットずつまとめる(製造の順の値で区切る。修理の無い月・頭桁も1つと数える)
    windows: list[tuple[str, str, list[dict], list[tuple]]] = []
    ordered = [k for k in keys if k[0] is not None]
    if s.lot_window > 1 and ordered:
        base = min(k[0] for k in ordered)
        by_w: dict[int, list[tuple]] = defaultdict(list)
        for k in ordered:
            by_w[(k[0] - base) // s.lot_window].append(k)
        has_ym = any(groups[k][0].get("mfg_ym") for k in ordered)
        width = max(len(k[1]) for k in ordered)
        for w in sorted(by_w):
            lo, hi = base + w * s.lot_window, base + (w + 1) * s.lot_window - 1
            name = f"{ym_str(lo)}〜{ym_str(hi)}" if has_ym else f"頭桁 {lo:0{width}d}〜{hi:0{width}d}"
            members = by_w[w]
            windows.append((name, members[0][1], [r for k in members for r in groups[k]], members))
    # ロットが1つだけのまとまりは、1ロットの比較と同じになるため比べない
    window_comps = compare_units([(n, c, recs) for n, c, recs, m in windows if len(m) >= 2], "window")
    comps = singles + window_comps
    ar.comparisons = comps
    ar.judgement = aspect_judgement(comps) if comps else INSUFFICIENT
    window_tested = {c.group_a for c in window_comps}
    ar.tables["ロット_まとめ"] = [
        {"まとまり": name, "含むロット": SEP.join(lot_name(k) for k in members), "ロットの数": len(members),
         "件数": len(recs), "割合(%)": pct(len(recs), total), "検定の対象": "○" if f"ロット {name}" in window_tested else ""}
        for name, _, recs, members in windows]

    lrows, crow = [], []
    for k in keys:
        recs = groups[k]
        us = [r["usage_months"] for r in recs if r.get("usage_status") == "ok" and r.get("usage_months") is not None]
        lrows.append({"ロット": lot_name(k), "頭桁": k[1], "製造の順": k[0], "製造年月": recs[0].get("mfg_ym") or "",
                      "件数": len(recs), "割合(%)": pct(len(recs), total),
                      "使用期間の中央値(か月)": round(float(median(us)), 1) if us else None,
                      "件数の偏り(参考)": "参考" if k in spikes else "",
                      "検定の対象": "○" if (k[1], f"ロット {lot_name(k)}") in tested else ""})
        cc = Counter(combo.category(r) for r in valid_of(recs))
        nv = sum(cc.values())
        for c in combo.categories:
            if cc[c]:
                crow.append({"ロット": lot_name(k), "コード": c, "名称": combo.name(c), "件数": cc[c], "割合(%)": pct(cc[c], nv)})
    ar.tables["ロット"] = lrows
    ar.tables["ロット_構成"] = crow
    ar.tables["ロット_比較"] = [row for comp in comps for row in comparison_rows(comp, combo.names)]

    prefix_only = any(r.get("mfg_status") == "prefix_only" for r in lot_recs)
    if prefix_only:
        ar.notes.append("頭桁だけで分けたロットは、周期(96 の次が 01 など)があっても区別できないため、"
                        "同じ頭桁に異なる時期の製造が混ざる可能性があります。")
    if any(r.get("mfg_ym") for r in lot_recs):
        ar.notes.append("古いロットほど市場での期間が長いため、修理の件数・内容は使用期間の違いの影響を受けます"
                        "(ロットの表に使用期間の中央値を示しています)。")
    ar.facts.update({"n_lots": len(keys), "n_tested": len(singles), "n_windows_tested": len(window_comps),
                     "lot_window": s.lot_window, "prefix_only": prefix_only,
                     "spikes": [(lot_name(k), len(groups[k])) for k in keys if k in spikes]})
    return ar


# ---------------------------------------------------------------- 除外・変換不能

def exclusions(combo: ComboData) -> list[dict]:
    n = len(combo.records)
    rows = [{"区分": "分類", "状態": "分類できた", "件数": len(combo.valid), "割合(%)": pct(len(combo.valid), n)},
            {"区分": "分類", "状態": UNKNOWN_LABEL, "件数": len(combo.unknown), "割合(%)": pct(len(combo.unknown), n)},
            {"区分": "分類", "状態": FAILED_LABEL, "件数": len(combo.failed), "割合(%)": pct(len(combo.failed), n)}]
    for key, label in (("mfg_status", "製造年月"), ("usage_status", "使用期間")):
        cnt = Counter(r.get(key) or "(なし)" for r in combo.records)
        rows += [{"区分": label, "状態": k, "件数": v, "割合(%)": pct(v, n)} for k, v in sorted(cnt.items())]
    warn = Counter(w.strip() for r in combo.records for w in (r.get("mfg_warning") or "").split(SEP) if w.strip())
    rows += [{"区分": "製造年月の注意", "状態": k, "件数": v, "割合(%)": pct(v, n)} for k, v in sorted(warn.items())]
    return rows


@dataclass
class ComboAnalysis:
    combo: ComboData
    overall: AspectResult
    recent: AspectResult
    usage: AspectResult
    lot: AspectResult
    exclusions: list[dict]

    def aspects(self) -> list[AspectResult]:
        return [self.recent, self.usage, self.lot]

    def comparisons(self) -> list[Comparison]:
        return [c for a in self.aspects() for c in a.comparisons]

    def n_findings(self) -> int:
        return sum(len(c.findings) for c in self.comparisons())


def analyze_combo(combo: ComboData, codebook: Codebook, s: Settings, run_date: date) -> ComboAnalysis:
    return ComboAnalysis(combo, overall(combo, codebook), recent(combo, s, run_date), usage(combo, s), lots(combo, s),
                         exclusions(combo))
