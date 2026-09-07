"""YouTube Data API v3 への読み取りアクセス。

`playlist` と `video` の両方がここを通る。**このモジュールだけが API の所在と
エラーの形を知っている。**

字幕はここを通らない。`captions.download` は公式ドキュメントが
"This method is requires the user to have permission to edit the video." と
述べており、**他人の動画では使えない**ためである（`transcript` を参照）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_BASE = "https://www.googleapis.com/youtube/v3/"
_TIMEOUT_SEC = 30


class YouTubeApiError(RuntimeError):
    """API が失敗した。動画 1 本を落とす理由になる。"""


def get(resource: str, params: dict[str, str], api_key: str) -> dict[str, Any]:
    """`resource` を 1 回叩いて JSON を返す。

    クォータは日次 10,000 units。`playlistItems.list` も `videos.list` も 1 unit
    なので、通常の使い方で上限に当たることはまず無い。
    """
    query = urllib.parse.urlencode({**params, "key": api_key})
    url = f"{_BASE}{resource}?{query}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SEC) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise YouTubeApiError(f"{resource} が HTTP {error.code} を返した: {detail}") from error
    except urllib.error.URLError as error:
        raise YouTubeApiError(f"{resource} に接続できなかった: {error.reason}") from error

    parsed: dict[str, Any] = json.loads(body)
    return parsed


def paginate(resource: str, params: dict[str, str], api_key: str) -> list[dict[str, Any]]:
    """`nextPageToken` を辿って items を集める。"""
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        page_params = dict(params)
        if page_token is not None:
            page_params["pageToken"] = page_token
        payload = get(resource, page_params, api_key)
        items.extend(payload.get("items", []))
        next_token = payload.get("nextPageToken")
        if not isinstance(next_token, str):
            return items
        page_token = next_token
