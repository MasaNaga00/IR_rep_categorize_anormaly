# 引き継ぎ資料: Windows 側(実データ環境)での作業ルール — 全世界修理データの傾向解析

- 作成日: 2026-11-05(Mac 側で作成)
- 読み手: Windows 側で作業する AI(GPT)と担当者
- 目的: 実データのある会社 PC で、環境構築・Dify 構築・実データでの実行・確認・調整を行うためのルールを示す。手順は `20261105_windows_steps.md`。

---

## 0. 最初に読む文書

| 文書 | 内容 |
|---|---|
| `docs/20261105_requirements.md` | **要件定義書(正本)**。要件 ID(E / I / G / S / A / T / O / U)はここで定義している |
| `docs/20261105_serial-converter-requirements.md` | シリアル変換ツールの要件定義書(会社 PC で実装する別ツール。SC-xx) |
| `docs/dify/classify_workflow_setup.md` | 分類用の Dify ワークフローの構築手順 |
| `docs/dify/summary_workflow_setup.md` | 要約用の Dify ワークフローの構築手順(任意) |
| `docs/handoff/20261105_windows_steps.md` | 担当者向けの手順 |
| `tests/fixtures/synthetic/README.md` | 架空テストデータの一覧 |

## 1. 作業のルール(必ず守る)

### 1.1 変更してはいけないもの(E-02)
- このリポジトリの `src/`(プログラム本体)と `tests/`(テスト)
- そのほか、リポジトリ内のファイル全般

プログラムの不具合や、仕様と合わない点を見つけても、**コードを直さない**。第4節の書式で報告する。

### 1.2 変更してよいもの(E-06)
**ローカル作業フォルダ**(例: `C:\work\anomaly_local\`。v2 のローカル作業フォルダとは別)の中のファイルだけ。

| ファイル | 変更の例 |
|---|---|
| `input_columns.yaml` | 入力 CSV の列名に合わせる(最初に必ず確認) |
| `settings.yaml` | 閾値・期間などの調整(変更後は `check`) |
| `classification_codes.yaml` | コードの説明文の調整など。**キーの構造は変えない**。変更後は `check`。Excel で直す場合は `codes-export` → 修正 → `codes-import` |
| `prompts/classify_prompt.md` | 分類用プロンプトの調整。**変更したら Dify に貼り直す**(キャッシュも別になる) |
| `prompts/summary_prompt.md` | 要約用プロンプトの調整(Dify に貼り直す) |
| `eval_name_map.csv` | 評価用の、過去の名称とコードの対応表 |
| `.env` | Dify の接続情報(共有しない) |
| `data\`, `outputs\`, `cache\` | 入力データ、実行結果、分類のキャッシュ |

### 1.3 例外: シリアル変換ツール
- シリアル変換ツールは、**会社 PC で新しく実装する別のツール**である(このリポジトリには含めない)。
- `docs/20261105_serial-converter-requirements.md` に従い、別のフォルダ(例: `C:\work\serial_converter\`)に作る。変換表も会社 PC で作る。
- 受け入れ例(SC-62)のテストがすべて通ることを確かめる。
- 解析プログラムに渡す変換結果は **CSV**(SC-41)。列名 `serial_normalized` / `lot_code` / `lot_order` / `mfg_ym` / `mfg_status` / `mfg_warning` は変えない(SC-43)。

### 1.4 情報の流れ(E-02)
- Mac → Windows の一方向。Windows 側の変更内容や実行結果は Mac に戻らない。
- Mac に伝えられるのは、担当者が手でメモできる程度の短い情報だけ(第4節)。
- **実データの内容(コメント本文、修理ID、シリアルなど)は報告に書かない。** 例が必要なら、架空の短い例に言い換える。

## 2. プロンプト・設定を調整するときの考え方

- 分類のルール(要件 G-06〜G-13、G-31)を変えない範囲で調整する。特に次は変えない。
  - 修理者が再現できなかったユーザ申告も、故障の種類に含める(G-07)
  - 主な故障の選び方の順番(G-08)
  - 読み取れない修理は推測しない(G-13)
  - 交換部品の情報を LLM に渡さない(G-31)
- 判定不能が多すぎる場合は、どの言語・どの販社で多いか(`language_metrics.csv`、対話式の段階1の表示)を先に見る。
- 注目点の閾値(`settings.yaml` の `min_group_n`・`alpha`・`min_diff_pt`・`min_code_n`)は、**下げると偶然の差を注目点にしやすくなる**。下げる場合は理由を報告に書く。

## 3. 確認の順番

1. テストが通る(`python -m pytest tests -q`)
2. `check` が通る
3. 架空データで `classify --dry-run` → `classify` → `analyze`
4. 実データ 30〜50 件で分類し、`sample_check.xlsx` を見る
5. 日本データで評価(`evaluate`)、内部コメントの比較(`compare-runs`)
6. シリアル変換ツールを作り、`merge-serial` で結合を確かめる
7. 対話式の実行(`python anomaly_cli.py`)で最初から最後まで

## 4. Mac 側への報告の書式

担当者が Mac 側のチャットに手で書き込む。1件あたり数行。

```
[要件 ID] 現象(1〜2行)
例: (架空の短い例)
```

書く内容の例:
- `G-29: parse_error が 10 バッチ中 3 回。応答の最後が途中で切れている`
- `G-14: 中国語の抜き取り 20 件中 × 4 件。例: 「不开机」なのに 動作不良 になる`
- `A-05a: 判定不能が 40%(英語)。例: "Repaired, checked OK" だけの修理が多い`
- `U-03: 段階4 で変換結果の CSV を指定すると NG。メッセージ: 列がありません: 受付日`

テストが失敗した場合は、失敗したテスト名(要件 ID が入っている)の一覧を書く。
