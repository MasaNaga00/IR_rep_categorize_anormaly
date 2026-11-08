# 新リポジトリの構成案

- 作成日: 2026-11-05
- ステータス: 確定(2026-11-05 ユーザ承認。案A・パッケージ名 anomaly・第5節の順番)。段階1の実装と、シリアル変換を別ツールにした変更(2026-11-05)に合わせて更新
- 対応する要件定義書: `docs/20261105_requirements.md`(要件 ID は同書のもの)
- 「v2」は既存のリポジトリ IR_rep_categorize_v2 を指す。v2 のファイルの中身は 2026-11-05 時点のコピー(このフォルダ)で確認した。

---

## 1. リポジトリの作り方【要確認】

今のフォルダ(`IR_rep_categorize_anormaly`)は v2 一式のコピーで、v2 の git の履歴も入っている。新しいリポジトリにする方法は次のどちらか。

| 案 | 内容 | 注意 |
|---|---|---|
| A(推奨) | 新しい空のフォルダを作り、第3節で「持ってくる」とした v2 のファイルだけをコピーして、新しく git を始める | 今のフォルダには手を付けない。v2 のファイルの由来は README に書く |
| B | 今のフォルダを使い、v2 専用のファイルを削除し、`.git` を消して git を始め直す | 削除を伴う(削除の前に一覧を示して確認を取る) |

- リポジトリ名: 今のフォルダ名を使うなら `IR_rep_categorize_anormaly`。綴りは英語では `anomaly` なので、新しく作るなら `IR_rep_categorize_anomaly` にする選択肢もある。

---

## 2. ディレクトリ構成

```
IR_rep_categorize_anomaly/
├── anomaly_cli.py                  起動用(対話式の入口と個別コマンド)U-02, U-07
├── README.md
├── requirements.txt                pyyaml, requests, python-dotenv, openpyxl, pandas, scipy  E-04
├── requirements-dev.txt            + pytest, requests-mock
├── .gitignore / .gitattributes
├── config/                         初期値(init でローカル作業フォルダへコピーする)E-06
│   ├── classification_codes.yaml     v2 からコピー
│   ├── input_columns.yaml            新しい列(I-01)
│   ├── settings.yaml                 既定値の一覧(要件定義書 第12節)
│   ├── prompts/
│   │   ├── classify_prompt.md        分類用(G-27)
│   │   └── summary_prompt.md         要約用(T-03)
├── src/anomaly/
│   ├── __init__.py
│   ├── constants.py
│   ├── settings.py                 settings.yaml の読み込みと値の検査
│   ├── local_config.py             ローカル作業フォルダと設定ファイルの探し方
│   ├── init_local.py
│   ├── check.py                    設定ファイル一式の検査(U-09)
│   ├── codebook.py                 コード体系(v2)
│   ├── codes_excel.py              コード体系の Excel 入出力(v2)
│   ├── dify_client.py              Dify の呼び出し・リトライ(v2、E-08, E-10)
│   ├── classify_cmd.py             classify コマンド(入力 → 分類 → 分類済みのデータ)
│   ├── input_loader.py             入力の読み込み・rec_key(I-01〜I-06)
│   ├── lang.py                     言語判定(G-02)
│   ├── classify/
│   │   ├── llm_request.py          バッチ作成・Dify への入力(G-11, G-12, G-25, G-26)
│   │   ├── llm_response.py         パース・検証(G-28, G-29)
│   │   ├── cache.py                キャッシュ(G-16)
│   │   └── runner.py               バッチ → 1件ずつの再試行 → 分類不可(G-18〜G-23)
│   ├── serial_merge.py             シリアル変換結果の結合・使用期間(S-08、S-10〜S-19)
│   ├── analysis/
│   │   ├── prepare.py              組み合わせごとのデータ作成、少数コードのまとめ(A-01〜A-04)
│   │   ├── stats.py                カイ二乗・フィッシャー・BH 補正(A-09)
│   │   ├── aspects.py              4つの観点の表(A-05〜A-13c, A-15〜A-15c, A-19, A-22〜A-24)
│   │   └── findings.py             注目点の判定と findings.json(A-14〜A-14b)
│   ├── report/
│   │   ├── templates.py            定型文(層1)(T-02, T-04, T-07)
│   │   ├── summary.py              Dify での要約(層2)(T-02, T-03)
│   │   ├── number_check.py         要約文の数値の照合(T-05)
│   │   ├── examples.py             代表事例の抽出(T-08)
│   │   └── markdown.py             report.md の組み立て
│   ├── outputs/
│   │   ├── files.py                CSV・JSON の書き出し、フォルダ名の処理(O-01, O-06)
│   │   ├── excel.py                tables.xlsx, index.xlsx(O-04)
│   │   └── tableau.py              tableau_long.csv
│   ├── quality/
│   │   ├── sample_check.py         抜き取り確認 Excel・言語別の指標(G-14)
│   │   ├── evaluate.py             日本データの評価(G-15)
│   │   └── compare_runs.py         2つの実行結果の比較(G-30)
│   ├── analyze.py                  解析の実行と analyze コマンド(第7〜9節)
│   ├── state.py                    段階の記録と再開(U-04, U-05)
│   └── cli.py                      対話式の流れ(U-01〜U-08)
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── codebook_test.yaml        テスト専用のコード体系(v2 のものを流用)
│   │   ├── settings_test.yaml
│   │   ├── serial_results/           架空のシリアル変換結果(repairs.csv を変換ツールで変換した想定)
│   │   └── synthetic/
│   │       ├── repairs.csv           架空の修理データ(4言語、修理IDの重複、空欄、全空欄を含む)
│   │       ├── mock_llm_results.json Dify の応答の模擬(正常・エラー・壊れた JSON)
│   │       ├── no_difference.csv     差が無いデータ(注目点が出ないことの確認用)
│   │       └── README.md             シナリオの一覧
│   └── test_*.py                   要件定義書 第11節の表のとおり
└── docs/
    ├── 20261106_handoff.md               引き継ぎ資料(残っている問題・次の作業)
    ├── 20261105_requirements.md          要件定義書(正本)
    ├── 20261105_repo-structure.md        本書
    ├── 20261105_global-part-trend-requirements-draft.md  たたき台(決定の経緯)
    ├── dify/
    │   ├── classify_workflow_setup.md    分類用ワークフローの構築手順
    │   └── summary_workflow_setup.md     要約用ワークフローの構築手順
    └── handoff/
        ├── 20261105_windows_steps.md     Windows 側の作業手順(担当者向け)
        └── 20261105_windows_rules.md     Windows 側の作業ルール(GPT 向け)
```

