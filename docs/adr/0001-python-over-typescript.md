# Python を使う（private 配下の Node 慣習から外れる）

このリポジトリは Python + uv で書く。**`private` 配下 11 リポジトリのうち 8 つが
Node/TypeScript なので、これは意図的な逸脱である。**

決め手は **yt-dlp 1 つだけ**である。ffmpeg と whisper.cpp は結局どちらの言語からも
外部プロセス起動になるので、言語選択の理由にはならない。**この ADR を読んで
「whisper のために Python にしたのだろう」と読み違えないこと。**

## 決め手

**yt-dlp は Python の埋め込みだけを公式にサポートしている。**
README の "EMBEDDING YT-DLP" が、CLI 呼び出しについては
"Your program should **avoid parsing the normal stdout** since they may change in
future versions" と警告したうえで、
"**From a Python program, you can embed yt-dlp in a more powerful fashion**" と
Python だけを名指ししている。`YoutubeDL(opts).extract_info(url, download=False)` で
オプションを dict のまま渡せる。他言語は CLI 起動 + `-J` / `--print` のパースになる。

**Node には、字幕を取る生きた選択肢が無い。** 調査時点（2026-09-06）で
`yt-dlp-wrap` は npm 上で deprecated 明示（最終公開 2023-09-13）、
`youtube-transcript` は「本番環境で動かない」系の issue が複数年 open のまま、
`fluent-ffmpeg` は deprecated かつ GitHub archived で README 自身が
"no longer works properly with recent ffmpeg versions" と書いている。

## 引き換えに捨てたもの

- `ai-report` の構成（ESM・Biome・`node:test`・`.ts` 直実行）をそのまま流用できない
- リポジトリ間で書き味が揃わない
- この Mac の `python3` は Xcode CLT 同梱の 3.9 で、`google-genai` の要求（3.10 以上）を
  満たさない。uv による Python 本体の導入が前提になる

## 採用しなかった案

**TypeScript + Node** — 既存 8 リポジトリと揃うが、字幕取得が
「メンテされていないラッパを使う」か「公式が非推奨とする stdout パースを書く」の
二択になる。`-J` で JSON を受ければ後者は緩和できるが、yt-dlp のオプションを
dict のまま渡せる Python との差は残る。

**Go** — `cording-pilot` / `trend-sight` と揃い単一バイナリになるが、
Node を選ぶ場合と同じ欠点をそのまま引き継ぐ。
