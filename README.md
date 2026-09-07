# 🎬 youtube-scribe

**「あとで見る」に溜まった動画を、読み返せる学習メモに変える。**

再生リストを渡すだけ。動画 1 本ごとに Markdown が 1 本できて、
**要点・詳細・用語**に加えて **話していた場面のスクショ** まで入る 📸

```sh
make scribe URL="https://www.youtube.com/playlist?list=PL..."
```

```
[1/9] 責任あるソフトウェアエンジニアリング - Forkwell Library#124
  書いた: 責任あるソフトウェアエンジニアリング---Forkwell-Library#124--Z_ZOeGDj_kA.md
...
作成 9 本 / 失敗 0 本
```

> 13 時間ぶんの動画が 13 本のメモになりました。**API 費用は 0 円** 🎉

---

## ⚡ はじめに

初回だけ [SETUP.md](SETUP.md) を 10 分（ffmpeg を入れて、API キー 2 つを `.env` に書く）。
**どちらのキーも無料枠で足ります。**

## 🚀 使う

```sh
make                                     # 📋 タスク一覧
make dry-run URL="<再生リストのURL>"      # 👀 対象を見るだけ。API を叩かない
make scribe  URL="<再生リストのURL>"      # ✍️  記事を作る
make scribe  URL="<動画のURL>"            # 🎯 単体動画も OK
make scribe  URL="..." ARGS="--limit 3"  # 🔧 オプションを足す
```

**同じ URL を何度打ってもいい** ♻️ 既にある記事は飛ばすので、
動画が増えたら同じコマンドを打つだけ。

<details>
<summary>💨 どこからでも <code>scribe URL</code> で呼びたい</summary>

シェルの設定に置いてください。

```sh
scribe() {
  (cd ~/git/private/youtube-scribe && set -a && . ./.env && set +a \
    && uv run youtube-scribe "$@")
}
```
</details>

## 📁 できあがるもの

```
articles/
├── 📄 Rust-の所有権入門--dQw4w9WgXcQ.md
└── 🖼  assets/dQw4w9WgXcQ/63.jpg
```

記事の流れは **要点 → 詳細 → 用語 → 所感欄**。
所感欄は空のまま置いてあります ✍️ **そこを埋めるのはあなたの仕事。**

## 📚 読んだ / まだ読んでない

**所感欄が空かどうかが、そのまま「読んだ印」**になります 👀
埋めれば読了。専用の管理ファイルは持ちません。

```sh
make unread   # 🔖 まだ読んでいない記事（所感欄が空）
make read     # ✅ 読み終えた記事
```

```
  1. 責任あるソフトウェアエンジニアリング---Forkwell-Library#124
  2. 侵入技術入門---Forkwell-Library-#123
  ...
未読 15 本 / 全 16 本
```

読んだけど特に書くことがなければ、一行だけ書いておけば十分です 🙆

🔄 **作り直したいときは記事ファイルを消すだけ。** ファイルがあること自体が
「処理済み」の印なので、消せば次の実行で作り直されます。

## 🎛 よく使うオプション

`ARGS="..."` に渡します。全部は `uv run youtube-scribe --help` に 📖

| | |
| --- | --- |
| `--limit N` | N 本だけ処理する |
| `--no-frames` | 🏃 スクショを撮らない。速い |
| `--no-transcribe` | 字幕が無い動画は飛ばす（2.9GiB のモデルを落とさずに済む） |
| `--offset-sec N` | スクショを話し始めの N 秒後に撮る（既定 3） |
| `--model NAME` | 要約に使うモデルを変える |

## 🩹 困ったとき

| 症状 | 対処 |
| --- | --- |
| 🖼 画像が付かない | `ffmpeg -version` が通るか確認。`brew reinstall ffmpeg` で直ることが多い |
| ⏳ 「レート制限に当たった」 | **無料枠は 1 日 20 本まで。** 16:00（日本時間）にリセット。残量は [AI Studio](https://aistudio.google.com/rate-limit) |
| 🚧 「YouTube が字幕の取得を拒否した」 | 叩きすぎ。**40 分ほど待つ**（叩き続けると解除が遠のきます） |
| 🔒 「再生リストが存在しません」 | 非公開の再生リストは読めません。**限定公開**に変えてください |
| 🤔 記事の中身が薄い | front matter の `source` を確認。`自動字幕` は句読点が無く精度も落ちます |

😌 止まっても成果は消えません。**記事が無い動画だけが次回に再試行されます。**

## ⚠️ 承知しておくこと

- 🎥 **字幕の取得に yt-dlp を使っています。** 公式 API では他人の動画の字幕が取れないため。
  YouTube の規約に触れる点を承知したうえでの選択で、**個人の非公開利用に限ります**
  → [ADR 0003](docs/adr/0003-use-yt-dlp-for-captions.md)
- 🤖 **Gemini の無料枠は、送った内容が Google の製品改善に使われ、人間がレビューし得ます。**
  公開動画の字幕なら実害は小さいですが、**機密を含む動画には使わないでください**
  → [ADR 0002](docs/adr/0002-gemini-free-tier-for-summarization.md)

## 📚 もっと知りたいとき

| | |
| --- | --- |
| 🛠 [SETUP.md](SETUP.md) | 初回セットアップ |
| 📖 [CONTEXT.md](CONTEXT.md) | このツールで使う言葉の定義 |
| 🤝 [CLAUDE.md](CLAUDE.md) | コードを触るときの手順と、守るべき不変条件 |
| 🧭 [docs/adr/](docs/adr/) | なぜそう作ったか（Python 選定・Gemini 無料枠・yt-dlp） |

開発時は `make check`（整形・lint・型・テスト）🧪
