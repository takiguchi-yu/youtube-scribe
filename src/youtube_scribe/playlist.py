"""再生リスト — **処理する動画を集めるためだけのもの。**

再生リストそのものについての記事は作らない。ここが返すのは動画ID の並びだけである。
"""

from __future__ import annotations

import re
import urllib.parse

from youtube_scribe.youtube_api import paginate

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{2,}$")


class NotAPlaylistError(ValueError):
    """渡された URL から再生リストID を取り出せなかった。"""


def playlist_id(url: str) -> str:
    """再生リストの URL から `list=` の値を取り出す。ID をそのまま渡してもよい。"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in ("http", "https"):
        found = urllib.parse.parse_qs(parsed.query).get("list", [])
        if found and _PLAYLIST_ID.fullmatch(found[0]):
            return found[0]
        raise NotAPlaylistError(f"再生リストID が URL に含まれていない: {url}")

    if _PLAYLIST_ID.fullmatch(url):
        return url
    raise NotAPlaylistError(f"再生リストID として解釈できない: {url}")


def video_id(url: str) -> str | None:
    """単体動画の URL なら動画ID を返す。そうでなければ None。

    URL ではなく裸の ID を渡された場合は、**長さ 11 なら動画ID とみなす。**
    動画ID は常に 11 文字であるのに対し、再生リストID は `PL` + 32 文字などで
    11 文字にはならないため、この規則で両者を切り分けられる。
    どちらとも取れる文字列を渡すときは、URL の形にすれば曖昧さが消える。
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url if _VIDEO_ID.fullmatch(url) else None

    if parsed.netloc.endswith("youtu.be"):
        candidate = parsed.path.lstrip("/")
        return candidate if _VIDEO_ID.fullmatch(candidate) else None

    found = urllib.parse.parse_qs(parsed.query).get("v", [])
    if found and _VIDEO_ID.fullmatch(found[0]):
        return found[0]
    return None


def video_ids(playlist: str, api_key: str) -> list[str]:
    """`playlistItems.list` で動画ID を順番どおりに集める（1 ページ 1 unit）。

    **再生リストの並び順を保つ。** 学習メモを順に読み返すときに効く。
    非公開・削除済みの項目は動画ID が取れないので落とす。
    """
    items = paginate(
        "playlistItems",
        {"part": "contentDetails", "playlistId": playlist, "maxResults": "50"},
        api_key,
    )
    found: list[str] = []
    seen: set[str] = set()
    for item in items:
        candidate = item.get("contentDetails", {}).get("videoId")
        if isinstance(candidate, str) and _VIDEO_ID.fullmatch(candidate) and candidate not in seen:
            seen.add(candidate)
            found.append(candidate)
    return found
