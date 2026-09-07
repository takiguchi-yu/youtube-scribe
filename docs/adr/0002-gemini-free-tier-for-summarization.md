# 要約は Gemini の無料枠で行う

要約は Gemini API（Interactions API）の**無料枠**で行い、既定モデルは
`gemini-3.8-flash` とする。`GEMINI_MODEL` で上書きできる。

**Claude ではない理由を、ここに書いておく。** このリポジトリの持ち主は Claude Code を
日常的に使っており、`ai-report` など他のリポジトリも Anthropic 寄りである。だから
将来の読み手は「なぜここだけ Gemini なのか」と必ず思う。理由は 1 つ、**追加の請求を
立てずに、かつ構造化出力のスキーマ強制を保てるのがこれだけだったから**である。

## 比較した 3 案（20 分の動画 1 本あたり）

| 案 | 追加請求 | スキーマ強制 | データの扱い |
| --- | --- | --- | --- |
| **Gemini 無料枠** | **なし** | **あり**（Structured Outputs） | **学習に使われる／人間レビューあり** |
| `claude -p`（Claude Code のサブスク枠） | なし（枠を消費） | なし。JSON の検証と再試行を自前で書く | 学習に使われない |
| Anthropic API キー + Opus 5 | 約 $0.11 | あり（`messages.parse()`） | 学習に使われない |

`claude -p` は実測した。**API キー無しで動く**が、Claude Code のシステムプロンプトを
毎回積むため下地が重い — 些細なプロンプト 1 回で cache 作成 19,874 + 読み 13,510 の
**約 33,000 トークン**、`--system-prompt` と `--tools ''` で置き換えても
**6,951 トークン**残る（素の API 呼び出しなら自前のシステムプロンプト約 600 トークン）。

## 引き換えに受け入れたこと

Gemini の利用規約（Effective March 23, 2026）は無料枠についてこう定めている。

> Google uses the content you submit to the Services and any generated responses to
> **provide, improve, and develop Google products and services** and machine learning technologies

> To help with quality and improve our products, **human reviewers may read, annotate,
> and process your API input and output**... **Do not submit sensitive, confidential,
> or personal information to the Unpaid Services.**

**送るのは公開されている YouTube 動画の字幕**なので、この用途では実害が小さいと判断した。
**限定公開・非公開で機密を含む動画に使うなら、この判断は成り立たない。**

緩和として `store=False` を明示している。既定は `store=True` で、無料枠では
インタラクションが 1 日保持される（有料は 55 日）。

## 既存の慣習に乗った点

`trend-sight` が既に Gemini を Interactions API + Structured Outputs で使っており、
環境変数も `GEMINI_API_KEY` / `GEMINI_MODEL` である。**鍵を新しく取る必要がなく、
命名も揃う。**

## 実装上、踏んだ地雷

- **Interactions API の例外は公開されていない階層にある。**
  `google.genai._gaos.lib.compat_errors` にあり、公開されている `google.genai.errors`
  とは別系統で `errors.APIError` を継承していない（実測で確認）。だから例外クラスを
  import せず、`status_code` の有無で判断している
- **`Interaction` 型も私有パスにしかない。** `create` の戻り値は `Interaction | Stream[...]`
  の union なので、クラスではなく属性で絞っている

## 無料枠の実際の上限（AI Studio で実測、2026-09-07）

**ドキュメントには一切載っていない。** Google は数値表を削除し、AI Studio でしか
見られないようにしている。実際に見に行った値がこれである。

| モデル | RPM | TPM | RPD |
| --- | --- | --- | --- |
| Gemini 3.8 Flash / 3.7 / 3.6 / 3.5 / 3 Flash | **5** | 250K | **20** |
| Gemini 2.5 Flash | 5 | 250K | 20 |
| Gemini 2.5 Flash Lite | 10 | 250K | 20 |
| **Gemini 3.5 Flash Lite / 3.1 Flash Lite** | **15** | 250K | **500** |
| Gemini 3.1 Pro / 2.5 Pro | 0 | 0 | 0（無料枠では使えない） |

**効いてくるのは RPD 20 である。** 1 本 = 1 リクエストなので、Flash 系は 1 日 20 本が上限。
TPM 250K に対して実測のピークは 98.8K で、**トークン量は一度も制約になっていない**
（書き起こしのブロック結合は無駄ではないが、ボトルネックはそこではなかった）。

