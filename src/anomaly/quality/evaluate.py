"""日本データの評価(要件定義書 G-15)。

  python anomaly_cli.py evaluate --gold <正解CSV> --run <実行フォルダ> [--local-dir ...]

正解 CSV の列: repair_id、sales_company、received_date、primary_code または primary_name、任意で failure_codes(; 区切り)。
修理IDだけでは重複があるため、販社名 + 修理ID + 受付日で、実行フォルダの classifications.csv と突き合わせる。
名称からコードへの変換は v2 の R-55a と同じ方法(正規化 + 対応表 eval_name_map.csv)。
過去の分類結果(メーカー責任の代表故障)を正解に使う場合は、定義が違うため参考値として扱う。
"""
from __future__ import annotations

import argparse
import csv
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from ..codebook import Codebook
from ..constants import SEP
from ..input_loader import parse_date


def normalize_name(s: str) -> str:
    """NFKC(半角カナ→全角、全角英数→半角)、空白除去、記号ゆれの統一、大文字小文字の無視(v2 と同じ)。"""
    t = unicodedata.normalize("NFKC", s or "")
    t = "".join(t.split())
    t = t.replace("／", "/").replace("・", "/").replace("･", "/")
    t = t.replace("【", "(").replace("】", ")").replace("[", "(").replace("]", ")")
    return t.lower()


def load_name_map(path: Path | None) -> dict[tuple[str, str], str]:
    if path is None or not Path(path).exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {(r["product_type"].strip(), normalize_name(r["past_name"])): r["code"].strip()
                for r in csv.DictReader(f) if r.get("past_name") and r.get("code")}


class NameConverter:
    def __init__(self, codebook: Codebook, name_map: dict | None = None):
        self.by_product = {p: {normalize_name(i.name): c for c, i in codebook.codes(p).items()} for p in codebook.product_ids()}
        self.name_map = name_map or {}
        self.unconverted: Counter = Counter()

    def code(self, product: str, name: str) -> str | None:
        key = normalize_name(name)
        c = self.by_product.get(product, {}).get(key) or self.name_map.get((product, key))
        if c is None:
            self.unconverted[(product, name)] += 1
        return c


def _key(sales: str, rid: str, received: str) -> tuple[str, str, str]:
    d = parse_date(received)
    return (sales.strip(), rid.strip(), d.isoformat() if d else received.strip())