---

## 3. v2 から持ってくるもの・作り直すもの・持ってこないもの

### 3.1 持ってくる(ほぼそのまま)

| v2 のファイル | 新しい場所 | 変更点 |
|---|---|---|
| `src/repclass/codebook.py` | `src/anomaly/codebook.py` | v2 では `claim_reproduction_statuses` が必須だったため、任意に変更した(E-01) |
| `src/repclass/codes_excel.py`、`codes_export.py`、`codes_import.py` | `src/anomaly/codes_excel.py`(コマンドは `anomaly_cli.py` から呼ぶ) | パッケージ名だけ |
| `src/repclass/check_codebook.py` | `src/anomaly/check.py` に統合 | 他の設定ファイルの検査を足す(U-09) |
| `src/repclass/dify_client.py` | `src/anomaly/classify/dify_client.py` | API キーを、分類用・要約用で切り替えられるようにする(E-08) |
| `config/classification_codes.yaml` | `config/classification_codes.yaml` | 内容は v2 の最新(0.4.1)のまま。先頭のコメントに「新リポジトリへコピー」を1行足した |
| `tests/fixtures/codebook_test.yaml` | 同じ場所 | そのまま |
| `.gitattributes`、`.gitignore` | 同じ場所 | `.gitignore` に `cache/` などを足す |

### 3.2 一部を流用して作り直す

| v2 のファイル | 新しい場所 | 流用する部分 / 作り直す部分 |
|---|---|---|
| `local_config.py`、`init_local.py` | 同名 | 探す順の仕組みは流用。環境変数名(`ANOMALY_LOCAL_DIR`)とファイルの一覧を変える |
| `llm_response.py` | `classify/llm_response.py` | パース(R-33)は流用。検証は新しい出力形式に合わせて作り直す(G-29) |
| `llm_request.py` | `classify/llm_request.py` | Dify への入力の組み立て方は流用。`records` の中身を変える(G-25) |
| `input_loader.py` | 同名 | 列の対応づけは流用。重複の扱いを変え、`rec_key` を付ける(I-02) |
| `evaluate.py` | `quality/evaluate.py` | 名称 → コードの変換(R-55a)は流用。評価の中身は作り直す(G-15) |
| `outputs.py` | `outputs/files.py` | CSV・JSON の書き出しの部分を流用 |
| `pipeline.py`(バッチの回し方・再試行・`--resume`) | `classify/runner.py`、`state.py` | 考え方は流用。1件ずつ2回の再試行、分類不可、キャッシュを足す(G-18〜G-23) |

### 3.3 持ってこない(v2 専用)

| v2 のファイル | 理由 |
|---|---|
| `judge.py` | 再現・メーカー責任・代表故障の判定は範囲外 |
| `failure_count.py` | 丸数字を使わない |
| `tables.py` | v2 の中間データ(申告表など)は使わない |
| `run.py`、`repclass_cli.py` | `anomaly_cli.py`・`cli.py` で置き換える |
| `config/prompts/v2/system_prompt.md` | 新しいプロンプトを作る(判断の考え方は参考にする) |
| `tests/v2/` の v2 専用テスト、`tests/fixtures/synthetic/`(v2 用) | 新しく作る |
| `docs/` の v2 の文書 | v2 のリポジトリに残す |

---

## 4. ローカル作業フォルダ(Windows 側)の中身