RPM 5 も効く。**SDK の既定リトライは 5 回**なので、1 本が失敗するとそのリトライだけで
RPM に達する。だから `GEMINI_RETRY_ATTEMPTS` の既定を 2 に下げてある。

### クォータはモデルごとに完全に独立している

これが逃げ道になる。`gemini-3.8-flash` を使い切っても `gemini-3.7-flash` は 0/20 のまま
であり、同じ日に続けられる。`summary.DEFAULT_FALLBACK_MODELS` はこれを使ったもので、
`cli` は**そのモデルが使えないと分かった時点で、同じ動画を次のモデルでそのまま試す。**

Flash Lite 系の **RPD 500** は Flash 系の 25 倍あり、実質の逃げ切り先になる。

### 混雑（5xx）もクォータ切れと同じ扱いにする

切り替えの合図は 429 だけではない。**500 も「そのモデルは今は使えない」を意味する。**

```
Error code: 500 - {'error': {'message': 'gemini-3.8-flash is currently
experiencing high demand, spikes in demand are usually temporary.
Please try again later.', 'code': 'api_error'}}
```

**本文がモデル名を名指ししている。** 混んでいるのはそのモデルであって、こちらの
リクエストに直せるところは無い。当初は 429 だけを切り替えの合図にしていたため、
この 500 で 1 本を落とし、**空いているモデルを 3 つ残したまま「作成 0 本 / 失敗 1 本」で
終わった。** いまは `summary.ModelUnavailableError` が 429 と 5xx の両方を表す。

同じ動画で何度も切り替わることは無い。`cli` の `model_index` は前へしか進まないので、
この変更で増えるリクエストは 1 回の実行あたり「切り替えの回数 x SDK の試行回数」で止まる。

**一時的な 500 のあとに先頭のモデルへ戻す案は採らなかった。** 品質の高いモデルへ
戻れる代わりに、恒常的に 5xx を返すモデルが先頭にあると毎本で試行を捨てることになる。
「`model_index` は単調に進む」という 1 本の規則を保つほうが安い。

## 未確認のまま残っていること
## 実 API で確認できたこと（2026-09-06）

- **`Optional[int]` を含む pydantic のスキーマは、そのまま受理された。**
  pydantic が出す `anyOf: [integer, null]` と `$defs` / `$ref` を含む形で、
  `gemini-3.8-flash` が 36 分の日本語動画を要約し、7 節すべてに `evidence_sec` を
  返した。ドキュメントの "JSON schema support" は `{"type": ["integer","null"]}` の
  形を示しているが、**変換は要らない。**
- **無料枠の上限は `limit: 20`。** ドキュメントには載っていないが、429 の本文が
  数値を返してくる（実測）。

  ```
  Quota exceeded for metric:
  generativelanguage.googleapis.com/generate_content_free_tier_requests,
  limit: 20, model: gemini-3.8-flash
  Please retry in 5.958007113s.
  ```

- **429 の本文は「何秒後に再試行せよ」を教えてくれる。** `RetryInfo` は返らないが、
  `message` の中に平文で入っている。ただし **自分でリトライしてはいけない** —
  SDK が既に 5 回・最大 60 秒で待ち直しており（`_RETRY_ATTEMPTS = 5` /
  `_RETRY_MAX_DELAY = 60.0`）、54 秒程度の指示はその中で吸収される。
  外側にもう一段ループを足したところ、1 本の動画で最大 20 リクエストを投げ、
  **`limit: 20` をその 1 本で使い切った。** この秒数は表示のためだけに読む。
- **`code` は `too_many_requests` であって `quota_exceeded` ではない。** つまり
  日あたりを使い切ったのではなく、短時間に集中しただけ。待てば続けられる。
- **500 のリトライは 1.5 秒で終わる。** Interactions API は公開側の tenacity では
  なく `_gaos` の別のリトライ層を通る。`HttpRetryOptions.attempts` は
  `RetryConfig.max_retries` にそのまま渡り（`_gaos/google_genai.py`）、5xx は
  リトライ対象に入っているが（`_gaos/interactions.py` の
  `["408", "409", "429", "5XX"]`）、初期待ち 500ms・指数 2 なので
  `GEMINI_RETRY_ATTEMPTS=2` では 0.5 秒 + 1.0 秒しか待たない。
  **「一時的」と言われた混雑を待ち切れる長さではない。** だから待つのではなく
  モデルを替える。
