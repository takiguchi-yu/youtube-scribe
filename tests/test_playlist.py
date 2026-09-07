"""入力の正規化 — 再生リストでも単体動画でも、同じ「動画ID の並び」にする。"""

import pytest

from youtube_scribe.playlist import NotAPlaylistError, playlist_id, video_id

PLAYLIST = "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
VIDEO = "dQw4w9WgXcQ"


def test_playlist_id_from_url() -> None:
    assert playlist_id(f"https://www.youtube.com/playlist?list={PLAYLIST}") == PLAYLIST


def test_playlist_id_from_watch_url_with_list() -> None:
    url = f"https://www.youtube.com/watch?v={VIDEO}&list={PLAYLIST}"
    assert playlist_id(url) == PLAYLIST


def test_playlist_id_accepts_a_bare_id() -> None:
    assert playlist_id(PLAYLIST) == PLAYLIST


def test_playlist_id_rejects_a_url_without_list() -> None:
    with pytest.raises(NotAPlaylistError):
        playlist_id(f"https://www.youtube.com/watch?v={VIDEO}")


def test_video_id_from_watch_url() -> None:
    assert video_id(f"https://www.youtube.com/watch?v={VIDEO}") == VIDEO


def test_video_id_from_short_url() -> None:
    assert video_id(f"https://youtu.be/{VIDEO}") == VIDEO


def test_video_id_from_bare_id() -> None:
    assert video_id(VIDEO) == VIDEO


def test_video_id_is_none_for_a_playlist() -> None:
    # 動画ID は常に 11 文字。再生リストID は PL + 32 文字なので、長さで切り分けられる。
    assert video_id(f"https://www.youtube.com/playlist?list={PLAYLIST}") is None
    assert video_id(PLAYLIST) is None


def test_video_id_rejects_wrong_length() -> None:
    assert video_id("short") is None
    assert video_id("waytoolongvideoid") is None
