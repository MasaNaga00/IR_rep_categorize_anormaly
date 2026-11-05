# Dify ワークフロー構築手順書(全世界修理データの分類用)

- 作成日: 2026-11-05
- 対象: 要件定義書 `docs/20261105_requirements.md` の G-06、G-25〜G-29、E-08、E-10
- v2(日本の修理データ用)のワークフローとは**別のアプリ**として作る。入力の形と出力の形が違うため。

---

## 1. 全体構成

```
[開始ノード]  product_name / code_list / records
     ↓
[LLM ノード]  SYSTEM: classify_prompt.md(product_name・code_list を変数挿入)
              USER:   records
     ↓
[終了ノード]  result = LLM の出力テキスト
```

### 1.1 入力変数(開始ノード)

| 変数名 | フィールドタイプ | 必須 | 最大長 | 内容 |
|---|---|---|---|---|
| `product_name` | 短いテキスト | ✓ | 100 | 製品種別の表示名(例: ミラーレスカメラ) |
| `code_list` | 段落 | ✓ | 30000 | コード一覧(1行1コード: `コード \| 名称 \| 説明文`) |
| `records` | 段落 | ✓ | 50000 | 修理の JSON 配列(文字列)。各要素は `rec_key` とコメント |

- 変数名は上のとおりにすること(Python 側がこの名前で送る)。
- 最大長が足りないと Dify がエラーを返す。内部コメントが長い販社が多い場合は、`records` の最大長を増やすか、バッチを小さくする(`settings.yaml` の `batch_size`)。

### 1.2 出力変数(終了ノード)

| 変数名 | 内容 |
|---|---|
| `result` | LLM の出力テキスト(JSON 文字列)。Python 側がパース・検証する |

## 2. 作成手順

### 2.1 アプリケーションの作成
1. Dify の「スタジオ」→「アプリを作成」→「ワークフロー」を選ぶ。
2. 名前の例: `repair-classify-global`

### 2.2 開始ノード
1.1 の表のとおりに3つの変数を追加する。

### 2.3 LLM ノード
- モデル: 社内で利用可能なモデル(v2 と同じでよい)
- Temperature: `0`
- 最大トークン数: 8000 以上(10修理のバッチで、日本語訳 `excerpt_ja` を含めて足りるように)
- Response Format: **JSON Object**(利用できる場合)
- SYSTEM:
  1. ローカル作業フォルダの `prompts/classify_prompt.md` を開き、`---` の行より**下**をすべて貼り付ける。
  2. 本文中の `{{product_name}}` と `{{code_list}}` を消し、同じ場所に Dify の変数挿入で、開始ノードの `product_name` と `code_list` を挿入する。
- USER: 開始ノードの `records` だけを変数挿入する。

### 2.4 終了ノード
- 出力変数 `result` を追加し、値に LLM ノードの `text` を選ぶ。

### 2.5 公開と API キー
1. 右上の「公開」を押す。
2. 「API アクセス」で API キー(`app-` で始まる)を発行する。
3. ローカル作業フォルダの `.env` に設定する。

```
DIFY_BASE_URL=https://<Dify のホスト>/v1
DIFY_API_KEY_CLASSIFY=app-xxxxxxxx
```

- `DIFY_BASE_URL` は `/v1` までを書く。
- 社内の証明書が必要な場合は、`.env` に `REQUESTS_CA_BUNDLE=<証明書ファイルのパス>` を追加する。

## 3. 動作確認

### 3.1 リクエストの中身の確認(Dify を呼ばない)

```
python anomaly_cli.py classify --input tests\fixtures\synthetic\repairs.csv --local-dir C:\work\anomaly_local --dry-run
```

- 実行フォルダの `raw\batch_001_request.json` を開き、`inputs` の3つの値を、Dify のテスト実行画面の3つの入力欄に貼り付けて実行する。
- `result` が `{"results": [...]}` の形で、送った修理の数だけ結果があることを確認する。

### 3.2 架空データでの通し確認

```
python anomaly_cli.py classify --input tests\fixtures\synthetic\repairs.csv --local-dir C:\work\anomaly_local
```

- 架空データは15行で、8件が分類の対象(7件は入力の検査で除外される想定。`tests/fixtures/synthetic/README.md`)。
- 目安: 分類不可が 0 件。中国語・韓国語のコメントにもコードが付き、`excerpt_ja` に日本語訳が入っていること(実行フォルダの `failures.csv`)。
- コメントがすべて空欄の修理(R005)は Dify に送らず、判定不能になる。

### 3.3 実データでの少数の確認
1. 実データから 30〜50 件程度を抜き出した CSV で `classify` を実行する。
2. `failures.csv` を Excel で開き、原文(excerpt)・訳(excerpt_ja)・コードを見て、明らかにおかしいものが無いかを見る。
3. 問題があれば、プロンプト(ローカル作業フォルダの `prompts/classify_prompt.md`)を直して Dify に貼り直し、もう一度実行する。
   - プロンプトを変えると、キャッシュも別になる(前の結果は使われない。要件 G-16)。

## 4. トラブルシューティング

| 現象(`validation_issues.csv` の種類) | 確認すること |
|---|---|
| `api_error`(401/403) | API キー(`DIFY_API_KEY_CLASSIFY`)、`DIFY_BASE_URL` の `/v1` |
| `api_error`(タイムアウト) | `settings.yaml` の `batch_size` を小さくする(例: 5) |
| `parse_error` | Response Format を JSON Object にする。`raw\*_response.json` で、説明文が付いていないか確認 |
| `missing_record` | 最大トークン数が足りず途中で切れている可能性。最大トークン数を増やすか、バッチを小さくする |
| `unknown_code` | コード一覧に無いコードを返している。`code_list` の最大長が足りているか |
| `primary_mismatch` | primary_code が failures に無い。プロンプトの「2. primary_code」の指示が守られているか |
| 分類不可が多い | Dify 側の障害の可能性。時間をおいて同じコマンドを実行すると、分類不可の修理だけが送り直される(成功した修理はキャッシュから使う) |
