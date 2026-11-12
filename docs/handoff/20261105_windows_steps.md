# Windows 側の作業手順(担当者向け) — 全世界修理データの傾向解析

- 作成日: 2026-11-05(Mac 側で作成)
- 更新: 2026-11-09(手順4・手順5に、除外の確認を追加)、2026-11-10(8桁の受付日 `YYYYMMDD` を読めるようにした)、2026-11-12(手順8にサマリーシート・製造ロットのグラフと設定の変更を追加)
- 作業のルールは `20261105_windows_rules.md`(先に読む)
- 例のフォルダ: コード `C:\work\IR_rep_categorize_anomaly\`、ローカル作業フォルダ `C:\work\anomaly_local\`

---

## 全体の流れ

| 手順 | 内容 | 目安 |
|---|---|---|
| 1 | コードを展開し、Python の環境を作り、テストを通す | 30分 |
| 2 | ローカル作業フォルダを作り、列名を合わせる | 30分 |
| 3 | 分類用の Dify ワークフローを作る | 30分 |
| 4 | 架空データで通しの確認 | 30分 |
| 5 | 実データ 30〜50 件で分類し、抜き取り確認 | 1〜2時間 |
| 6 | 日本データで評価・内部コメントの比較 | 1時間 |
| 7 | シリアル変換ツールを作り、結合を確かめる | 半日〜 |
| 8 | 対話式の実行で、異常検知の結果を解析する | 運用 |
| 9 | (任意)要約用の Dify ワークフローを作る | 30分 |

## 手順1: コードの展開と環境

1. GitHub からリポジトリ全体をダウンロードし、`C:\work\IR_rep_categorize_anomaly\` に展開する。新しい版が届いたら、このフォルダを丸ごと置き換える(ローカル作業フォルダは別の場所なので消えない)。
2. コードのフォルダで:

```
py -3.15 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest tests -q
```

- 確認: 最後の行が `xxx passed`(失敗が 0)。失敗したら、失敗したテスト名を Mac 側に伝える。
- 社内の証明書で pip が失敗する場合は、v2 のときと同じ方法(証明書の指定)で入れる。

## 手順2: ローカル作業フォルダ

```
python anomaly_cli.py init --local-dir C:\work\anomaly_local
```

1. `C:\work\anomaly_local\input_columns.yaml` を開き、右側の列名を実データの CSV の列名に書き換える。
   - `ID`・`事業コード`・`user_comment` などは v2 と同じ名前にしてある。`製品名`・`部品番号`・`シリアル`・`受付日`・`販社名` は仮の名前なので、必ず確認する。
2. 確認:

```
python anomaly_cli.py check --local-dir C:\work\anomaly_local
```

- 確認: `結果: 問題なし`。

> 毎回 `--local-dir` を書かないために、環境変数 `ANOMALY_LOCAL_DIR` に `C:\work\anomaly_local` を設定してもよい。

## 手順3: 分類用の Dify ワークフロー

`docs/dify/classify_workflow_setup.md` のとおりに作り、`C:\work\anomaly_local\.env` の `DIFY_BASE_URL` と `DIFY_API_KEY_CLASSIFY` を書き換える(初期値のままだとエラーになる)。

## 手順4: 架空データで通しの確認

```
python anomaly_cli.py classify --input tests\fixtures\synthetic\repairs.csv --local-dir C:\work\anomaly_local --dry-run
python anomaly_cli.py classify --input tests\fixtures\synthetic\repairs.csv --local-dir C:\work\anomaly_local
python anomaly_cli.py analyze  --classified C:\work\anomaly_local\outputs\<日時> --local-dir C:\work\anomaly_local
```

- 確認: dry-run の画面に `除外 7 件` と理由別の内訳が出る(架空データには、除外される行をわざと7行入れてある)。実行フォルダに `excluded_rows.csv` ができる。
- 確認: `分類不可 0 件`。実行フォルダの `failures.csv` で、中国語・韓国語の修理にもコードと日本語訳が付いている。`combos\X100__P-001\report.md` が開ける。

## 手順5: 実データ 30〜50 件

1. 実データから 30〜50 件を抜き出した CSV(UTF-8)を `C:\work\anomaly_local\data\` に置く。日本(CMJ)と海外の両方を含める。
2. 先に `--dry-run` を付けて実行し、画面の「除外:」の行を見る。除外が多いときは、下の表で原因を探し、実行フォルダの `excluded_rows.csv`(行番号・理由・問題の値)で該当する行を確かめる。

| 画面の理由 | よくある原因 | 対処 |
|---|---|---|
| 〇〇が空 | `input_columns.yaml` で対応づけた列が、値の入っていない別の列になっている | 列名を確かめる |
| 受付日が読めない | 日付の書き方が `2026-01-05`・`2026/1/5`(時刻付きも可)・`20260105`(8桁)・Excel の日付の数値 以外(例: `01/05/2026`、`2026年1月5日`、`20260105.0`)、または実在しない日付 | CSV を作るときに日付の書き方をそろえる。そろえられなければ Mac 側に書き方を報告する |
| コード体系に無い製品種別 | `事業コード` の値が `ML`・`LENS`・`DC` と完全には一致しない(小文字、全角、別の呼び方など) | 値をそろえる。別の値を使う必要があれば Mac 側に報告する |

3. 除外に問題が無ければ、`--dry-run` を外して実行:

```
python anomaly_cli.py classify --input C:\work\anomaly_local\data\<ファイル>.csv --local-dir C:\work\anomaly_local
```

4. 実行フォルダの次を見る。
   - `sample_check.xlsx`: 原文・日本語訳・付いたコードを見て、明らかにおかしい行の「確認」欄に × を付ける。
   - `language_metrics.csv`: 言語別の判定不能・分類不可の割合
   - `validation_issues.csv`: 分類不可の理由
5. 問題があれば `prompts\classify_prompt.md` を直し、Dify に貼り直して、もう一度実行する(手順5を繰り返す)。
6. Mac 側への報告: 除外の内訳(理由別の件数と、値の書き方)、× の数(言語別)、判定不能の割合(言語別)、分類不可の件数と理由、架空の例に言い換えた問題の例。

## 手順6: 日本データの評価・内部コメントの比較

### 6-1. 評価(過去の分類結果がある場合)
1. 正解 CSV(UTF-8)を作る。列: `repair_id`、`sales_company`、`received_date`、`primary_name`(過去の分類の名称)。
2. 実行:

```
python anomaly_cli.py evaluate --gold <正解CSV> --run <実行フォルダ> --local-dir C:\work\anomaly_local
```

3. `evaluation\unconverted_names.csv` に出た名称は、`eval_name_map.csv`(列: `product_type`, `past_name`, `code`)に追記して、もう一度実行する。
- 過去の分類結果は「メーカー責任の代表故障」なので、この解析の分類とは定義が違う。一致率は参考値。

### 6-2. 内部コメントを渡す/渡さないの比較(CMJ)

```
python anomaly_cli.py classify --input <日本データ> --local-dir C:\work\anomaly_local --output-dir C:\work\anomaly_local\outputs\cmp_default
python anomaly_cli.py classify --input <日本データ> --local-dir C:\work\anomaly_local --output-dir C:\work\anomaly_local\outputs\cmp_all --internal-comments all
python anomaly_cli.py compare-runs --a C:\work\anomaly_local\outputs\cmp_default --b C:\work\anomaly_local\outputs\cmp_all --sales-company CMJ
```

- 確認: 主な故障の一致率と、判定不能の割合の差。渡した方が明らかによい場合は、`settings.yaml` の `internal_comment_excluded_sales_companies` から CMJ を外す。

## 手順7: シリアル変換ツール

1. `docs\20261105_serial-converter-requirements.md` を GPT に渡し、別のフォルダ(例: `C:\work\serial_converter\`)に実装してもらう。受け入れ例(SC-62)のテストが通ることを確かめる。
2. 変換表(`model_types.csv`、`prefix_ym.csv`、`settings.yaml` のタイプ2の起点)を作る。
3. 解析に使う入力 CSV を変換する(出力は CSV)。
4. 結合を確かめる:

```
python anomaly_cli.py merge-serial --input <入力CSV> --serial-result <変換結果CSV> --local-dir C:\work\anomaly_local
```

- 確認: `mfg_status` の `ok` の件数が妥当か、結合できなかった機種(`not_converted`)が何か。

## 手順8: 対話式の実行(運用)

```
python anomaly_cli.py --local-dir C:\work\anomaly_local
```

- 修理データの CSV のパスを聞かれる(ファイルをドラッグ&ドロップしてよい)。表示を確かめながら y / n で進む。
- 段階4でシリアル変換結果の CSV を聞かれる(無ければ Enter)。
- 途中で n を選んだり止めたりしても、次に起動すると「前回の続きから再開しますか?」と聞かれる。
- 出力: 実行フォルダの `index.xlsx`(全組み合わせの一覧)、`combos\<製品>__<部番>\report.md`・`tables.xlsx`、`tableau_long.csv`(Tableau 用)。
- `tables.xlsx` の先頭のシート「サマリー」に、件数・①使用期間で比べる・②時期で比べる・注目点の一覧・3つのグラフがまとまっている。その下に ③製造ロットで比べる(製造年月ごとの表と、件数・構成比の積み上げ縦棒。★=注目点のロット)がある(要件定義書 7.7 節)。
- 期間・区切りを変えるときは、`C:\work\anomaly_local\settings.yaml` を書き換える(対話式の段階5でも変えられる)。
  - `recent_months`: 直近の月数(既定 6)。受付日で数え、実行日の月は含めない。
  - `usage_split_months`: 使用期間(受付日の年月 − 製造年月)をこの月数「未満 / 以上」に分ける(既定 24。24か月ちょうどは「以上」)。
  - `summary_top_n`: サマリーに出す故障の種類の数(既定 10)。
  - `keep_top_codes`: 件数が少なくても「その他(少数)」にまとめない、上位の種類の数(既定 5)。
- **2026-11-12 の版から、`early_months` は使われなくなった。** ローカル作業フォルダの `settings.yaml` に `early_months: 12` の行が残っていると、`check` などで「使われなくなりました」と表示される(エラーにはならない)。その行を消し、必要なら `usage_split_months` の行を足す。

## 手順9: (任意)要約文

`docs/dify/summary_workflow_setup.md` のとおりに作り、`.env` に `DIFY_API_KEY_SUMMARY` を書く。対話式の段階7で要約を作るか聞かれるようになる。
