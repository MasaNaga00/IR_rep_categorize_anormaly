"""分類済みデータの CSV と JSON の読み書き(要件定義書 O-01、O-06)。

classifications.csv(修理単位)、failures.csv(修理 × 故障の種類)、environments.csv(環境要因)の3つで、
分類済みの修理を表す。段階4(分類)がこの形で書き出し、解析(analyze)がこの形で読む。
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from ..constants import COMMENT_FIELDS

INPUT_FIELDS = ("repair_id", "model", "product_type", "part_no", "serial", "received_date", "sales_company") + COMMENT_FIELDS
CLASSIFICATION_FIELDS = ("rec_key",) + INPUT_FIELDS + (
    "lang", "primary_code", "primary_name", "info_level", "classify_status", "failed_reason",
    "mfg_ym", "lot_code", "lot_order", "mfg_status", "mfg_warning", "usage_months", "usage_status")
FAILURE_FIELDS = ("rec_key", "seq", "code", "name", "source", "excerpt", "excerpt_ja")
ENVIRONMENT_FIELDS = ("rec_key", "factor", "source", "abnormal_use", "excerpt")
INT_FIELDS = ("lot_order", "usage_months")

_UNSAFE = re.compile(r'[\\/:*?"<>|\s]')


def safe_name(s: str) -> str:
    """O-06: ファイル名に使えない文字を _ に置き換える。"""
    return _UNSAFE.sub("_", s.strip()) or "_"


def combo_dir_name(model: str, part_no: str) -> str:
    return f"{safe_name(model)}__{safe_name(part_no)}"


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return v


def write_csv(path: Path, fields, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(r.get(k)) for k in fields})
    return path


def write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_classified(out_dir: str | Path, records: list[dict], name_of=None) -> None:
    """分類済みの修理を3つの CSV に書く。name_of(product_type, code) でコード名を付ける(省略可)。"""
    out = Path(out_dir)
    name_of = name_of or (lambda p, c: "")
    write_csv(out / "classifications.csv", CLASSIFICATION_FIELDS,
              ({**r, "primary_name": r.get("primary_name") or name_of(r["product_type"], r.get("primary_code"))} for r in records))
    write_csv(out / "failures.csv", FAILURE_FIELDS,
              ({"rec_key": r["rec_key"], "seq": i, **f, "name": f.get("name") or name_of(r["product_type"], f.get("code"))}
               for r in records for i, f in enumerate(r.get("failures") or [], start=1)))
    write_csv(out / "environments.csv", ENVIRONMENT_FIELDS,
              ({"rec_key": r["rec_key"], **e} for r in records for e in r.get("environment") or []))


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_classified(in_dir: str | Path) -> list[dict]:
    """write_classified で書いた3つの CSV を読み、分類済みの修理(dict)のリストに戻す。"""
    d = Path(in_dir)
    missing = [n for n in ("classifications.csv", "failures.csv", "environments.csv") if not (d / n).exists()]
    if missing:
        raise FileNotFoundError(f"{d} に {', '.join(missing)} がありません")
    recs = _read(d / "classifications.csv")
    by_key = {}
    for r in recs:
        for k in INT_FIELDS:
            r[k] = int(float(r[k])) if r.get(k) not in (None, "") else None
        r["failures"], r["environment"] = [], []
        by_key[r["rec_key"]] = r
    fails = sorted(_read(d / "failures.csv"), key=lambda x: (x["rec_key"], int(x.get("seq") or 0)))
    for f in fails:
        if f["rec_key"] in by_key:
            by_key[f["rec_key"]]["failures"].append({k: f.get(k, "") for k in ("code", "name", "source", "excerpt", "excerpt_ja")})
    for e in _read(d / "environments.csv"):
        if e["rec_key"] in by_key:
            by_key[e["rec_key"]]["environment"].append(
                {"factor": e.get("factor", ""), "source": e.get("source", ""),
                 "abnormal_use": (e.get("abnormal_use") or "").upper() == "TRUE", "excerpt": e.get("excerpt", "")})
    return recs
