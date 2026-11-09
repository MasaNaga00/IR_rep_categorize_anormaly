"""シリアル変換結果の結合と使用期間の計算(要件定義書 第6節 S-08、S-10〜S-19)。

シリアル → 製造年月・ロットの変換は、このプログラムでは行わない。
会社PCの別ツール(docs/20261105_serial-converter-requirements.md)の出力 CSV を読み込み、
「製品名 + シリアル + 受付日」をキーに、修理のデータへ結合する。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .constants import MFG_NOT_CONVERTED, MFG_RESULT_COLUMNS, MFG_STATUS_VALUES, MFG_WARNING_VALUES, SEP
from .input_loader import normalize_serial, parse_date

YM_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
MAX_SHOWN = 10                       # エラー・一覧を画面に出す最大件数(S-12, S-13, S-16)
RESULT_FIELDS = ("lot_code", "lot_order", "mfg_ym", "mfg_status", "mfg_warning")   # 結合で付け足す値(S-12)
MFG_STATUS_ALL = MFG_STATUS_VALUES + (MFG_NOT_CONVERTED,)

# 使用期間の状態(S-08)
USAGE_OK = "ok"
USAGE_STATUS_VALUES = ("ok", "no_mfg_ym", "negative", "over_max")
USAGE_STATUS_NAMES = {
    "ok": "使用期間を計算した",
    "no_mfg_ym": "製造年月なし",
    "negative": "使用期間がマイナス(製造年月が受付日より後)",
    "over_max": "使用期間が上限を超える",
}


class SerialResultError(Exception):
    """変換結果ファイルの問題(S-12, S-13)。結合しない。"""

    def __init__(self, path, errors: list[str], total: int | None = None):
        self.path = str(path)
        self.errors = list(errors)
        self.total = total if total is not None else len(errors)
        more = f"(ほか {self.total - len(self.errors)} 件)" if self.total > len(self.errors) else ""
        super().__init__(f"{self.path}: シリアル変換結果に {self.total} 件の問題があります{more}\n"
                         + "\n".join(f"- {e}" for e in self.errors))


@dataclass(frozen=True)
class MfgResult:
    lot_code: str
    lot_order: int | None
    mfg_ym: str
    mfg_status: str
    mfg_warning: str

    def as_tuple(self):
        return (self.lot_code, self.lot_order, self.mfg_ym, self.mfg_status, self.mfg_warning)


@dataclass
class SerialResults:
    """読み込んだ変換結果。キー(製品名, シリアル, 受付日 ISO)→ 結果。"""
    path: Path
    sha256: str
    rows: int = 0
    by_key: dict[tuple[str, str, str], MfgResult] = field(default_factory=dict)
    duplicate_rows: int = 0          # 同じキー・同じ値でまとめた行
    skipped_no_serial: int = 0       # シリアルが空(こちらは変換結果を使わずに no_serial にするため不要)
    skipped_no_date: int = 0         # 基準日が無い・読めない(受付日で結合できないため使えない)
    _n_conflicts: int = 0

    def meta(self) -> dict:
        return {"path": str(self.path), "sha256": self.sha256, "rows": self.rows, "keys": len(self.by_key),
                "duplicate_rows": self.duplicate_rows, "skipped_no_serial": self.skipped_no_serial,
                "skipped_no_date": self.skipped_no_date}


def result_column_names(columns: dict) -> tuple[str, str]:
    """S-10: 変換結果ファイルの製品名・基準日の列名。未指定なら入力 CSV と同じ列名。"""
    model = columns.get("serial_result_model") or columns["model"]
    ref = columns.get("serial_result_reference_date") or columns["received_date"]
    return model, ref


def ym_to_index(ym: str) -> int:
    """'YYYY-MM' → 通し月数(年 × 12 + 月 − 1)。"""
    y, m = ym.split("-")
    return int(y) * 12 + int(m) - 1


def _parse_lot_order(v: str) -> tuple[bool, int | None]:
    s = (v or "").strip()
    if not s:
        return True, None
    try:
        f = float(s)
    except ValueError:
        return False, None
    if not f.is_integer():
        return False, None
    return True, int(f)


def load_serial_results(path: str | Path, model_col: str, date_col: str) -> SerialResults:
    """変換結果ファイルを読み、検査する(S-10, S-12, S-13)。問題があれば SerialResultError。"""
    path = Path(path)
    h = hashlib.sha256()
    with open(path, "rb") as fb:
        for chunk in iter(lambda: fb.read(1 << 20), b""):
            h.update(chunk)
    res = SerialResults(path=path, sha256=h.hexdigest())
    errors: list[str] = []
    n_errors = 0
    conflicts: list[str] = []

    def err(msg):
        nonlocal n_errors
        n_errors += 1
        if len(errors) < MAX_SHOWN:
            errors.append(msg)

    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            _read_rows(f, res, model_col, date_col, err, conflicts)
    except UnicodeDecodeError as e:
        raise SerialResultError(path, [f"UTF-8 として読めません(先頭から {e.start} バイト付近)"])
    n_conflicts = res._n_conflicts
    if n_errors or n_conflicts:
        raise SerialResultError(path, errors + conflicts, n_errors + n_conflicts)
    return res


def _read_rows(f, res: "SerialResults", model_col: str, date_col: str, err, conflicts: list[str]) -> None:
    reader = csv.DictReader(f)
    header = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = header
    missing = [c for c in (model_col, date_col) + MFG_RESULT_COLUMNS if c not in header]
    if missing:
        raise SerialResultError(res.path, [f"列がありません: {', '.join(missing)}"])
    res._n_conflicts = 0
    for row in reader:
        res.rows += 1
        where = f"{reader.line_num}行目"          # ファイルの行番号(見出しが1行目)
        model = (row.get(model_col) or "").strip()
        serial = normalize_serial(row.get("serial_normalized"))
        status = (row.get("mfg_status") or "").strip()
        ym = (row.get("mfg_ym") or "").strip()
        lot_code = (row.get("lot_code") or "").strip()
        warning = (row.get("mfg_warning") or "").strip()

        bad = False
        if status not in MFG_STATUS_VALUES:
            err(f"{where}: mfg_status '{status}' は定義にありません({' / '.join(MFG_STATUS_VALUES)})")
            bad = True
        if ym and not YM_RE.match(ym):
            err(f"{where}: mfg_ym '{ym}' は 'YYYY-MM' の形ではありません")
            bad = True
        ok_order, lot_order = _parse_lot_order(row.get("lot_order"))
        if not ok_order:
            err(f"{where}: lot_order '{row.get('lot_order')}' は整数ではありません")
            bad = True
        if status == "ok" and not ym:
            err(f"{where}: mfg_status が ok なのに mfg_ym が空です")
            bad = True
        if bad:
            continue

        if not serial:
            res.skipped_no_serial += 1
            continue
        d = parse_date(row.get(date_col))
        if d is None:
            res.skipped_no_date += 1
            continue
        key = (model, serial, d.isoformat())
        value = MfgResult(lot_code, lot_order, ym, status, warning)
        prev = res.by_key.get(key)
        if prev is None:
            res.by_key[key] = value
        elif prev.as_tuple() == value.as_tuple():
            res.duplicate_rows += 1
        else:
            res._n_conflicts += 1
            if len(conflicts) < MAX_SHOWN:
                conflicts.append(f"{where}: 同じキー(製品名={model}, シリアル={serial}, 基準日={d.isoformat()})"
                                 f"で、変換結果が違う行があります")


def usage_months(received_date: str, mfg_ym: str, max_months: int) -> tuple[int | None, str]:
    """S-08: 使用期間(月数)と状態。received_date は 'YYYY-MM-DD'。"""
    if not mfg_ym:
        return None, "no_mfg_ym"
    months = ym_to_index(received_date[:7]) - ym_to_index(mfg_ym)
    if months < 0:
        return months, "negative"
    if months > max_months:
        return months, "over_max"
    return months, USAGE_OK


@dataclass
class MergeSummary:
    total: int = 0
    status_counts: Counter = field(default_factory=Counter)
    warning_counts: Counter = field(default_factory=Counter)
    usage_counts: Counter = field(default_factory=Counter)
    not_converted_models: Counter = field(default_factory=Counter)
    result_meta: dict | None = None

    def converted_ratio(self) -> float:
        """製造年月まで求められた割合(T-07)。"""
        return self.status_counts["ok"] / self.total if self.total else 0.0

    def as_dict(self) -> dict:
        """実行メタ情報用(S-16, S-17)。"""
        return {
            "total": self.total,
            "mfg_status": {k: self.status_counts[k] for k in MFG_STATUS_ALL if self.status_counts[k]},
            "mfg_warning": {k: v for k, v in sorted(self.warning_counts.items())},
            "usage_status": {k: self.usage_counts[k] for k in USAGE_STATUS_VALUES if self.usage_counts[k]},
            "not_converted_models_top": self.not_converted_models.most_common(MAX_SHOWN),
            "serial_result_file": self.result_meta,
        }

    def summary_lines(self) -> list[str]:
        """画面に出す短い要約(S-16)。"""
        lines = []
        if self.result_meta is None:
            lines.append("シリアル変換結果: 指定なし(使用期間・ロットの解析は「製造年月の情報なし」になります)")
        else:
            m = self.result_meta
            lines.append(f"シリアル変換結果: {m['path']}({m['rows']} 行、キー {m['keys']} 件)")
        st = ", ".join(f"{k} {self.status_counts[k]}" for k in MFG_STATUS_ALL if self.status_counts[k])
        lines.append(f"  mfg_status: {st or 'なし'}(全 {self.total} 件)")
        if self.not_converted_models and self.result_meta is not None:
            top = ", ".join(f"{m} {c}" for m, c in self.not_converted_models.most_common(MAX_SHOWN))
            lines.append(f"  結合できなかった機種(上位{MAX_SHOWN}): {top}")
        if self.warning_counts:
            lines.append("  注意: " + ", ".join(f"{k} {v}" for k, v in sorted(self.warning_counts.items())))
        us = ", ".join(f"{k} {self.usage_counts[k]}" for k in USAGE_STATUS_VALUES if self.usage_counts[k])
        lines.append(f"  使用期間: {us or 'なし'}")
        return lines


def merge_serial(records: list[dict], results: SerialResults | None, usage_max_months: int) -> MergeSummary:
    """S-11〜S-16, S-08: 各修理に変換結果と使用期間を付ける(records を書き換える)。

    付ける列: lot_code, lot_order, mfg_ym, mfg_status, mfg_warning, usage_months, usage_status
    """
    summ = MergeSummary(total=len(records), result_meta=results.meta() if results is not None else None)
    for r in records:
        serial = r.get("serial") or ""
        if not serial:
            value = MfgResult("", None, "", "no_serial", "")
        else:
            key = ((r.get("model") or "").strip(), serial, r["received_date"])
            value = results.by_key.get(key) if results is not None else None
            if value is None:
                value = MfgResult("", None, "", MFG_NOT_CONVERTED, "")
                summ.not_converted_models[key[0]] += 1
        r["lot_code"], r["lot_order"], r["mfg_ym"] = value.lot_code, value.lot_order, value.mfg_ym
        r["mfg_status"], r["mfg_warning"] = value.mfg_status, value.mfg_warning
        r["usage_months"], r["usage_status"] = usage_months(r["received_date"], value.mfg_ym, usage_max_months)
        summ.status_counts[value.mfg_status] += 1
        summ.usage_counts[r["usage_status"]] += 1
        for w in filter(None, (x.strip() for x in value.mfg_warning.split(SEP))):
            summ.warning_counts[w] += 1
    return summ


def unknown_warnings(summary: MergeSummary) -> list[str]:
    """変換ツールの定義(SC-35)に無い注意の値。エラーにはしないが、表示する。"""
    return sorted(w for w in summary.warning_counts if w not in MFG_WARNING_VALUES)


# ---------------------------------------------------------------- コマンド

OUTPUT_EXTRA = ("rec_key", "lot_code", "lot_order", "mfg_ym", "mfg_status", "mfg_warning", "usage_months", "usage_status")


def main(argv=None) -> int:
    """python anomaly_cli.py merge-serial --input <入力CSV> --serial-result <変換結果CSV> [--output <CSV>]

    分類の前でも、入力 CSV と変換結果の結合だけを試せる(会社PCでの確認用)。
    """
    from .codebook import CodebookError, load_codebook
    from .input_loader import InputError, load_column_config, load_input
    from .local_config import resolve_paths
    from .settings import SettingsError, load_settings

    ap = argparse.ArgumentParser(description="シリアル変換結果を入力 CSV に結合し、件数を表示する(分類はしない)")
    ap.add_argument("--input", required=True)
    ap.add_argument("--serial-result", help="変換ツールの出力 CSV。省略すると、すべて not_converted になる")
    ap.add_argument("--output", help="結合した結果を CSV で書き出す(省略可)")
    ap.add_argument("--local-dir")
    ap.add_argument("--codebook")
    ap.add_argument("--columns")
    ap.add_argument("--settings")
    a = ap.parse_args(argv)
    paths = resolve_paths(local_dir=a.local_dir, codebook=a.codebook, columns=a.columns, settings=a.settings)
    try:
        cb = load_codebook(paths.codebook)
        settings = load_settings(paths.settings)
        cols = load_column_config(paths.columns)
        loaded = load_input(a.input, cb.product_ids(), cols)
        results = None
        if a.serial_result:
            results = load_serial_results(a.serial_result, *result_column_names(cols))
    except (CodebookError, SettingsError, SerialResultError, InputError, ValueError, OSError) as e:
        print(f"NG: {e}")
        return 1
    summ = merge_serial(loaded.records, results, settings.usage_max_months)
    print(f"入力: {a.input}({len(loaded.records)} 件、除外 {len(loaded.excluded)} 件)")
    for line in loaded.excluded_summary_lines(list_file=None):   # I-05a(merge-serial は一覧のファイルを書かない)
        print(line)
    for line in summ.summary_lines():
        print(line)
    unk = unknown_warnings(summ)
    if unk:
        print(f"  注意: 変換ツールの定義に無い mfg_warning があります: {', '.join(unk)}")
    if a.output:
        out = Path(a.output)
        if out.exists():
            print(f"NG: 出力先にファイルがあります(上書きしません): {out}")
            return 1
        fields = ["repair_id", "model", "product_type", "part_no", "serial", "received_date", "sales_company"]
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields + list(OUTPUT_EXTRA), extrasaction="ignore")
            w.writeheader()
            for r in loaded.records:
                w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields + list(OUTPUT_EXTRA)})
        print(f"出力: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
