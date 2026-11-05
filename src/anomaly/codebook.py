"""分類コード体系(YAML)の読み込み・自動チェック・API。

v2(IR_rep_categorize_v2)の codebook.py をコピーしたもの。v2 の要件 R-15〜R-22 に従う。
変更点: v2 専用の claim_reproduction_statuses を任意にした(要件定義書 E-01)。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from .constants import RECORD_TYPES, REQUIRED_ROLES, RESPONSIBILITY_VALUES, SPECIAL_ENV_KEYS


class CodebookError(Exception):
    """YAML の自動チェック違反(R-16)。errors に違反をすべて持つ。"""

    def __init__(self, path, errors: list[str]):
        self.path = str(path)
        self.errors = list(errors)
        super().__init__(f"{self.path}: {len(self.errors)} 件の問題があります\n" + "\n".join(f"- {e}" for e in self.errors))


@dataclass(frozen=True)
class CodeInfo:
    code: str
    name: str
    description: str
    record_type: str
    responsibility: str
    role: str | None = None


@dataclass(frozen=True)
class EnvFactorInfo:
    key: str
    name: str
    description: str
    always_user_caused: bool = False


@dataclass(frozen=True)
class ClaimStatusInfo:
    key: str
    name: str
    description: str
    counts_as_confirmed: bool


def _text(v) -> str:
    return " ".join(str(v).split()) if v is not None else ""


class Codebook:
    def __init__(self, path: Path, data: dict, sha256: str):
        self.path = Path(path)
        self.sha256 = sha256
        self.version = str((data.get("meta") or {}).get("version", ""))
        self._products: dict[str, dict] = {}
        self._codes: dict[str, dict[str, CodeInfo]] = {}
        for pid, p in data["product_categories"].items():
            self._products[pid] = p
            self._codes[pid] = {
                c: CodeInfo(c, str(v["name"]), _text(v.get("description")), v["record_type"],
                            v["responsibility"], v.get("role"))
                for c, v in p["failure_categories"].items()
            }
        self._env_default = {
            k: v for k, v in (data.get("environment_factors") or {}).items() if k not in SPECIAL_ENV_KEYS
        }
        self._statuses = {
            k: ClaimStatusInfo(k, str(v["name"]), _text(v.get("description")), bool(v["counts_as_confirmed"]))
            for k, v in (data.get("claim_reproduction_statuses") or {}).items()
        }

    # --- 製品・コード ---
    def product_ids(self) -> list[str]:
        return list(self._products)

    def product_name(self, product_id: str) -> str:
        return str(self._products[product_id]["name"])

    def codes(self, product_id: str) -> dict[str, CodeInfo]:
        return dict(self._codes[product_id])

    def code(self, product_id: str, code: str) -> CodeInfo | None:
        return self._codes.get(product_id, {}).get(code)

    def is_valid_code(self, product_id: str, code: str) -> bool:
        return self.code(product_id, code) is not None

    def is_manufacturer(self, product_id: str, code: str) -> bool:
        info = self.code(product_id, code)
        return info is not None and info.responsibility == "manufacturer"

    def is_service(self, product_id: str, code: str) -> bool:
        info = self.code(product_id, code)
        return info is not None and info.record_type == "service"

    def code_by_role(self, product_id: str, role: str) -> str:
        for c, info in self._codes[product_id].items():
            if info.role == role:
                return c
        raise KeyError(f"{product_id}: role {role!r} のコードがありません")

    def code_name(self, product_id: str, code: str) -> str:
        info = self.code(product_id, code)
        return info.name if info else code

    # --- 環境要因 ---
    def environment_factors(self, product_id: str) -> dict[str, EnvFactorInfo]:
        merged: dict[str, dict] = {k: dict(v) for k, v in self._env_default.items()}
        for k, v in (self._products[product_id].get("environment_factors_override") or {}).items():
            merged.setdefault(k, {}).update(v or {})
        return {
            k: EnvFactorInfo(k, str(v.get("name", k)), _text(v.get("description")), bool(v.get("always_user_caused", False)))
            for k, v in merged.items()
        }

    # --- ユーザ申告再現 ---
    def claim_statuses(self) -> dict[str, ClaimStatusInfo]:
        return dict(self._statuses)

    def confirmed_statuses(self) -> set[str]:
        return {k for k, v in self._statuses.items() if v.counts_as_confirmed}

    # --- LLM に渡すコード一覧(R-22) ---
    def code_list_text(self, product_id: str) -> str:
        return "\n".join(f"{i.code} | {i.name} | {i.description}" for i in self._codes[product_id].values())


def _validate(data) -> list[str]:
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["YAML の最上位が辞書ではありません"]
    products = data.get("product_categories")
    if not isinstance(products, dict) or not products:
        errs.append("product_categories がありません")
        products = {}
    prefixes: dict[str, str] = {}
    for pid, p in products.items():
        if not isinstance(p, dict):
            errs.append(f"{pid}: 製品セクションが辞書ではありません")
            continue
        for key in ("name", "code_prefix", "failure_categories"):
            if key not in p:
                errs.append(f"{pid}: {key} がありません")
        prefix = p.get("code_prefix")
        if prefix:
            if prefix in prefixes:
                errs.append(f"{pid}: code_prefix '{prefix}' が {prefixes[prefix]} と重複しています")
            else:
                prefixes[prefix] = pid
        fcs = p.get("failure_categories") or {}
        role_count = {r: 0 for r in REQUIRED_ROLES}
        has_service = False
        for code, v in fcs.items():
            where = f"{pid}/{code}"
            if not isinstance(v, dict):
                errs.append(f"{where}: 定義が辞書ではありません")
                continue
            for key in ("name", "description", "record_type", "responsibility"):
                if key not in v:
                    errs.append(f"{where}: {key} がありません")
            if "responsibility" in v and v["responsibility"] not in RESPONSIBILITY_VALUES:
                errs.append(f"{where}: responsibility が不正です({v['responsibility']!r})")
            if "record_type" in v and v["record_type"] not in RECORD_TYPES:
                errs.append(f"{where}: record_type が不正です({v['record_type']!r})")
            if v.get("record_type") == "service":
                has_service = True
            role = v.get("role")
            if role is not None:
                if role in role_count:
                    role_count[role] += 1
                else:
                    errs.append(f"{where}: role が不正です({role!r})")
            if prefix and not str(code).startswith(prefix):
                errs.append(f"{where}: code_prefix '{prefix}' で始まっていません")
        for r, n in role_count.items():
            if n == 0:
                errs.append(f"{pid}: role '{r}' のコードがありません")
            elif n > 1:
                errs.append(f"{pid}: role '{r}' のコードが {n} 個あります(1個にしてください)")
        if not has_service:
            errs.append(f"{pid}: record_type 'service' のコードがありません")
        for k, v in (p.get("environment_factors_override") or {}).items():
            if isinstance(v, dict) and "always_user_caused" in v and not isinstance(v["always_user_caused"], bool):
                errs.append(f"{pid}/environment_factors_override/{k}: always_user_caused は真偽値にしてください")
    envs = data.get("environment_factors") or {}
    if not isinstance(envs, dict):
        errs.append("environment_factors が辞書ではありません")
        envs = {}
    for k, v in envs.items():
        if isinstance(v, dict) and "always_user_caused" in v and not isinstance(v["always_user_caused"], bool):
            errs.append(f"environment_factors/{k}: always_user_caused は真偽値にしてください")
    sts = data.get("claim_reproduction_statuses")  # v2 専用。この解析では使わないため任意
    if sts is not None and not isinstance(sts, dict):
        errs.append("claim_reproduction_statuses が辞書ではありません")
    elif sts:
        for k, v in sts.items():
            if not isinstance(v, dict) or "name" not in v or not isinstance(v.get("counts_as_confirmed"), bool):
                errs.append(f"claim_reproduction_statuses/{k}: name と counts_as_confirmed(真偽値)が必要です")
    return errs


def load_codebook(path: str | Path) -> Codebook:
    path = Path(path)
    raw = path.read_bytes()
    data = yaml.safe_load(raw.decode("utf-8-sig"))
    errs = _validate(data)
    if errs:
        raise CodebookError(path, errs)
    return Codebook(path, data, hashlib.sha256(raw).hexdigest())
