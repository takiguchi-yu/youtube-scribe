# CLAUDE.md

このリポジトリで作業するときの手順。用語は [CONTEXT.md](CONTEXT.md)、
使い方は [README.md](README.md)、設計の経緯は [docs/adr/](docs/adr/) にある。

## 触る前に

`CONTEXT.md` を読む。**このリポジトリは言葉を厳しく決めている。**
とくに次の 3 つを取り違えると設計が崩れる。

- **再生リストは記事の単位ではない。** 動画を集めるための入力にすぎない
- **書き起こしは、取得手段を問わない言葉。** 字幕でも文字起こしでも書き起こしと呼ぶ
- **記事は成果物であると同時に「処理済み」の印。** 存在＝成功を意味する

## 守る不変条件

> 記事ファイルが存在する ＝ その動画の処理は成功している

これが崩れると、失敗した動画が永久に再試行されなくなる。だから、

- **失敗したら記事を書かない。** 部分的な成果物を残さない
- 書き込みは一時ファイル経由で行い、最後に `replace` する（`store.save`）
- 記事にならなかった画像は捨てる（`store.discard_assets`）

この規則を変えるときは、`store.py` の docstring と `docs/adr/` を一緒に直すこと。

## 責務の分け方

| モジュール | 変更されるのはこのときだけ |
| --- | --- |
| `cli` | 配線とメッセージ。ここにロジックを置かない |
| `youtube_api` | YouTube Data API の叩き方 |
| `playlist` | 入力を動画ID の並びに正規化する規則 |
| `video` | メタデータの読み取りとチャプターの解釈 |
| `media` | **yt-dlp の使い方。yt-dlp を import してよいのはここだけ** |
| `transcript` | 字幕・文字起こしの取り方。3 手段をここで 1 つの形に潰す |
| `summary` | プロンプトとモデル。**Gemini SDK を import してよいのはここだけ** |
| `frame` | ffmpeg の呼び方 |
| `article` | 記事の見た目。**`summary` を import しない**（SDK を引き込まないため）。受け取るのはデータだけ |
| `store` | 保存先とファイル名規則、処理済み判定 |

依存は `cli` から各モジュールへの一方向。`article` と `store` は互いを知らない。

## 変えるときに気をつけること

- **`-ss` は `-i` の前に置く。** 実測で 0.09 秒 対 7.97 秒（600 秒の動画）。
  精度は落ちない（両者の出力は MD5 一致）
- **シーン変化検出は使わない。** スコアが輝度のみで計算されるため、色だけが変わる
  スライド切り替わりを原理的に検出できない。撮る時刻は `summary` が決める
- **画像を Claude に見せない。** 時刻は書き起こしから決まるので、画像入力の
  トークンコストが 0 で済んでいる
- **`whisper-cli` と `ffmpeg` の stdout をパースしない。** 前者は `--output-json`、
  後者は終了コードとファイルの有無で判定する
- **Gemini の例外クラスを import して分岐しない。** Interactions API が投げる例外は
  `google.genai._gaos.lib.compat_errors` にあり、公開されている `google.genai.errors`
  とは**別の階層**で `errors.APIError` を継承していない（実測済み）。`status_code` の
  有無で判断する（`summary.describe_error`）
- **`Interaction` 型も私有パスにしかない。** `create` の戻り値はクラスではなく属性で絞る
- **`store=False` を外さない。** 既定は保存で、無料枠は 1 日保持される
- **失敗の理由を握り潰さない。** 字幕が取れなかった理由は最後のメッセージまで運ぶ。
  実際に「YouTube が 429 を返した」のに「whisper-cli が PATH に無い」とだけ出て、
  原因の切り分けに回り道した
- **一時的な失敗（429 / 5xx）で文字起こしへ落とさない。** 2.9GiB のモデルを落として
  音声認識を始める価値がない。記事を作らず終われば次の実行で再試行される
- **リトライを重ねない。** SDK が既に `_RETRY_ATTEMPTS = 5` / `_RETRY_MAX_DELAY = 60.0`
  で待ち直している（`google/genai/_api_client.py`）。外側にもう一段ループを足したら、
  1 本の動画で 5 x 4 = 20 リクエストになり、**無料枠の `limit: 20` をその 1 本で
  使い切った。** 429 の本文にある「retry in Ns」は、人に見せるためだけに読む
- **429 のエラーコードを信用しない。** 公式の表は `quota_exceeded` を daily、
  `too_many_requests` を短時間の集中としているが、**RPD 20 を使い切っても
  `too_many_requests` が返ってくる**（実測 18 件すべて）。表と挙動が食い違う。
  SDK のリトライを抜けて 429 が出たら、そのモデルは今は使えない、それだけで十分
- **クォータはモデルごとに独立している。** 使い切ったら次のモデルへ移れば同じ日に
  続けられる。Flash 系は RPD 20 / RPM 5、Flash Lite 系は RPD 500 / RPM 15（実測）
- **500 もモデルを替える理由になる。** 返ってくる本文は
  `gemini-3.8-flash is currently experiencing high demand` とモデルを名指しする。
  **429 と 5xx をまとめて `summary.ModelUnavailableError` にしている**のはこのため。
  1 度これを分けていたせいで、空いているモデルを 3 つ残して「作成 0 本」で終わった
- **相手に絞られたら、その場で全体を止める。** `summary.ModelUnavailableError` と
  `transcript.ThrottledError` がその合図で、`cli` は `break` する。**同じ失敗を
  2 回やった** — Gemini のクォータ切れ後に 11 本ぶんの字幕を取り続けて YouTube に
  絞られ、その YouTube 429 でも 3 本続けて叩いて絞りを深めた。相手が「もう受けない」
  と言ったあとの試行は、得るものが無いうえに解除を遠ざける
- モデルは `GEMINI_MODEL` で上書きできる。既定は無料枠で使える最上位の Flash

## 確認

```sh
sh tools/check.sh
```

`ruff format --check` / `ruff check` / `mypy`（strict）/ `pytest` を順に回す。
**4 つ全部が通ってから報告する。**

記事の見た目を変えたらゴールデンを更新する。

```sh
GOLDEN_UPDATE=1 uv run pytest tests/test_article.py
```

**差分を目で見てからコミットすること。** 意図しない変化が混ざっていないか確かめる。

## テストの方針

外部境界（Gemini / yt-dlp / ffmpeg / whisper-cli）はテストしない。
テストが見ているのは、字幕のパース、ファイル名の生成、時刻の変換、記事の組み立てなど、
外部に触らない部分だけである。ここに新しいロジックを足したらテストも足す。
