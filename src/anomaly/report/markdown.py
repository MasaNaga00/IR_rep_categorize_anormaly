"""組み合わせごとのレポート(report.md)の組み立て(要件定義書 T-01、T-02 層1、T-07、T-08)。"""
from __future__ import annotations

from collections import Counter

from ..analysis.aspects import ComboAnalysis
from ..analysis.findings import JUDGEMENT_NAMES, NOT_AVAILABLE, Comparison
from ..constants import SEP
from ..settings import Settings
from .examples import pick_examples
from .templates import comparison_sentences, lot_summary_sentences, note_sentences, p1

ASPECT_TITLES = {"recent": "最近の修理とそれ以前の比較", "usage": "使用期間", "lot": "製造ロット"}
SOURCE_NAMES = {"user": "ユーザ", "repair": "修理者", "internal": "内部"}


def prerequisites(ca: ComboAnalysis, s: Settings, run_info: dict) -> list[str]:
    """T-07: データの前提。"""
    c, f = ca.combo, ca.overall.facts
    n = f["n_all"]
    rf = ca.recent.facts
    mfg = Counter(r.get("mfg_status") or "" for r in c.records)
    warn = Counter(w.strip() for r in c.records for w in (r.get("mfg_warning") or "").split(SEP) if w.strip())
    lines = [
        f"修理件数: {n} 件(分類できた {f['n_valid']} 件 / 判定不能 {f['n_unknown']} 件 / 分類不可 {f['n_failed']} 件)",
        f"受付日の期間: {rf.get('first')} 〜 {rf.get('latest')}(実行日 {run_info['run_date']})",
        f"製造年月が求められた修理: {mfg['ok']} 件({p1(100 * mfg['ok'] / n if n else 0)})、"
        f"ロットだけ分かる修理: {mfg['prefix_only']} 件、それ以外: {n - mfg['ok'] - mfg['prefix_only']} 件"
        + ("" if run_info.get("serial_result") else "(シリアルの変換結果は指定されていません)"),
    ]
    if warn:
        lines.append("製造年月の注意: " + "、".join(f"{k} {v} 件" for k, v in sorted(warn.items())))
    excluded = run_info.get("internal_excluded")
    if excluded:
        lines.append(f"内部コメントを分類に使っていない販社: {', '.join(excluded)}")
    lines += [
        "割合の分母は、判定不能・分類不可を除いた件数です。",
        "件数の比較は構成比(割合)の比較で、故障率ではありません(出荷台数・生産台数のデータがないため)。",
        "海外データの分類精度は、正解データでは検証していません。",
    ]
    lines += run_info.get("warnings", [])
    return lines


def concentration_sentences(conc: dict, s: Settings) -> list[str]:
    """A-05a: 偏りの大きさを数字で示す。判断の言葉は、決まった閾値でだけ付ける。"""
    if not conc["n_valid"]:
        return []
    t = conc["top1"]
    out = [f"最も多い『{t['name']}』が全体の {p1(t['pct'])}、上位3つで {p1(conc['top3_pct'])} を占めます。"
           f"全体の80%は {conc['codes_for_80']} 種類の故障で占められます(故障の種類は全部で {conc['n_codes']} 種類)。"]
    if t["pct"] >= s.concentration_high_pct:
        out.append(f"特定の故障の種類(『{t['name']}』)に修理が集中しています。")
    elif t["pct"] < s.concentration_low_pct:
        out.append("特定の故障の種類への偏りは小さいです。")
    if conc["n_valid"] < s.min_group_n:
        out.append(f"注記: 分類できた修理が {conc['n_valid']} 件と少ないため、割合は参考値です。")
    return out


def _overall_sentences(ca: ComboAnalysis, s: Settings) -> list[str]:
    f = ca.overall.facts
    out = []
    if f["n_valid"] == 0:
        return ["分類できた修理がないため、故障の種類の傾向は示せません。"]
    out += concentration_sentences(f["concentration"], s)
    tops = "、".join(f"『{name}』{n} 件({p1(p)})" for _, name, n, p in f["top"])
    out.append(f"分類できた {f['n_valid']} 件のうち、件数の多い故障の種類は {tops} です。")
    if f["env_any"]:
        envs = "、".join(f"{name} {n} 件" for name, n, _ in f["env"][:3])
        out.append(f"使用環境・取り扱い(落下・水濡れなど)の記述がある修理は {f['env_any']} 件({p1(f['env_any_pct'])})です({envs})。")
    else:
        out.append("使用環境・取り扱い(落下・水濡れなど)の記述がある修理はありません。")
    if f["repeat_serials"]:
        out.append(f"同じシリアルの修理が2回以上あるものが {f['repeat_serials']} 件あります(再修理の可能性。表「再修理の可能性」)。")
    return out


def _comparison_block(comp: Comparison, s: Settings) -> list[str]:
    lines = comparison_sentences(comp, s) + note_sentences(comp.notes)
    if comp.result is not None and comp.result.chi2_reference and comp.judgement != "insufficient":
        lines.append("注記: 件数の少ない種類が多く、全体の構成の検定(カイ二乗)は参考値です。")
    return lines


