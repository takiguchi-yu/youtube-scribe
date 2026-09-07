"""動画そのもの — 動画ID とメタデータ。

**記事 1 本に対応する単位はここである。** 再生リストではない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from youtube_scribe.youtube_api import get

_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$"
)

# 行頭のタイムスタンプ。`0:00 イントロ` / `00:01:23 まとめ` / `(1:23) 本題` を拾う。
_CHAPTER = re.compile(
    r"^\s*[\[(]?\s*(?P<h>\d{1,2}:)?(?P<m>\d{1,2}):(?P<s>\d{2})\s*[\])]?\s*[-–—:]?\s*(?P<title>.+?)\s*$"
)


@dataclass(frozen=True)
class Chapter:
    """投稿者が説明欄にタイムスタンプの形で書いた見出し。"""

    start_sec: int
    title: str


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    description: str
    published_at: str
    duration_sec: int
    chapters: tuple[Chapter, ...]

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    def url_at(self, second: int) -> str:
        """その時刻から再生が始まる URL。記事のキャプションに添える。"""
        return f"{self.url}&t={second}s"


def parse_duration(value: str) -> int:
    """ISO 8601 の duration を秒にする。`videos.list` の `contentDetails.duration` 用。

    解釈できないものは 0 を返す。**長さは記事の必須情報ではない**ので、
    ここで例外を投げて動画 1 本を落とす価値がない。
    """
    matched = _DURATION.fullmatch(value.strip())
    if matched is None:
        return 0
    parts = {key: int(raw) for key, raw in matched.groupdict(default="0").items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def parse_chapters(description: str) -> tuple[Chapter, ...]:
    """説明欄からチャプターを取り出す。

    **YouTube Data API にチャプター専用のフィールドは無い。** 公式ドキュメントが
    "You can create video chapters by including formatted timestamps ... in the
    description text" と述べているとおり、説明欄のタイムスタンプが実体である。

    YouTube 側の規約に合わせ、**先頭が 0 秒で始まらないものはチャプターとみなさない。**
    そうしないと、本文中にたまたま出てくる時刻表記を拾ってしまう。
    """
    found: list[Chapter] = []
    for line in description.splitlines():
        matched = _CHAPTER.match(line)
        if matched is None:
            continue
        hours = int((matched.group("h") or "0:")[:-1])
        seconds = hours * 3600 + int(matched.group("m")) * 60 + int(matched.group("s"))
        title = matched.group("title").strip()
        if title:
            found.append(Chapter(start_sec=seconds, title=title))

    if len(found) < 2 or found[0].start_sec != 0:
        return ()
    return tuple(found)


def fetch(video_ids: list[str], api_key: str) -> dict[str, Video]:
    """`videos.list` で動画のメタデータを引く（1 呼び出しにつき 1 unit）。

    1 リクエストに何件まで渡せるかは公式リファレンスに明記が無いため、
    広く使われている 50 件で刻む。
    """
    result: dict[str, Video] = {}
    for start in range(0, len(video_ids), 50):
        chunk = video_ids[start : start + 50]
        payload = get(
            "videos",
            {"part": "snippet,contentDetails", "id": ",".join(chunk)},
            api_key,
        )
        for item in payload.get("items", []):
            snippet = item.get("snippet", {})
            description = str(snippet.get("description", ""))
            video = Video(
                video_id=str(item["id"]),
                title=str(snippet.get("title", "")),
                description=description,
                published_at=str(snippet.get("publishedAt", "")),
                duration_sec=parse_duration(
                    str(item.get("contentDetails", {}).get("duration", ""))
                ),
                chapters=parse_chapters(description),
            )
            result[video.video_id] = video
    return result
