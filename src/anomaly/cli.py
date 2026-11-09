"""対話式の実行: 修理データを渡し、y / n で答えていくだけで解析の出力まで進む(要件定義書 U-01〜U-08)。

  python anomaly_cli.py [--local-dir <ローカル作業フォルダ>]                  対話式
  python anomaly_cli.py run --input <CSV> [--serial-result <CSV>] --yes      質問なしで最後まで(U-06)

段階: 0 再開の確認 / 1 入力の確認 / 2 分類の準備 / 3 分類 / 4 シリアル変換結果の結合 / 5 解析の設定 /
      6 解析 / 7 説明文 / 8 完了。n を選んだ段階で終了し、次回の起動で続きから再開できる。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from .analyze import analyze_records, summary_lines
from .classify.cache import ClassifyCache, cache_signature, sha256_of
from .classify.runner import ClassifyResult, classify_records
from .classify_cmd import internal_setting, run_meta, write_outputs
from .codebook import CodebookError, load_codebook
from .dify_client import KEY_CLASSIFY, KEY_SUMMARY, DifyClient
from .input_loader import EXCLUDED_FIELDS, InputError, llm_comments, load_column_config, load_input
from .lang import detect_lang
from .local_config import ENV_VAR, local_dir_from, resolve_paths
from .outputs.files import read_classified, write_classified, write_csv, write_json
from .serial_merge import SerialResultError, load_serial_results, merge_serial, result_column_names
from .settings import Settings, SettingsError, load_settings, parse_settings
from .state import STAGE_NAMES, RunState, find_unfinished, load_state

SECONDS_PER_CALL = 30          # 所要時間の目安(Dify の1回の呼び出し)
EDITABLE = [                   # 段階5で聞き直す設定(キー, 説明)
    ("recent_months", "直近の期間(か月)"),
    ("recent_compare", "直近と比べる期間(all_before / same_length)"),
    ("early_months", "使用期間の「初期」(か月以内)"),
    ("usage_bins", "使用期間の区切り(開始月をカンマ区切り)"),
    ("lot_min_n", "ロット(まとまり)の最低件数"),
    ("lot_window", "まとめるロットの数"),
    ("min_group_n", "比べる群の最低件数"),
    ("alpha", "補正後の p 値の基準"),
    ("min_diff_pt", "割合の差の最低値(ポイント)"),
    ("min_code_n", "割合が高い側のコードの最低件数"),
]


class Abort(Exception):
    """n を選んだ、または続けられないエラー。state は保存済み。"""


class Console:
    """画面の入出力と run.log(U-08)。テストでは input_fn / print_fn を差し替える。"""

    def __init__(self, auto_yes: bool = False, input_fn=input, print_fn=print):
        self.auto_yes = auto_yes
        self.input_fn = input_fn
        self.print_fn = print_fn
        self.log_path: Path | None = None
        self._buffer: list[str] = []

    def set_log(self, path: Path) -> None:
        self.log_path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for line in self._buffer:
                f.write(line + "\n")
        self._buffer = []

    def log(self, msg: str) -> None:
        line = f"{datetime.now():%H:%M:%S} {msg}"
        if self.log_path is None:
            self._buffer.append(line)
        else:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def say(self, msg: str = "") -> None:
        self.print_fn(msg)
        if msg:
            self.log(msg)

    def ask(self, question: str, default: str = "") -> str:
        if self.auto_yes:
            return default
        ans = self.input_fn(f"{question} ").strip()
        self.log(f"[質問] {question} → {ans!r}")
        return ans if ans else default

    def yn(self, question: str) -> bool:
        if self.auto_yes:
            self.log(f"[質問] {question} → y(--yes)")
            return True
        while True:
            ans = self.ask(f"{question}(y/n)").lower()
            if ans in ("y", "yes"):
                return True
            if ans in ("n", "no"):
                return False
            self.say("  y か n で答えてください。")

    def choice(self, question: str, options: dict[str, str], auto: str) -> str:
        if self.auto_yes:
            self.log(f"[質問] {question} → {auto}(--yes)")
            return auto
        text = " ".join(f"{k}: {v}" for k, v in options.items())
        while True:
            ans = self.ask(f"{question} {text}")
            if ans in options:
                return ans
            self.say(f"  {' / '.join(options)} のどれかで答えてください。")


def clean_path(s: str) -> str:
    """U-02: ドラッグ&ドロップで付く引用符・前後の空白を取り除く。"""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    return s.strip()


class Session:
    def __init__(self, console: Console, local_dir: str | None, client=None, run_date: date | None = None,
                 internal_mode: str = "default", summary_client=None):
        self.c = console
        self.local_dir = local_dir
        self.paths = resolve_paths(local_dir=local_dir)
        self.client = client
        self.summary_client = summary_client
        self.run_date = run_date or date.today()
        self.internal_mode = internal_mode
        self.state: RunState | None = None
        self.records: list[dict] = []
        self.loaded = None
        self.result: ClassifyResult | None = None
        self.codebook = None
        self.base_settings: Settings | None = None

    # ------------------------------------------------------------ 共通
    @property
    def run_dir(self) -> Path:
        return Path(self.state.run_dir)

    def settings(self) -> Settings:
        if not self.state.overrides:
            return self.base_settings
        return parse_settings({**self.base_settings.as_dict(), **self.state.overrides}, source="対話で変更")

    def stop(self, msg: str) -> None:
        self.c.say(msg)
        if self.state is not None:
            self.state.save()
            self.c.say(f"ここで終了します。次回の起動で続きから再開できます(実行フォルダ: {self.run_dir})。")
        raise Abort(msg)

    def load_config(self) -> None:
        try:
            self.codebook = load_codebook(self.paths.codebook)
            self.base_settings = load_settings(self.paths.settings)
            self.columns = load_column_config(self.paths.columns)
        except (CodebookError, SettingsError, ValueError) as e:
            self.c.say(f"NG: 設定ファイルに問題があります: {e}")
            self.c.say("対処: python anomaly_cli.py check で問題の一覧を確認し、ローカル作業フォルダの設定ファイルを直してください。")
            raise Abort(str(e))

    # ------------------------------------------------------------ 段階0
    def stage0(self, input_path: str | None) -> None:
        st = None if input_path else find_unfinished(self.paths.outputs_dir)
        if st is not None:
            self.c.say(f"[0] 途中で終わった実行があります: {st.describe()}")
            if self.c.yn("前回の続きから再開しますか?"):
                self.state = st
                self.c.set_log(self.run_dir / "run.log")
                self.c.log(f"再開: 段階{st.completed}から")
                return
        run_dir = self.paths.outputs_dir / f"{datetime.now():%Y%m%d_%H%M%S}"
        while run_dir.exists():
            run_dir = run_dir.with_name(run_dir.name + "_")
        self.state = RunState(str(run_dir), internal_mode=self.internal_mode)
        self.c.set_log(run_dir / "run.log")

    # ------------------------------------------------------------ 段階1
    def stage1(self, input_path: str | None) -> None:
        s = self.base_settings
        path = input_path or self.state.input
        while True:
            if not path:
                path = clean_path(self.c.ask("[1] 修理データの CSV のパスを入力してください(ファイルをドラッグ&ドロップしても可):"))
            if not path:
                self.stop("入力ファイルが指定されませんでした。")
            try:
                self.loaded = load_input(path, self.codebook.product_ids(), self.columns)
                break
            except (InputError, OSError, UnicodeDecodeError) as e:
                self.c.say(f"NG: 入力ファイルを読めません: {e}")
                self.c.say("対処: パスと、列名(input_columns.yaml)、文字コード(UTF-8)を確認してください。")
                if self.c.auto_yes or not self.c.yn("別のファイルを指定しますか?"):
                    self.stop("入力ファイルを読めないため終了します。")
                path = ""
        self.state.input = str(path)
        L = self.loaded
        self.c.say(f"[1] 入力: {path}")
        self.c.say(f"  件数: {L.total_rows} 行(読み込み {len(L.records)} 件、除外 {len(L.excluded)} 件)")
        write_csv(self.run_dir / "excluded_rows.csv", EXCLUDED_FIELDS, L.excluded_rows())     # I-05a
        for line in L.excluded_summary_lines(indent="    "):
            self.c.say(line)
        if not L.has_serial_column:
            self.c.say(f"  注意: シリアルの列({self.columns['serial']})がありません(使用期間・ロットの解析はできません)")
        combos = L.combos()
        top = ", ".join(f"{m}×{p} {n}件" for (m, p), n in combos.most_common(10))
        self.c.say(f"  製品×部番: {len(combos)} 個({top}{' ほか' if len(combos) > 10 else ''})")
        sales = Counter(r["sales_company"] for r in L.records)
        self.c.say("  販社別: " + ", ".join(f"{k} {v}" for k, v in sales.most_common()))
        langs = Counter(detect_lang(*llm_comments(r, s.internal_excluded(r["sales_company"], self.state.internal_mode)).values())
                        for r in L.records)
        self.c.say("  言語別: " + ", ".join(f"{k} {v}" for k, v in langs.most_common()))
        if L.duplicate_id_rows():
            self.c.say(f"  修理IDが重複している行: {L.duplicate_id_rows()} 行(別の修理として扱います)")
        if not L.records:
            self.stop("分類できる修理がありません。")
        if not self.c.yn("この内容で進めますか?"):
            self.stop("段階1で終了します。")
        self.state.complete(1)

    # ------------------------------------------------------------ 段階2・3
    def _cache(self, s: Settings) -> ClassifyCache:
        prompt_sha = sha256_of(self.paths.classify_prompt)
        sig = cache_signature(self.codebook.sha256, prompt_sha, internal_setting(s, self.state.internal_mode))
        return ClassifyCache(self.paths.cache_dir, sig)

    def stage2(self) -> None:
        s = self.base_settings
        plan = classify_records(copy.deepcopy(self.loaded.records), self.codebook, s, None, self._cache(s),
                                internal_mode=self.state.internal_mode, dry_run=True).stats
        minutes = plan.batches * SECONDS_PER_CALL / 60
        self.c.say(f"[2] 分類の準備: キャッシュ済み {plan.cached} 件、コメントが空欄 {plan.no_text} 件、Dify に送る {plan.sent} 件")
        self.c.say(f"  Dify の呼び出し: 約 {plan.batches} 回(所要時間の目安: 約 {minutes:.0f} 分。1回 {SECONDS_PER_CALL} 秒として)")
        excluded = ", ".join(s.internal_comment_excluded_sales_companies) if self.state.internal_mode == "default" else self.state.internal_mode
        self.c.say(f"  内部コメントを渡さない販社: {excluded or 'なし'}")
        if not self.c.yn("分類を始めますか?"):
            self.stop("段階2で終了します。")
        self.state.complete(2)

    def _client(self):
        if self.client is None:
            try:
                self.client = DifyClient.from_env(self.paths.env_file, key_var=KEY_CLASSIFY)
            except ValueError as e:
                self.stop(f"NG: {e}\n対処: {self.paths.env_file} に DIFY_BASE_URL と {KEY_CLASSIFY} を書いてください。")
        return self.client

    def stage3(self) -> None:
        s = self.base_settings
        client = self._client()
        cache = self._cache(s)
        started = datetime.now()

        def progress(done, total):
            if not self.c.auto_yes:
                print(f"\r  分類中: {done} / {total} 件", end="", flush=True)

        self.c.say("[3] 分類を始めます(途中で止めても、分類できた分はキャッシュに残ります)。")
        self.result = classify_records(self.loaded.records, self.codebook, s, client, cache,
                                       internal_mode=self.state.internal_mode, raw_dir=self.run_dir / "raw", progress=progress)
        if not self.c.auto_yes:
            print()
        for line in self.result.stats.summary_lines():
            self.c.say(line)
        while self.result.stats.failed and self.result.stats.failed_ratio() > s.failed_warn_ratio:   # G-22
            self.c.say(f"注意: 分類不可が {self.result.stats.failed_ratio():.1%} あります(基準 {s.failed_warn_ratio:.0%})。")
            ans = self.c.choice("どうしますか?", {"1": "再試行する", "2": "このまま解析に進む", "3": "中断する"}, auto="2")
            if ans == "3":
                self._write_classified(started, cache)
                self.state.complete(2)
                self.stop("段階3で中断します。分類できた修理はキャッシュに残っています。")
            if ans == "2":
                break
            failed = [r for r in self.loaded.records if r.get("classify_status") == "failed"]
            again = classify_records(failed, self.codebook, s, client, cache, internal_mode=self.state.internal_mode,
                                     raw_dir=self.run_dir / "raw_retry", progress=progress)
            if not self.c.auto_yes:
                print()
            st = self.result.stats
            st.failed = sum(1 for r in self.loaded.records if r.get("classify_status") == "failed")
            st.ok = st.total - st.failed
            st.calls += again.stats.calls
            st.failed_reasons = Counter(r["failed_reason"] for r in self.loaded.records if r.get("classify_status") == "failed")
            self.result.issues.extend(again.issues)
            self.c.say(f"  再試行の結果: 分類できた {st.ok} 件、分類不可 {st.failed} 件")
        self.records = self.loaded.records
        self._write_classified(started, cache)
        self.state.complete(3)

    def _write_classified(self, started, cache) -> None:
        args = SimpleNamespace(input=self.state.input, internal_comments=self.state.internal_mode, dry_run=False)
        write_outputs(self.run_dir, self.loaded.records, self.result, self.codebook, self.base_settings)
        write_json(self.run_dir / "run_meta.json", run_meta(
            started=started, args=args, paths=self.paths, codebook=self.codebook, settings=self.base_settings,
            prompt_sha=sha256_of(self.paths.classify_prompt), loaded=self.loaded, result=self.result, merge=None,
            cache=cache, seconds=(datetime.now() - started).total_seconds()))

    # ------------------------------------------------------------ 段階4
    def stage4(self, serial_path: str | None) -> None:
        if not self.records:
            self.records = read_classified(self.run_dir)
        s = self.base_settings
        path = serial_path if serial_path is not None else self.state.serial_result
        results = None
        while True:
            if path is None:
                path = clean_path(self.c.ask("[4] シリアル変換結果の CSV のパスを入力してください(無ければ Enter で飛ばす):"))
            if not path:
                break
            try:
                results = load_serial_results(path, *result_column_names(self.columns))
                break
            except (SerialResultError, OSError) as e:
                self.c.say(f"NG: シリアル変換結果を読めません: {e}")
                if self.c.auto_yes:
                    self.stop("シリアル変換結果を読めないため終了します。")
                self.c.say("対処: 変換ツールの出力 CSV(UTF-8)を指定してください。Enter で飛ばすこともできます。")
                path = None
        self.state.serial_result = path or ""
        summ = merge_serial(self.records, results, s.usage_max_months)
        self.c.say("[4] シリアル変換結果の結合")
        for line in summ.summary_lines():
            self.c.say(line)
        write_classified(self.run_dir, self.records, self.codebook.code_name)
        meta_path = self.run_dir / "run_meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["serial_merge"] = summ.as_dict()
            write_json(meta_path, meta)
        if not self.c.yn("この内容で解析に進みますか?"):
            self.state.complete(4)
            self.stop("段階4で終了します。")
        self.state.complete(4)

    # ------------------------------------------------------------ 段階5
    def stage5(self) -> None:
        s = self.settings()
        self.c.say("[5] 解析の設定")
        for key, label in EDITABLE:
            self.c.say(f"  {label}: {_fmt(getattr(s, key))}")
        if not self.c.yn("この設定で解析しますか?"):
            self.c.say("  変える項目だけ値を入力してください(Enter でそのまま)。")
            overrides = dict(self.state.overrides)
            for key, label in EDITABLE:
                while True:
                    ans = self.c.ask(f"  {label} [{_fmt(getattr(s, key))}]:")
                    if not ans:
                        break
                    try:
                        value = _parse_value(key, ans)
                        parse_settings({**self.base_settings.as_dict(), **overrides, key: value})
                    except (ValueError, SettingsError) as e:
                        self.c.say(f"    値が正しくありません: {e}")
                        continue
                    overrides[key] = value
                    break
            self.state.overrides = overrides
            s = self.settings()
            self.c.say("  変更後: " + ", ".join(f"{k}={_fmt(getattr(s, k))}" for k in overrides) if overrides else "  変更なし")
        self.state.complete(5)

    # ------------------------------------------------------------ 段階6〜8
    def stage6(self) -> None:
        if not self.records:
            self.records = read_classified(self.run_dir)
        s = self.settings()
        info = {"serial_result": self.state.serial_result or None,
                "internal_excluded": list(s.internal_comment_excluded_sales_companies) if self.state.internal_mode == "default"
                else (["(すべての販社)"] if self.state.internal_mode == "none" else [])}
        if (self.run_dir / "index.xlsx").exists():
            self.c.say("[6] 解析の結果は既にあります(作り直しません)。")
        else:
            self.c.say("[6] 解析しています。")
            results = analyze_records(self.records, self.codebook, s, self.run_date, self.run_dir, info)
            for line in summary_lines(results):
                self.c.say(line)
        self.state.complete(6)

    def stage7(self) -> None:
        from .report.summary import summarize_run

        self.c.say("[7] 説明文: 定型文のレポート(report.md)を作りました。")
        has_key = self.summary_client is not None or os.environ.get(KEY_SUMMARY) or _env_has(self.paths.env_file, KEY_SUMMARY)
        if has_key and self.c.yn("Dify で要約文も作りますか?"):
            client = self.summary_client
            if client is None:
                try:
                    client = DifyClient.from_env(self.paths.env_file, key_var=KEY_SUMMARY)
                except ValueError as e:
                    self.c.say(f"NG: {e}(要約文は作らずに進みます)")
            if client is not None:
                st = summarize_run(self.run_dir, client)
                self.c.say("  " + st.summary_line())
                for e in st.errors[:5]:
                    self.c.say(f"    {e}")
                if st.warned:
                    self.c.say("  注意: 数値などの警告がある要約は、report.md の末尾の「要約の確認」を見てください。")
        self.state.complete(7)

    def stage8(self) -> None:
        self.state.complete(8)
        self.c.say(f"[8] 完了しました。出力フォルダ: {self.run_dir}")
        for name in ("index.xlsx", "tableau_long.csv", "classifications.csv", "failures.csv", "classify_failed.csv",
                     "validation_issues.csv", "run_meta.json", "run.log"):
            if (self.run_dir / name).exists():
                self.c.say(f"  {name}")
        combos = sorted((self.run_dir / "combos").glob("*")) if (self.run_dir / "combos").exists() else []
        self.c.say(f"  combos/ 製品×部番ごとのフォルダ {len(combos)} 個(tables.xlsx / report.md / findings.json)")

    # ------------------------------------------------------------ 全体
    def run(self, input_path: str | None = None, serial_path: str | None = None) -> int:
        self.load_config()
        self.stage0(input_path)
        st = self.state
        try:
            if st.completed < 3:
                self.stage1(input_path)
                self.stage2()
                self.stage3()
            if st.completed < 4:
                self.stage4(serial_path)
            if st.completed < 5:
                self.stage5()
            if st.completed < 6:
                self.stage6()
            if st.completed < 7:
                self.stage7()
            self.stage8()
        except Abort:
            return 1
        except KeyboardInterrupt:
            self.c.say("")
            self.stop_quietly()
            return 130
        return 0

    def stop_quietly(self) -> None:
        if self.state is not None:
            self.state.save()
            self.c.say(f"中断しました。次回の起動で続きから再開できます(実行フォルダ: {self.run_dir})。")


def _fmt(v) -> str:
    if isinstance(v, (list, tuple)):
        return ",".join(str(x) for x in v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _parse_value(key: str, text: str):
    default = parse_settings(None).as_dict()[key]
    if isinstance(default, list):
        return [int(x) for x in text.replace("、", ",").split(",") if x.strip()]
    if isinstance(default, bool):
        raise ValueError("真偽値は変更できません")
    if isinstance(default, int):
        return int(text)
    if isinstance(default, float):
        return float(text)
    return text


def _env_has(env_file: Path, key: str) -> bool:
    if not env_file.exists():
        return False
    return any(line.strip().startswith(f"{key}=") for line in env_file.read_text(encoding="utf-8").splitlines())


def main(argv=None, client=None, console: Console | None = None, run_date: date | None = None, summary_client=None) -> int:
    ap = argparse.ArgumentParser(description="対話式の実行(引数なし)/ run: 質問なしで最後まで")
    ap.add_argument("--local-dir", help=f"ローカル作業フォルダ(環境変数 {ENV_VAR} でも指定できる)")
    ap.add_argument("--input", help="修理データの CSV(指定すると段階1でパスを聞かない)")
    ap.add_argument("--serial-result", help="シリアル変換結果の CSV(指定すると段階4でパスを聞かない。空文字なら飛ばす)")
    ap.add_argument("--internal-comments", choices=("default", "all", "none"), default="default")
    ap.add_argument("--yes", action="store_true", help="すべての質問に y で答える(U-06)")
    ap.add_argument("--run-date", help="実行日(基準日)YYYY-MM-DD。省略すると今日")
    a = ap.parse_args(argv)
    con = console or Console(auto_yes=a.yes)
    if a.yes and not a.input:
        con.say("NG: --yes のときは --input を指定してください。")
        return 1
    local = a.local_dir or (str(local_dir_from()) if local_dir_from() else None)
    if local is None and not a.yes:
        local = clean_path(con.ask(f"ローカル作業フォルダのパスを入力してください(環境変数 {ENV_VAR} を設定すると聞きません):")) or None
    if local is None:
        con.say(f"NG: ローカル作業フォルダを指定してください(--local-dir または環境変数 {ENV_VAR})。")
        return 1
    if not Path(local).exists():
        con.say(f"NG: ローカル作業フォルダがありません: {local}")
        con.say(f"対処: python anomaly_cli.py init --local-dir \"{local}\" で作ってください。")
        return 1
    try:
        rd = run_date or (datetime.strptime(a.run_date, "%Y-%m-%d").date() if a.run_date else None)
    except ValueError:
        con.say("NG: --run-date は YYYY-MM-DD の形で指定してください。")
        return 1
    serial = a.serial_result if a.serial_result is not None else ("" if a.yes else None)
    return Session(con, local, client=client, run_date=rd, internal_mode=a.internal_comments,
                   summary_client=summary_client).run(a.input, serial)


def main_run(argv=None, **kw) -> int:
    """python anomaly_cli.py run ...(U-06)"""
    return main(argv, **kw)


if __name__ == "__main__":
    sys.exit(main())
