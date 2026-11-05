"""2つの実行結果の比較(要件定義書 G-30)。

  python anomaly_cli.py compare-runs --a <実行フォルダA> --b <実行フォルダB> [--sales-company CMJ] [--output <CSV>]

主な用途: 日本データ(CMJ)を、内部コメントを「渡す」「渡さない」の両方で分類し(classify --internal-comments)、
主な故障の一致率と、判定不能・分類不可の割合の差を見て、既定の設定を決める。
同じ入力 CSV を分類した2つの実行フォルダを、rec_key(+ 修理ID)で突き合わせる。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _unknown(r: dict) -> bool:
    return r.get("classify_status") != "failed" and (r.get("info_level") == "none" or r.get("primary_code", "").endswith("_UNK"))


def compare(rows_a: list[dict], rows_b: list[dict], sales_company: str | None = None) -> dict:
    def filt(rows):
        return {(r["rec_key"], r["repair_id"]): r for r in rows if sales_company is None or r["sales_company"] == sales_company}
    a, b = filt(rows_a), filt(rows_b)
    keys = sorted(set(a) & set(b))
    both_ok = [k for k in keys if a[k].get("classify_status") != "failed" and b[k].get("classify_status") != "failed"]
    match = [k for k in both_ok if a[k].get("primary_code") == b[k].get("primary_code")]

    def rate(rows, f):
        rows = list(rows)
        return round(100.0 * sum(1 for r in rows if f(r)) / len(rows), 1) if rows else 0.0
    ra, rb = [a[k] for k in keys], [b[k] for k in keys]
    return {
        "sales_company": sales_company or "(すべて)", "matched": len(keys), "only_a": len(set(a) - set(b)), "only_b": len(set(b) - set(a)),
        "both_classified": len(both_ok), "primary_agree": len(match),
        "primary_agree_pct": round(100.0 * len(match) / len(both_ok), 1) if both_ok else None,
        "unknown_pct_a": rate(ra, _unknown), "unknown_pct_b": rate(rb, _unknown),
        "failed_pct_a": rate(ra, lambda r: r.get("classify_status") == "failed"),
        "failed_pct_b": rate(rb, lambda r: r.get("classify_status") == "failed"),
        "differences": [{"rec_key": k[0], "repair_id": k[1], "sales_company": a[k]["sales_company"],
                         "a_code": a[k].get("primary_code"), "a_name": a[k].get("primary_name"),
                         "b_code": b[k].get("primary_code"), "b_name": b[k].get("primary_name")}
                        for k in both_ok if a[k].get("primary_code") != b[k].get("primary_code")],
    }


def main(argv=None) -> int:
    from ..outputs.files import write_csv

    ap = argparse.ArgumentParser(description="2つの実行結果(classify の実行フォルダ)を比べる")
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--sales-company", help="この販社だけを比べる(例: CMJ)")
    ap.add_argument("--output", help="主な故障が違った修理の一覧を CSV で書く")
    x = ap.parse_args(argv)
    try:
        ra, rb = _read(Path(x.a) / "classifications.csv"), _read(Path(x.b) / "classifications.csv")
    except OSError as e:
        print(f"NG: {e}")
        return 1
    res = compare(ra, rb, x.sales_company)
    pct = "—" if res["primary_agree_pct"] is None else f"{res['primary_agree_pct']}%"
    print(f"比較({res['sales_company']}): 突き合わせ {res['matched']} 件(A だけ {res['only_a']}、B だけ {res['only_b']})")
    print(f"  主な故障の一致率: {pct}({res['primary_agree']} / {res['both_classified']} 件)")
    print(f"  判定不能: A {res['unknown_pct_a']}% / B {res['unknown_pct_b']}%、分類不可: A {res['failed_pct_a']}% / B {res['failed_pct_b']}%")
    if x.output:
        write_csv(Path(x.output), ["rec_key", "repair_id", "sales_company", "a_code", "a_name", "b_code", "b_name"], res["differences"])
        print(f"  違った修理の一覧: {x.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
