"""段階の記録と再開(要件定義書 U-04、U-05)。実行フォルダの state.json に書く。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

STATE_FILE = "state.json"
STAGE_NAMES = {
    0: "開始前",
    1: "入力の確認",
    2: "分類の準備",
    3: "分類",
    4: "シリアル変換結果の結合",
    5: "解析の設定",
    6: "解析",
    7: "説明文",
    8: "完了",
}


@dataclass
class RunState:
    run_dir: str
    input: str = ""
    internal_mode: str = "default"
    completed: int = 0                  # 終わった段階(0〜8)
    serial_result: str | None = None    # 変換結果ファイルのパス。"" は指定なし(飛ばした)、None はまだ聞いていない
    overrides: dict = field(default_factory=dict)   # 段階5で変えた設定
    created_at: str = ""
    updated_at: str = ""

    @property
    def done(self) -> bool:
        return self.completed >= 8

    @property
    def path(self) -> Path:
        return Path(self.run_dir) / STATE_FILE

    def save(self) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self.created_at = self.created_at or now
        self.updated_at = now
        Path(self.run_dir).mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    def complete(self, stage: int) -> None:
        self.completed = max(self.completed, stage)
        self.save()

    def describe(self) -> str:
        return f"{Path(self.run_dir).name}(最後に終わった段階: {self.completed}. {STAGE_NAMES[self.completed]}、更新 {self.updated_at})"


def load_state(run_dir: str | Path) -> RunState | None:
    p = Path(run_dir) / STATE_FILE
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data["run_dir"] = str(run_dir)
        return RunState(**{k: v for k, v in data.items() if k in RunState.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError):
        return None


def find_unfinished(outputs_dir: str | Path) -> RunState | None:
    """U-04: 途中で終わった実行のうち、いちばん新しいもの(フォルダ名の日時順)。"""
    d = Path(outputs_dir)
    if not d.exists():
        return None
    for sub in sorted((p for p in d.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        st = load_state(sub)
        if st is not None and not st.done:
            return st
    return None