def build_report(ca: ComboAnalysis, s: Settings, run_info: dict) -> str:
    c = ca.combo
    md = [f"# 修理傾向の解析: {c.model} × {c.part_no}", "",
          f"製品種別: {c.product_type} / 実行日: {run_info['run_date']}", "", "## 前提", ""]
    md += [f"- {x}" for x in prerequisites(ca, s, run_info)]
    md += ["", "## 1. 全体の傾向", ""] + [f"- {x}" for x in _overall_sentences(ca, s)]

    md += ["", f"## 2. {ASPECT_TITLES['recent']}", ""]
    md += [f"- {x}" for x in _comparison_block(ca.recent.comparisons[0], s)]

    md += ["", f"## 3. {ASPECT_TITLES['usage']}", ""]
    u = ca.usage
    if u.judgement == NOT_AVAILABLE and not u.comparisons:
        md.append("- 製造年月の情報(シリアルの変換結果)が無いため、使用期間の解析はできません。")
    else:
        uf = u.facts
        md.append(f"- 使用期間が分かる修理 {uf['n_usable']} 件の使用期間の中央値は {uf['median']} か月"
                  f"(第1四分位 {uf['q1']} か月、第3四分位 {uf['q3']} か月)です。")
        md += [f"- {x}" for x in _comparison_block(u.comparisons[0], s)]
        md.append("- 注記: 使用期間は製造年月からの期間で、製造から販売までの在庫期間を含みます(購入日からではありません)。")

    md += ["", f"## 4. {ASPECT_TITLES['lot']}", ""]
    lot = ca.lot
    md += [f"- {x}" for x in lot_summary_sentences(lot.comparisons, lot.judgement, s, lot.facts.get("spikes", []))]
    md += [f"- 注記: {x}" for x in lot.notes]
    for comp in lot.comparisons:
        md += [f"- 注記({comp.group_a}): {x}" for x in comp.notes]

    md += ["", "## 代表事例", "", "注目点のうち、比べた群(直近の期間・使用期間の短い側・そのロット)で割合が高い故障の事例です。", ""]
    any_example = False
    for comp in ca.comparisons():
        for f in focus_findings(comp):
            group = comp.group_a
            ex = pick_examples(comp, f, s.examples_per_finding, s.random_seed, c.key)
            md.append(f"### {ASPECT_TITLES[comp.aspect]}: 『{f.name}』({group})")
            md.append("")
            if not ex:
                md.append("- (抜き出しのある事例がありません)")
            for e in ex:
                src = SOURCE_NAMES.get(e["source"], e["source"])
                head = f"[{e['sales_company']} / {e['lang']}{' / ' + src if src else ''}] 修理ID {e['repair_id']}"
                text = e["excerpt"] or "(抜き出しなし)"
                ja = e["excerpt_ja"]
                md.append(f"- {head}: {text}" + (f" (訳: {ja})" if ja and ja != text else ""))
            md.append("")
            any_example = True
    if not any_example:
        md.append("- 比べた群で割合が高い注目点が無いため、代表事例はありません。")
        md.append("")

    md += ["## 判定の一覧", "", "| 観点 | 比較 | 判定 | 注目点の数 |", "|---|---|---|---|"]
    for comp in ca.comparisons():
        md.append(f"| {ASPECT_TITLES[comp.aspect]} | {comp.label} | {JUDGEMENT_NAMES[comp.judgement]} | {len(comp.findings)} |")
    if not ca.comparisons():
        md.append("| ー | ー | 比較なし | 0 |")
    md.append("")
    md.append(f"注目点の条件: 両群とも {s.min_group_n} 件以上(ロットは {s.lot_min_n} 件以上)、補正後の p 値 < {s.alpha}、"
              f"割合の差 {s.min_diff_pt:g} ポイント以上かつ該当 {s.min_code_n} 件以上。")
    return "\n".join(md) + "\n"


def focus_findings(comp: Comparison):
    """T-08: 代表事例を示す注目点。比べた群(群A)で割合が高いものだけ(低いものは構成比の影響のことが多いため)。"""
    return [f for f in comp.findings if f.higher == "a"]


def all_examples(ca: ComboAnalysis, s: Settings) -> list[dict]:
    """代表事例の表(tables.xlsx の「代表事例」シート)。"""
    rows = []
    for comp in ca.comparisons():
        for f in focus_findings(comp):
            group = comp.group_a
            for e in pick_examples(comp, f, s.examples_per_finding, s.random_seed, ca.combo.key):
                rows.append({"観点": ASPECT_TITLES[comp.aspect], "比較": comp.label, "群": group, "コード": f.code, "名称": f.name,
                             "rec_key": e["rec_key"], "修理ID": e["repair_id"], "販社": e["sales_company"], "言語": e["lang"],
                             "記載": SOURCE_NAMES.get(e["source"], e["source"]), "原文": e["excerpt"], "日本語訳": e["excerpt_ja"]})
    return rows
