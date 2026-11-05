# 全世界修理データの分類と、製品×部番ごとの修理傾向解析

部品の使用率による異常検知で抽出された「製品(機種)× 部品番号」の修理データ(全世界)を、故障の種類で分類し(Dify 経由の LLM)、次の観点で傾向を集計・比較して、表と中立的な説明文(日本語)を出力する。

1. 全体
2. 最近の修理 vs それ以前(受付日)
3. 使用期間(受付日 − 製造年月)
4. 製造ロットへの集中

分類のコード体系とDifyの呼び出し部は、v2(IR_rep_categorize_v2、日本の修理データの分類パイプライン)から持ってきた。

## 文書

| 文書 | 内容 |
|---|---|
| `docs/20261105_requirements.md` | **要件定義書(正本)**。要件 ID(E / I / G / S / A / T / O / U) |
| `docs/20261105_repo-structure.md` | リポジトリの構成、v2 から持ってきたもの、作る順番と進み具合 |
| `docs/20261105_serial-converter-requirements.md` | シリアル変換ツールの要件定義書(会社PCで実装する別ツール) |
| `docs/20261105_global-part-trend-requirements-draft.md` | 要件のたたき台(決定の経緯) |
| `docs/handoff/20261105_windows_steps.md` | **会社PCでの作業手順(担当者向け)** |
| `docs/handoff/20261105_windows_rules.md` | 会社PCでの作業ルール(GPT 向け) |
| `docs/dify/classify_workflow_setup.md` | 分類用の Dify ワークフローの構築手順 |
| `docs/dify/summary_workflow_setup.md` | 要約用の Dify ワークフローの構築手順(任意) |
| `tests/fixtures/synthetic/README.md` | 架空テストデータの一覧 |

## 進み具合

| 段階 | 内容 | 状態 |
|---|---|---|
| 1. 土台 | 設定ファイル、入力の読み込み、言語判定、設定の検査、`init` / `check` | 実装済み |
| 2. シリアル変換結果の結合 | 変換ツールの出力を結合し、使用期間を計算(`merge-serial`) | 実装済み |
| 3. 解析と定型文 | 4つの観点の表、注目点、レポート(`analyze`) | 実装済み |
| 4. 分類 | Dify での分類、再試行、分類不可、キャッシュ(`classify`) | 実装済み(会社PCでの確認待ち) |
| 5. 対話式の実行 | y / n で最後まで進める(`python anomaly_cli.py`) | 実装済み |
| 6. 品質と要約 | 抜き取り確認、評価(`evaluate`)、比較(`compare-runs`)、Dify での要約(`summarize`) | 実装済み |

## 構成

```
anomaly_cli.py              起動用スクリプト(対話式 / run / init / check / merge-serial / classify / analyze / summarize / evaluate / compare-runs / codes-export / codes-import)
src/anomaly/                プログラム本体
config/
  classification_codes.yaml コード体系の初期値(v2 の 0.4.1 のコピー)
  input_columns.yaml        入力 CSV の列の対応づけの初期値
  settings.yaml             解析の既定値・閾値
  prompts/                  Dify に貼り付けるプロンプトの初期値(分類用・要約用)
tests/                      テスト(テスト名に要件 ID)
tests/fixtures/             テスト専用のコード体系・架空データ
```

## 使い方

```
python -m venv .venv
.venv\Scripts\activate            (Mac: source .venv/bin/activate)
pip install -r requirements-dev.txt
python -m pytest tests -q

python anomaly_cli.py init  --local-dir <ローカル作業フォルダ>     初期値をコピー(既存のファイルは上書きしない)

python anomaly_cli.py --local-dir <ローカル作業フォルダ>         対話式: 入力ファイルのパスを聞かれ、y / n で答えていくと解析の出力まで進む
python anomaly_cli.py run --input <CSV> [--serial-result <CSV>] --yes --local-dir <ローカル作業フォルダ>
                                                                  質問なしで最後まで

python anomaly_cli.py check --local-dir <ローカル作業フォルダ>     設定ファイル一式の検査
python anomaly_cli.py merge-serial --input <入力CSV> --serial-result <変換結果CSV> [--output <CSV>] --local-dir <ローカル作業フォルダ>
                                                                  シリアル変換結果の結合だけを試す(分類はしない)
python anomaly_cli.py classify --input <入力CSV> [--serial-result <変換結果CSV>] --local-dir <ローカル作業フォルダ> [--dry-run]
                                                                  Dify で分類し、実行フォルダに分類済みのデータを書く
python anomaly_cli.py analyze --classified <分類済みのフォルダ> [--serial-result <変換結果CSV>] [--run-date YYYY-MM-DD] --local-dir <ローカル作業フォルダ>
                                                                  分類済みのデータから、製品×部番ごとの表・注目点・レポートを作る
python anomaly_cli.py summarize --run <実行フォルダ> --local-dir <ローカル作業フォルダ>   Dify で要約文を作り report.md に入れる(任意)
python anomaly_cli.py evaluate --gold <正解CSV> --run <実行フォルダ> --local-dir <ローカル作業フォルダ>  日本データの評価
python anomaly_cli.py compare-runs --a <実行フォルダ> --b <実行フォルダ> [--sales-company CMJ]          2つの実行の比較
python anomaly_cli.py codes-export --local-dir <ローカル作業フォルダ>              コード体系 YAML → Excel
python anomaly_cli.py codes-import --local-dir <ローカル作業フォルダ> --xlsx <Excel> Excel → YAML
```

- シリアル → 製造年月の変換は、このプログラムでは行わない。会社PCの別ツール(`docs/20261105_serial-converter-requirements.md`)で変換した結果を、分類の後に結合する。
- Windows 側で変更するファイル(コード体系・列の対応づけ・設定・接続情報・データ・結果)は、コードとは別の**ローカル作業フォルダ**に置く(要件 E-06)。v2 のローカル作業フォルダとは別のフォルダにする(例: `C:\work\anomaly_local\`)。
- ローカル作業フォルダは `--local-dir` か、環境変数 `ANOMALY_LOCAL_DIR` で指定する。
- 動作環境: Python 3.13 以上(Windows・Mac)。
