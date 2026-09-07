# SETUP

初回に一度だけやること。所要 10 分ほど。
使い方は [README.md](README.md)、用語は [CONTEXT.md](CONTEXT.md) にある。

**API キーは 2 つ要る。どちらも同じ Google Cloud プロジェクトでまかなえる。**

---

## 手順 1: 外部コマンドを入れる

```sh
brew install ffmpeg        # 画面キャプチャに使う（必須）
brew install whisper-cpp   # 字幕が無い動画の文字起こしに使う（任意）
```

`whisper-cpp` はモデルファイルを同梱しない。**初回に必要になった時点で自動で落とす**
（`~/.cache/whisper.cpp/ggml-large-v3.bin`、約 2.9GiB）。
文字起こしを使わないなら `--no-transcribe` を付ければよく、この行は飛ばして構わない。

---

## 手順 2: Gemini API キーを取得する → `GEMINI_API_KEY`

1. https://aistudio.google.com/apikey を開く
2. 「APIキーを作成」からキーを発行する
3. 発行されたキーを控える

公式ドキュメントに **"Every Gemini API key is associated with a Google Cloud project"**
とあるとおり、キーは必ず Cloud プロジェクトに紐づく。新規なら既定プロジェクトが自動で
作られる。**手順 3 で使うプロジェクトと同じものにしておくと管理先が 1 つで済む。**

既定モデルは `gemini-3.8-flash` で、料金ページ上 Free Tier が "Free of charge"。
変更したい場合は `GEMINI_MODEL` か `--model` で上書きできる。

**無料枠のデータの扱いについては [README](README.md#無料枠で承知しておくこと) を必ず読むこと。**
送った字幕と生成された要約は Google の製品改善に使われ、人間がレビューし得る。

---

## 手順 3: YouTube Data API のキーを取得する → `YOUTUBE_API_KEY`

1. https://console.cloud.google.com/ でプロジェクトを開く（手順 2 のものを使い回してよい）
2. 「APIとサービス」→「ライブラリ」で **YouTube Data API v3** を有効化する
3. 「APIとサービス」→「認証情報」→「認証情報を作成」→「**APIキー**」
4. 発行されたキーを控える
5. そのキーを開き、「APIの制限」で **YouTube Data API v3 のみ**に絞る（推奨）

**OAuth は要らない。** このツールが使うのは `playlistItems.list` と `videos.list` だけで、
どちらも公式リファレンスに Authorization セクションが無く、API キー単独で叩ける。

クォータも問題にならない。公式の記載は
**"a default quota allocation of ... 10,000 units per day combined for all other endpoints"**
で、両エンドポイントとも 1 呼び出し 1 unit。50 本の再生リストを 1 回処理して 3 units 程度である。
（`search.list` には「1 日 100 回」の専用枠があるが、このツールは使っていない。）

---

## 手順 4: キーをファイルに置く

公式ドキュメントは **"Treat your Gemini API key like a password."**
**"Never check API keys into source control systems like Git."** と明記している。
`.env` は `.gitignore` 済みなので、**中身が追跡されることはない。**

```sh
cp .env.example .env
```

`.env` を開いて、手順 2 と手順 3 で控えたキーを貼る。

```
YOUTUBE_API_KEY=...
GEMINI_API_KEY=...
```

**`GOOGLE_API_KEY` は設定しないこと。** `GEMINI_API_KEY` と両方あると SDK は
`GOOGLE_API_KEY` を優先し、警告を出す。

> **Claude Code から作業するときの注意**
> `~/.claude-personal/settings.json` に `deny: Read(**/.env*)` があるため、
> **Claude は `.env` を読むことも書くこともできない。** 鍵がコンテキストに入らないための
> 防御なので、外さないほうがよい。Claude にツールを実行させたい場合は、
> `!` を付けて自分のシェルで打つ（下記）。

---

## 手順 5: 動かす

```sh
uv sync

set -a && source .env && set +a

# まず対象を確認するだけ
uv run youtube-scribe "https://www.youtube.com/playlist?list=PL..." --dry-run

# 1 本だけ作ってみる
uv run youtube-scribe "https://www.youtube.com/playlist?list=PL..." --limit 1
```

毎回 `set -a && source .env && set +a` を打つのが面倒なら、シェルの設定に関数を置く。

```sh
scribe() {
  (cd ~/git/private/youtube-scribe \
    && set -a && . ./.env && set +a \
    && uv run youtube-scribe "$@")
}
```

---

## 環境変数の一覧

必須は上の 2 つだけ。残りは既定のままで動く。

| 変数 | 既定 | 用途 |
| --- | --- | --- |
| `YOUTUBE_API_KEY` | — | **必須。** 再生リストと動画メタデータの取得 |
| `GEMINI_API_KEY` | — | **必須。** 要約 |
| `GEMINI_MODEL` | `gemini-3.8-flash` | 要約に使うモデル |
| `GEMINI_FALLBACK_MODELS` | `gemini-3.7-flash,gemini-3.5-flash,gemini-3.5-flash-lite` | クォータ切れのときに順に切り替える先。空文字で無効 |
| `GEMINI_RETRY_ATTEMPTS` | `2` | SDK に試させる回数。**増やすとクォータの消費も増える** |
| `WHISPER_MODEL` | `~/.cache/whisper.cpp/ggml-large-v3.bin` | 文字起こしに使うモデルの場所 |

`GOOGLE_API_KEY` は設定しないこと。`GEMINI_API_KEY` と両方あると SDK は
`GOOGLE_API_KEY` を優先し、警告を出す。

**クォータはモデルごとに独立している。** 無料枠は Flash 系が 1 日 20 本、
Flash Lite 系が 500 本。既定のフォールバック順はこれを使ったもので、
`gemini-3.8-flash` を使い切っても同じ日に続けられる。

---

## つまずきやすいところ

**「再生リストが存在しません」と出る**
その再生リストが**非公開**になっている。非公開の再生リストは、オーナー本人がログインして
いるときしか見えず、API キーでも yt-dlp でも読めない。YouTube 側で「限定公開」に変える。

**`invalid peer certificate: UnknownIssuer` で `uv` が落ちる**
社内ネットワークの自己署名 CA が原因。`uv` に `--system-certs` を付けるか、
`export UV_SYSTEM_CERTS=1` を設定する。ツール本体は `truststore` で OS の証明書ストアを
使うので、実行時は追加の設定が要らない。

**`whisper-cli` が PATH に無い、と言われる**
`brew install whisper-cpp` を実行するか、文字起こしを使わないなら `--no-transcribe` を付ける。

**Gemini のレート制限に当たる**
無料枠の上限は Google がドキュメントから数値表を削除しており、
https://aistudio.google.com/rate-limit でしか確認できない。SDK が既定 5 回まで自動で
リトライし、それでも駄目なら**その動画の記事を作らずに次へ進む**ので、時間をおいて
同じコマンドを再実行すれば残りが処理される。