def evaluate(gold_rows: list[dict], run_rows: list[dict], failures: dict[str, set[str]], codebook: Codebook,
             name_map: dict | None = None) -> dict:
    conv = NameConverter(codebook, name_map)
    run_by_key: dict[tuple, list[dict]] = {}
    for r in run_rows:
        run_by_key.setdefault(_key(r["sales_company"], r["repair_id"], r["received_date"]), []).append(r)
    res = {"gold_rows": len(gold_rows), "not_found": 0, "ambiguous": 0, "gold_empty": 0, "unconverted": 0,
           "failed_in_run": 0, "evaluated": 0, "primary_match": 0, "tp": 0, "fp": 0, "fn": 0, "set_rows": 0,
           "mismatches": []}
    for g in gold_rows:
        hits = run_by_key.get(_key(g.get("sales_company", ""), g.get("repair_id", ""), g.get("received_date", "")), [])
        if not hits:
            res["not_found"] += 1
            continue
        if len(hits) > 1:
            res["ambiguous"] += 1
            continue
        r = hits[0]
        if r.get("classify_status") == "failed":
            res["failed_in_run"] += 1
            continue
        p = r["product_type"]
        gold = (g.get("primary_code") or "").strip()
        if not gold and (g.get("primary_name") or "").strip():
            gold = conv.code(p, g["primary_name"]) or ""
            if not gold:
                res["unconverted"] += 1
                continue
        if not gold:
            res["gold_empty"] += 1
            continue
        res["evaluated"] += 1
        if r.get("primary_code") == gold:
            res["primary_match"] += 1
        else:
            res["mismatches"].append({"rec_key": r["rec_key"], "repair_id": r["repair_id"], "sales_company": r["sales_company"],
                                      "gold_code": gold, "gold_name": codebook.code_name(p, gold),
                                      "run_code": r.get("primary_code"), "run_name": codebook.code_name(p, r.get("primary_code"))})
        if (g.get("failure_codes") or "").strip():
            gs = {x.strip() for x in g["failure_codes"].split(SEP) if x.strip()}
            rs = failures.get(r["rec_key"], set())
            res["set_rows"] += 1
            res["tp"] += len(gs & rs)
            res["fp"] += len(rs - gs)
            res["fn"] += len(gs - rs)
    res["primary_accuracy"] = round(res["primary_match"] / res["evaluated"], 3) if res["evaluated"] else None
    res["precision"] = round(res["tp"] / (res["tp"] + res["fp"]), 3) if res["tp"] + res["fp"] else None
    res["recall"] = round(res["tp"] / (res["tp"] + res["fn"]), 3) if res["tp"] + res["fn"] else None
    res["unconverted_names"] = [{"product_type": p, "past_name": n, "count": c} for (p, n), c in conv.unconverted.most_common()]
    return res


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main(argv=None) -> int:
    from ..codebook import CodebookError, load_codebook
    from ..local_config import resolve_paths
    from ..outputs.files import write_csv, write_json

    ap = argparse.ArgumentParser(description="正解データ(日本)と分類結果を比べる")
    ap.add_argument("--gold", required=True)
    ap.add_argument("--run", required=True, help="classify の実行フォルダ")
    ap.add_argument("--local-dir")
    ap.add_argument("--codebook")
    a = ap.parse_args(argv)
    paths = resolve_paths(local_dir=a.local_dir, codebook=a.codebook)
    try:
        cb = load_codebook(paths.codebook)
        gold = _read(Path(a.gold))
        run_rows = _read(Path(a.run) / "classifications.csv")
        fails: dict[str, set[str]] = {}
        for f in _read(Path(a.run) / "failures.csv"):
            fails.setdefault(f["rec_key"], set()).add(f["code"])
    except (CodebookError, OSError, KeyError) as e:
        print(f"NG: {e}")
        return 1
    need = {"repair_id", "sales_company", "received_date"}
    if gold and not need <= set(gold[0]) or gold and not ({"primary_code", "primary_name"} & set(gold[0])):
        print(f"NG: 正解 CSV に必要な列がありません(必要: {', '.join(sorted(need))}、primary_code または primary_name)")
        return 1
    res = evaluate(gold, run_rows, fails, cb, load_name_map(paths.name_map))
    out = Path(a.run) / "evaluation"
    write_json(out / "evaluate_summary.json", {k: v for k, v in res.items() if k not in ("mismatches",)})
    write_csv(out / "mismatches.csv", ["rec_key", "repair_id", "sales_company", "gold_code", "gold_name", "run_code", "run_name"],
              res["mismatches"])
    write_csv(out / "unconverted_names.csv", ["product_type", "past_name", "count"], res["unconverted_names"])
    acc = "—" if res["primary_accuracy"] is None else f"{res['primary_accuracy']:.1%}"
    print(f"評価: 正解 {res['gold_rows']} 行のうち評価 {res['evaluated']} 件。主な故障の一致率 {acc}")
    if res["set_rows"]:
        print(f"  故障の種類の集合({res['set_rows']} 件): 適合率 {res['precision']}、再現率 {res['recall']}")
    print(f"  評価から外した: 実行結果に無い {res['not_found']}、キーが重複 {res['ambiguous']}、分類不可 {res['failed_in_run']}、"
          f"正解が空 {res['gold_empty']}、名称を変換できない {res['unconverted']}")
    if res["unconverted_names"]:
        print(f"  変換できなかった名称 {len(res['unconverted_names'])} 種類: {out / 'unconverted_names.csv'} を見て eval_name_map.csv に追記")
    print(f"出力: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