v2 のローカル作業フォルダとは**別のフォルダ**にする(例: `C:\work\anomaly_local\`)。設定や結果が混ざらないようにするため。

```
anomaly_local/
├── classification_codes.yaml     コード体系(init でコピー。Windows 側で調整してよい)
├── input_columns.yaml            入力の列名の対応づけ(実データの列名に合わせる)
├── settings.yaml                 既定値・閾値(調整してよい)
├── prompts/
│   ├── classify_prompt.md        Dify に貼り付ける分類用プロンプト
│   └── summary_prompt.md         Dify に貼り付ける要約用プロンプト
├── eval_name_map.csv             評価用の名称 → コードの対応表(必要なときに人が作る)
├── .env                          DIFY_BASE_URL, DIFY_API_KEY_CLASSIFY, DIFY_API_KEY_SUMMARY
├── data/                         入力の CSV
├── cache/                        分類結果のキャッシュ(自動)
└── outputs/                      実行結果(自動)
```

Windows 側で人が用意するもの:

| もの | 時期 | 備考 |
|---|---|---|
| `input_columns.yaml` の列名 | 最初 | 実データの列名を確認して書き換える |
| シリアル変換ツールと変換表 | 使用期間・ロットを解析するまで | 別の要件定義書 `docs/20261105_serial-converter-requirements.md` で、会社PCで実装する。解析プログラムとは別のフォルダに置く |
| シリアル変換結果の CSV | 解析の段階4(結合)まで | 入力 CSV を変換ツールで変換したもの。無くても解析は止まらない(使用期間・ロットの観点が「情報なし」になる) |
| Dify の分類用ワークフロー | 分類の前 | 手順書(`docs/dify/classify_workflow_setup.md`)を後で作る |
| Dify の要約用ワークフロー | 任意 | 無くても定型文のレポートは出る |

---

## 5. 作る順番(案)

Dify に依存しない部分から作る。解析部は「分類済みのデータ」があれば動くので、架空の分類結果で先に作り込める。

| 段階 | 内容 | 要件 | Windows で確認できること |
|---|---|---|---|
| 1. 土台 | v2 からのコピー、`settings.py`、`local_config.py`、`input_loader.py`、`lang.py`、`check.py`、`init` | E-01〜E-09、I-01〜I-06、G-02、U-09 | 実データが読み込めるか(列名・日付・件数) |
| 2. シリアル変換結果の結合 | `serial_merge.py` | S-08、S-10〜S-19 | 変換ツールの出力を結合して、`mfg_status` の件数が妥当か |
| 3. 解析と定型文 | `analysis/`、`report/templates.py`・`markdown.py`・`examples.py`、`outputs/` | A-01〜A-24、T-01、T-02(層1)、T-04、T-06〜T-08、O-01〜O-06 | (分類が無くても)架空の分類結果で出力の形を確認 |
| 4. 分類 | `classify/`、分類用プロンプト、Dify の手順書 | G-01〜G-30、E-10 | 少数(数十件)で分類し、抜き取り確認 |
| 5. 対話式の実行 | `cli.py`、`state.py` | U-01〜U-08 | 実データで最初から最後まで |
| 6. 品質と要約 | `quality/`、`report/summary.py`・`number_check.py`、要約用プロンプト・手順書、Windows 向けの手順書 | G-14、G-15、G-30、T-02(層2)、T-03、T-05 | 抜き取り確認、日本データでの評価、内部コメントの比較 |

- 段階ごとに、Mac でテストが通った状態で GitHub に上げる。Windows 側は段階1・2・4・5の後に実データで確認し、問題を短いメモで返す(v2 と同じ)。
- 段階3までは Dify を使わないので、会社 PC の Dify 側の準備と並行して進められる。

---

## 6. 決定事項(2026-11-05)

| No | 内容 | 決定 |
|---|---|---|
| P1 | リポジトリの作り方・名前 | 案A。新しいフォルダ `IR_rep_categorize_anomaly` |
| P2 | パッケージ名・起動ファイル | `anomaly`、`anomaly_cli.py` |
| P3 | 作る順番 | 第5節のとおり |

## 7. 進み具合

| 段階 | 状態 |
|---|---|
| 1. 土台 | 実装済み(2026-11-05)。シリアル変換表の読み込み・検査は、変換を別ツールにしたため外した |
| 2. シリアル変換結果の結合 | 実装済み(2026-11-05)。`merge-serial` コマンドで、分類の前に結合だけを試せる |
| 3. 解析と定型文 | 実装済み(2026-11-05)。`analyze` コマンドで、分類済みのデータからレポートまで作れる |
| 4. 分類 | 実装済み(2026-11-05)。`classify` コマンド、分類用プロンプト初版、Dify の手順書。**会社PCでの確認待ち** |
| 5. 対話式の実行 | 実装済み(2026-11-05)。`python anomaly_cli.py` で y / n で最後まで、`run --yes` で質問なし |
| 6. 品質と要約 | 実装済み(2026-11-05)。抜き取り確認・言語別指標・`evaluate`・`compare-runs`・`summarize`、Windows 向けの手順書 |
| 次 | 会社PCで確認(`docs/handoff/20261105_windows_steps.md`)。シリアル変換ツールは会社PCで実装。残っている問題と次の作業は `docs/20261106_handoff.md`(2026-11-06) |
| (別ツール)シリアル変換 | 要件定義済み。会社PCで実装する |
