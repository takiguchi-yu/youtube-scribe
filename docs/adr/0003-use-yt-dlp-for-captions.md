# 字幕・音声・映像の取得に yt-dlp を使う

**公式 API では動画の中身に触れないため、yt-dlp を使う。** 個人の非公開利用に限る前提で、
規約上のリスクを承知したうえで選んだ。

## なぜ公式 API では足りないか

`captions.download` は公式ドキュメントが
**"This method is requires the user to have permission to edit the video."**（原文ママ）
と述べており、**他人の動画の字幕は取得できない。** OAuth を通しても同じで、動画の
編集権限そのものが要る。

字幕が取れなければ、このツールの目的（動画の中身を要約する）は成り立たない。
`playlistItems.list` と `videos.list` は API キーで叩けるが、返るのは
タイトル・説明欄・長さといったメタデータだけである。

## 承知したこと

字幕を取る現実的な手段（yt-dlp、youtubei.js、youtube-transcript）は、いずれも
`youtube.com/api/timedtext` か `/youtubei/` を叩く。**robots.txt はその両方を
`Disallow` にしている。**

- 利用規約（Dated: December 15, 2023）は、public search engine が robots.txt に従う
  場合を除き **"access the Service using any automated means (such as robots, botnets
  or scrapers)"** を禁じている
- Developer Policies（Last updated 2026-06-24）の Scraping 節は、スクレイピングだけで
  なく **"obtain scraped YouTube data or content"**（スクレイプ済みデータを取得すること）
  も禁じている
- 音声・映像のダウンロードも **"access, reproduce, download... any part of the Service
  or any Content"** の禁止条項に当たる

**違法という話ではなく、YouTube との規約上の問題である。** 生成物を公開せず、
個人の学習メモに留める前提で使っている。

## 採用しなかった案

**公式 API の範囲だけで作る** — 規約は完全にクリアだが、渡せるのがタイトルと説明欄
だけになり、動画の中身を要約できない。画面キャプチャも撮れない。

**自分がアップロードした動画だけを対象にする** — `captions.download` が使える唯一の
正規ルート。ただし他人の再生リストを読むという用途そのものが消える。

**字幕ファイルを人が用意して渡す** — ツールは規約に触れないが、自動化の価値が
ほとんど無くなる。
