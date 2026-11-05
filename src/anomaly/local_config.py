"""ローカル作業フォルダ(Windows 側で変更するファイルの置き場所)(要件定義書 E-06)。

v2 の local_config.py を元にしたもの。設定ファイルは、
コマンドで個別に指定したパス → ローカル作業フォルダ → リポジトリの config/(初期値) の順で探す。
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
ENV_VAR = "ANOMALY_LOCAL_DIR"

# ローカル作業フォルダ内の相対パス → リポジトリの初期値
# (シリアル変換表は別ツールで管理するため、ここには置かない)
DEFAULTS = {
    "classification_codes.yaml": CONFIG_DIR / "classification_codes.yaml",
    "input_columns.yaml": CONFIG_DIR / "input_columns.yaml",
    "settings.yaml": CONFIG_DIR / "settings.yaml",
    "prompts/classify_prompt.md": CONFIG_DIR / "prompts" / "classify_prompt.md",
    "prompts/summary_prompt.md": CONFIG_DIR / "prompts" / "summary_prompt.md",
}
LOCAL_SUBDIRS = ("data", "outputs", "cache", "prompts")
ENV_TEMPLATE = """# Dify の接続情報(このファイルは共有しないこと)
DIFY_BASE_URL=https://your-dify-host/v1
# 分類用ワークフローの API キー
DIFY_API_KEY_CLASSIFY=app-xxxxxxxx
# 要約用ワークフローの API キー(無ければ要約文は作らない)
# DIFY_API_KEY_SUMMARY=app-yyyyyyyy
# 社内の証明書が必要な場合
# REQUESTS_CA_BUNDLE=C:\\path\\to\\corp-ca.pem
"""
NAME_MAP_HEADER = "product_type,past_name,code\n"


@dataclass
class ResolvedPaths:
    local_dir: Path | None
    codebook: Path
    columns: Path
    settings: Path
    classify_prompt: Path
    env_file: Path
    outputs_dir: Path
    cache_dir: Path
    name_map: Path | None


def local_dir_from(local_dir=None) -> Path | None:
    if local_dir:
        return Path(local_dir)
    v = os.environ.get(ENV_VAR)
    return Path(v) if v else None


def resolve_paths(local_dir=None, codebook=None, columns=None, settings=None, classify_prompt=None) -> ResolvedPaths:
    local = local_dir_from(local_dir)

    def pick(explicit, rel):
        if explicit:
            return Path(explicit)
        if local is not None and (local / rel).exists():
            return local / rel
        return DEFAULTS[rel]

    name_map = local / "eval_name_map.csv" if local is not None else None
    base = local if local is not None else REPO_ROOT
    return ResolvedPaths(
        local_dir=local,
        codebook=pick(codebook, "classification_codes.yaml"),
        columns=pick(columns, "input_columns.yaml"),
        settings=pick(settings, "settings.yaml"),
        classify_prompt=pick(classify_prompt, "prompts/classify_prompt.md"),
        env_file=base / ".env",
        outputs_dir=base / "outputs",
        cache_dir=base / "cache",
        name_map=name_map if name_map is not None and name_map.exists() else None,
    )


def init_local(local_dir) -> list[Path]:
    """ローカル作業フォルダを作り、初期値をコピーする。既存のファイルは上書きしない。"""
    local = Path(local_dir)
    created: list[Path] = []
    if not local.exists():
        local.mkdir(parents=True)
    for sub in LOCAL_SUBDIRS:
        d = local / sub
        if not d.exists():
            d.mkdir(parents=True)
            created.append(d)
    for rel, src in DEFAULTS.items():
        dst = local / rel
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            created.append(dst)
    for rel, text in ((".env", ENV_TEMPLATE), ("eval_name_map.csv", NAME_MAP_HEADER)):
        dst = local / rel
        if not dst.exists():
            dst.write_text(text, encoding="utf-8-sig" if rel.endswith(".csv") else "utf-8")
            created.append(dst)
    return created
