"""分類結果のキャッシュ(要件定義書 G-16、G-23、U-05)。

- 修理のキー: 販社名 + 修理ID + 受付日 + LLM に渡したコメントの SHA-256
- キャッシュの区別: コード体系の SHA-256、プロンプトの SHA-256、内部コメントの渡し方(販社の一覧)。
  どれかが変わったら別のファイルになる(前の結果は使わない)。
- 分類不可の修理は保存しない(次回の実行で自動的に送り直す)。
- 書き込みはバッチごとに追記する(途中で止まっても、それまでの結果は残る)。
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

FIELDS = ("key", "rec_key", "repair_id", "result", "saved_at")


def sha256_of(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record_key(rec: dict, comments: dict[str, str]) -> str:
    body = json.dumps(comments, ensure_ascii=False, sort_keys=True)
    text = "\x1f".join([rec.get("sales_company", ""), rec.get("repair_id", ""), rec.get("received_date", ""), body])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_signature(codebook_sha: str, prompt_sha: str, internal_setting: str) -> str:
    return hashlib.sha256("|".join([codebook_sha, prompt_sha, internal_setting]).encode("utf-8")).hexdigest()[:16]


class ClassifyCache:
    def __init__(self, cache_dir: str | Path, signature: str):
        self.dir = Path(cache_dir)
        self.signature = signature
        self.path = self.dir / f"classify_{signature}.csv"
        self._data: dict[str, dict] = {}
        if self.path.exists():
            with open(self.path, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    try:
                        self._data[row["key"]] = json.loads(row["result"])
                    except (json.JSONDecodeError, KeyError):
                        continue      # 途中で止まって壊れた行は読み飛ばす

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def put_many(self, entries: list[tuple[str, dict, dict]]) -> None:
        """entries: (キー, 修理, 正規化した結果)。"""
        if not entries:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        with open(self.path, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            now = datetime.now().isoformat(timespec="seconds")
            for key, rec, result in entries:
                w.writerow({"key": key, "rec_key": rec.get("rec_key", ""), "repair_id": rec.get("repair_id", ""),
                            "result": json.dumps(result, ensure_ascii=False), "saved_at": now})
                self._data[key] = result


class NullCache(ClassifyCache):
    """キャッシュを使わない(テストや --no-cache 用)。"""

    def __init__(self):
        self._data = {}
        self.path = None
        self.signature = ""

    def put_many(self, entries) -> None:
        for key, _, result in entries:
            self._data[key] = result
