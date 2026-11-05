"""コード体系(YAML)と Excel の相互変換。

v2 の codes_excel.py をコピーしたもの(v2 の要件 R-58〜R-63)。変更点: コマンド名のみ。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from .codebook import _validate
from .local_config import resolve_paths

CODE_HEADERS = ["コード", "名称", "説明文", "記録区分", "責任区分", "役割", "判定ルール", "確認状態"]
CODE_KEYS = ["code", "name", "description", "record_type", "responsibility", "role", "decision_rule", "description_status"]
PRODUCT_HEADERS = ["製品ID", "表示名", "接頭文字"]
ENV_HEADERS = ["要因", "名称", "説明文", "常にユーザ起因", "キーワード"]
FIXED_SHEETS = ("説明", "製品設定", "環境要因")
DROPDOWNS = {
    "記録区分": "failure,service,unknown",
    "責任区分": "manufacturer,user_or_unknown",
    "役割": "connected_device,other,unknown",
    "確認状態": "ai_inferred,verified",
}
KW_SEP = "、"
SECTION_TITLES = {
    "meta": "メタ情報",
    "product_categories": "製品種別ごとの故障分類",
    "environment_factors": "環境要因(製品共通の既定値)",
    "claim_reproduction_statuses": "ユーザ申告再現(v2 用。この解析では使わない)",
}
HELP_LINES = [
    "コード体系(classification_codes.yaml)の確認・修正用ファイル",
    "",
    "■ シート",
    "製品設定: 1行=1製品(製品ID / 表示名 / 接頭文字)。製品を追加するときは行を足し、製品IDと同じ名前のシートを作る(既存の製品シートをコピーしてよい)。",
    "環境要因: 1行=1要因。常にユーザ起因は TRUE / FALSE。キーワードは「、」区切り。",
    "ML / LENS など: 1行=1コード。行の追加・削除・修正ができる。",
    "",
    "■ 列(製品シート)",
    "コード: 製品の接頭文字で始める(例: M001)。同じシートで重複させない。",
    "記録区分: failure(故障)/ service(検査・清掃などのサービス)/ unknown(判定不能)",
    "責任区分: manufacturer(メーカー責任)/ user_or_unknown(ユーザ起因・不明)",
    "役割: connected_device(接続機器要因)/ other(その他)/ unknown(判定不能)。各製品に1つずつ必要。それ以外のコードは空欄。",
    "確認状態: ai_inferred(AI の下書き)/ verified(確認済み)",
    "",
    "■ YAML に戻す",
    "python anomaly_cli.py codes-import --xlsx <このファイル> [--local-dir <ローカル作業フォルダ>]",
    "・自動チェックで問題があれば、YAML は書き換えずに問題の一覧を表示する。",
    "・書き換える前に、元の YAML を classification_codes_YYYYMMDD_HHMMSS.bak.yaml として残す。",
    "・「ユーザ申告再現」の値と、製品ごとの環境要因の上書きは、このファイルに出していない(YAML のまま引き継ぐ)。",
    "・YAML はプログラムで書き直すため、手で書き足したコメントは残らない。",
]


class CodesImportError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__(f"{len(self.errors)} 件の問題があります\n" + "\n".join(f"- {e}" for e in self.errors))


@dataclass
class ImportResult:
    yaml_path: Path
    backup_path: Path
    added: dict = field(default_factory=dict)
    changed: dict = field(default_factory=dict)
    removed: dict = field(default_factory=dict)
    env_changed: bool = False


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8-sig"))


def _s(v) -> str:
    return "" if v is None else str(v).rstrip("\n")


# ---------------------------------------------------------------- export

def export_codes(yaml_path, xlsx_path, force: bool = False) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.worksheet.datavalidation import DataValidation

    xlsx_path = Path(xlsx_path)
    if xlsx_path.exists() and not force:
        raise FileExistsError(f"{xlsx_path} はすでにあります(上書きするには --force)")
    data = _load_yaml(yaml_path)
    wb = Workbook()
    help_ws = wb.active
    help_ws.title = "説明"
    for line in HELP_LINES:
        help_ws.append([line])
    help_ws.column_dimensions["A"].width = 120

    def header(ws, headers, widths):
        ws.append(headers)
        for i, w in enumerate(widths, start=1):
            ws.cell(1, i).font = Font(bold=True)
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w
        ws.freeze_panes = "A2"

    ps = wb.create_sheet("製品設定")
    header(ps, PRODUCT_HEADERS, [12, 30, 10])
    for pid, p in data["product_categories"].items():
        ps.append([pid, _s(p.get("name")), _s(p.get("code_prefix"))])

    es = wb.create_sheet("環境要因")
    header(es, ENV_HEADERS, [14, 20, 70, 14, 50])
    for k, v in (data.get("environment_factors") or {}).items():
        es.append([k, _s(v.get("name")), _s(v.get("description")), bool(v.get("always_user_caused", False)),
                   KW_SEP.join(str(x) for x in (v.get("keywords") or []))])
        es.cell(es.max_row, 3).alignment = Alignment(wrap_text=True, vertical="top")

    for pid, p in data["product_categories"].items():
        ws = wb.create_sheet(pid)
        header(ws, CODE_HEADERS, [10, 28, 80, 12, 18, 18, 40, 12])
        for code, v in p["failure_categories"].items():
            ws.append([code] + [_s(v.get(k)) or None for k in CODE_KEYS[1:]])
            ws.cell(ws.max_row, 3).alignment = Alignment(wrap_text=True, vertical="top")
        for name, choices in DROPDOWNS.items():
            col = ws.cell(1, CODE_HEADERS.index(name) + 1).column_letter
            dv = DataValidation(type="list", formula1=f'"{choices}"', allow_blank=True)
            ws.add_data_validation(dv)
            dv.add(f"{col}2:{col}2000")
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)
    return xlsx_path


# ---------------------------------------------------------------- import

def _rows(ws, headers, errors, sheet):
    got = [ws.cell(1, i + 1).value for i in range(len(headers))]
    if got != headers:
        errors.append(f"{sheet}: 1行目の見出しが違います(期待: {', '.join(headers)})")
        return []
    out = []
    for r in range(2, ws.max_row + 1):
        vals = [ws.cell(r, i + 1).value for i in range(len(headers))]
        if all(v is None or str(v).strip() == "" for v in vals):
            continue
        out.append((r, vals))
    return out


def _bool(v):
    if isinstance(v, bool):
        return v
    if v is None or str(v).strip() == "":
        return False
    t = str(v).strip().lower()
    if t in ("true", "1", "はい", "yes"):
        return True
    if t in ("false", "0", "いいえ", "no"):
        return False
    raise ValueError(v)


def _norm(v) -> str:
    return " ".join(_s(v).split())


def _build(xlsx_path, old: dict) -> tuple[dict, list[str]]:
    from openpyxl import load_workbook

    wb = load_workbook(xlsx_path, data_only=True)
    errors: list[str] = []
    for name in ("製品設定", "環境要因"):
        if name not in wb.sheetnames:
            errors.append(f"シート「{name}」がありません")
    if errors:
        return {}, errors

    old_products = old.get("product_categories") or {}
    products: dict = {}
    for r, (pid, pname, prefix) in _rows(wb["製品設定"], PRODUCT_HEADERS, errors, "製品設定"):
        pid = _s(pid).strip()
        if not pid:
            errors.append(f"製品設定 {r}行目: 製品ID が空です")
            continue
        if pid in products:
            errors.append(f"製品設定 {r}行目: 製品ID {pid} が重複しています")
            continue
        if pid not in wb.sheetnames:
            errors.append(f"製品設定 {r}行目: 製品 {pid} のシートがありません")
            continue
        base = {k: v for k, v in (old_products.get(pid) or {}).items() if k not in ("name", "code_prefix", "failure_categories")}
        prod = {"name": _s(pname), "code_prefix": _s(prefix).strip()}
        prod.update(base)
        old_codes = (old_products.get(pid) or {}).get("failure_categories") or {}
        codes: dict = {}
        for cr, vals in _rows(wb[pid], CODE_HEADERS, errors, pid):
            code = _s(vals[0]).strip()
            if not code:
                errors.append(f"{pid} {cr}行目: コードが空です")
                continue
            if code in codes:
                errors.append(f"{pid} {cr}行目: コード {code} が重複しています")
                continue
            entry = {k: v for k, v in (old_codes.get(code) or {}).items() if k not in CODE_KEYS}
            item = {}
            for k, v in zip(CODE_KEYS[1:], vals[1:]):
                t = _s(v).strip() if k != "description" else _s(v)
                if t:
                    item[k] = t
            item.update(entry)
            codes[code] = item
        prod["failure_categories"] = codes
        products[pid] = prod
    for name in wb.sheetnames:
        if name not in FIXED_SHEETS and name not in products and not any(e.startswith(f"製品設定") and name in e for e in errors):
            errors.append(f"シート「{name}」が製品設定にありません")

    old_env = old.get("environment_factors") or {}
    env: dict = {}
    for r, (k, name, desc, always, kws) in _rows(wb["環境要因"], ENV_HEADERS, errors, "環境要因"):
        k = _s(k).strip()
        if not k:
            errors.append(f"環境要因 {r}行目: 要因が空です")
            continue
        try:
            a = _bool(always)
        except ValueError:
            errors.append(f"環境要因 {r}行目({k}): 常にユーザ起因 は TRUE / FALSE にしてください({always!r})")
            continue
        item = {"name": _s(name), "description": _s(desc), "always_user_caused": a}
        kw = [x.strip() for x in _s(kws).replace(",", KW_SEP).split(KW_SEP) if x.strip()]
        if kw:
            item["keywords"] = kw
        item.update({kk: vv for kk, vv in (old_env.get(k) or {}).items() if kk not in ("name", "description", "always_user_caused", "keywords")})
        env[k] = item

    new = {}
    for key, val in old.items():
        if key == "product_categories":
            new[key] = products
        elif key == "environment_factors":
            new[key] = env
        else:
            new[key] = val
    new.setdefault("product_categories", products)
    new.setdefault("environment_factors", env)
    if not errors:
        errors.extend(_validate(new))
    return new, errors


def _diff(old: dict, new: dict):
    added, changed, removed = {}, {}, {}
    oldp = old.get("product_categories") or {}
    for pid, p in new["product_categories"].items():
        oc = (oldp.get(pid) or {}).get("failure_categories") or {}
        nc = p["failure_categories"]
        a = [c for c in nc if c not in oc]
        d = [c for c in oc if c not in nc]
        ch = [c for c in nc if c in oc and any(_norm(nc[c].get(k)) != _norm(oc[c].get(k)) for k in CODE_KEYS[1:])]
        if a: added[pid] = a
        if ch: changed[pid] = ch
        if d: removed[pid] = d
    for pid in oldp:
        if pid not in new["product_categories"]:
            removed[pid] = list((oldp[pid].get("failure_categories") or {}))
    oe, ne = old.get("environment_factors") or {}, new.get("environment_factors") or {}
    env_changed = set(oe) != set(ne) or any(
        _norm(ne[k].get(f)) != _norm(oe[k].get(f)) for k in ne for f in ("name", "description", "keywords"))\
        or any(bool(ne[k].get("always_user_caused", False)) != bool(oe[k].get("always_user_caused", False)) for k in ne)
    return added, changed, removed, env_changed


class _Dumper(yaml.SafeDumper):
    pass


def _str_rep(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data.rstrip("\n") + "\n", style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_rep)


def _dump(data: dict) -> str:
    out = ["# =============================================================================",
           "# 故障分類コード体系(修理データ分類 v2)",
           "# =============================================================================",
           "# このファイルはプログラム(codes-import)で書き出したもの。Excel で修正する場合は codes-export を使う。",
           "# 変更後は python anomaly_cli.py check で自動チェックを通すこと(要件定義書 R-16)",
           ""]
    for key, val in data.items():
        title = SECTION_TITLES.get(key, key)
        out += ["# =============================================================================", f"# {title}",
                "# =============================================================================",
                yaml.dump({key: val}, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=10000).rstrip("\n"), ""]
    return "\n".join(out)


def import_codes(xlsx_path, yaml_path, now: str | None = None) -> ImportResult:
    yaml_path = Path(yaml_path)
    old = _load_yaml(yaml_path)
    new, errors = _build(xlsx_path, old)
    if errors:
        raise CodesImportError(errors)
    added, changed, removed, env_changed = _diff(old, new)
    stamp = now or datetime.now().strftime("%Y%m%d_%H%M%S")
    date = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}"
    n = lambda d: sum(len(v) for v in d.values())
    meta = dict(new.get("meta") or {})
    entry = {"version": meta.get("version", ""), "date": date,
             "changes": [f"Excel から反映(追加 {n(added)} / 変更 {n(changed)} / 削除 {n(removed)} / 環境要因の変更 {'あり' if env_changed else 'なし'})"]}
    meta["changelog"] = [entry] + list(meta.get("changelog") or [])
    new["meta"] = meta
    text = _dump(new)
    if _validate(yaml.safe_load(text)):
        raise CodesImportError(["書き出した YAML の再チェックで問題が見つかりました(プログラムの不具合の可能性)"])
    backup = yaml_path.with_name(f"{yaml_path.stem}_{stamp}.bak.yaml")
    shutil.copyfile(yaml_path, backup)
    yaml_path.write_text(text, encoding="utf-8")
    return ImportResult(yaml_path, backup, added, changed, removed, env_changed)


# ---------------------------------------------------------------- CLI

def main_export(argv=None) -> int:
    ap = argparse.ArgumentParser(description="コード体系(YAML)を Excel に書き出す")
    ap.add_argument("--local-dir")
    ap.add_argument("--codebook")
    ap.add_argument("--xlsx")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    y = resolve_paths(local_dir=a.local_dir, codebook=a.codebook).codebook
    x = Path(a.xlsx) if a.xlsx else y.with_name("classification_codes.xlsx")
    try:
        export_codes(y, x, force=a.force)
    except FileExistsError as e:
        print(f"NG: {e}")
        return 1
    print(f"書き出し: {y}")
    print(f"  → {x}")
    return 0


def main_import(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Excel で修正したコード体系を YAML に戻す")
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--local-dir")
    ap.add_argument("--codebook")
    a = ap.parse_args(argv)
    y = resolve_paths(local_dir=a.local_dir, codebook=a.codebook).codebook
    try:
        r = import_codes(a.xlsx, y)
    except CodesImportError as e:
        print(f"NG: {len(e.errors)} 件の問題があるため、YAML は書き換えていません")
        for err in e.errors[:15]:
            print(f"  - {err}")
        if len(e.errors) > 15:
            print(f"  ...ほか {len(e.errors) - 15} 件")
        return 1
    print(f"取り込み完了: {r.yaml_path}")
    print(f"バックアップ: {r.backup_path.name}")
    pids = sorted(set(r.added) | set(r.changed) | set(r.removed))
    if not pids and not r.env_changed:
        print("変更なし")
    for pid in pids:
        line = f"  {pid}: 追加 {len(r.added.get(pid, []))} / 変更 {len(r.changed.get(pid, []))} / 削除 {len(r.removed.get(pid, []))}"
        print(line)
        for label, d in (("追加", r.added), ("変更", r.changed), ("削除", r.removed)):
            if d.get(pid):
                codes = d[pid]
                print(f"    {label}: " + ", ".join(codes[:10]) + (f" ...ほか {len(codes) - 10}" if len(codes) > 10 else ""))
    if r.env_changed:
        print("  環境要因: 変更あり")
    return 0


if __name__ == "__main__":
    sys.exit(main_export() if len(sys.argv) < 2 or sys.argv[1] != "import" else main_import(sys.argv[2:]))
