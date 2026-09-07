"""動画の実体へのアクセス — **yt-dlp を知っているのはこのモジュールだけである。**

`transcript` は字幕と音声を、`frame` は映像を、どちらもここ経由で受け取る。
動画 1 本につき `extract_info` は 1 回しか呼ばない。

映像・音声はダウンロードせず、**署名済みの直リンクを返す**。ffmpeg も whisper-cli も
HTTP の Range 要求でシークできるため、2 時間の動画を丸ごと落とす必要がない。
URL には有効期限があるので、受け取ったらその場で使うこと。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yt_dlp

_MAX_HEIGHT = 1080


class MediaUnavailableError(RuntimeError):
    """yt-dlp が動画を開けなかった（非公開・削除済み・地域制限など）。"""


@dataclass(frozen=True)
class Media:
    """1 本の動画について、yt-dlp が見つけた所在をまとめたもの。"""

    video_url: str | None
    audio_url: str | None
    manual_captions: dict[str, str] = field(default_factory=dict)
    auto_captions: dict[str, str] = field(default_factory=dict)


def _pick_caption_urls(tracks: object) -> dict[str, str]:
    """`{lang: [{ext, url}, ...]}` から言語ごとに vtt の URL を選ぶ。"""
    picked: dict[str, str] = {}
    if not isinstance(tracks, dict):
        return picked
    for language, candidates in tracks.items():
        if not isinstance(language, str) or not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if candidate.get("ext") == "vtt" and isinstance(candidate.get("url"), str):
                picked[language] = candidate["url"]
                break
    return picked


def _is_direct(fmt: dict[str, Any]) -> bool:
    """ffmpeg がそのままシークできる形式か。HLS/DASH マニフェストは除く。"""
    return isinstance(fmt.get("url"), str) and fmt.get("protocol") in ("https", "http")


def _pick_video_url(formats: list[dict[str, Any]]) -> str | None:
    """フレーム抽出に使う映像を選ぶ。

    **音声は要らない。** 映像のみのストリームのほうが軽く、シークも速い。
    スライドやコード画面が読める必要があるので、1080p までで最も高いものを取る。
    """
    usable = [
        fmt
        for fmt in formats
        if _is_direct(fmt)
        and fmt.get("vcodec") not in (None, "none")
        and isinstance(fmt.get("height"), int)
        and fmt["height"] <= _MAX_HEIGHT
    ]
    if not usable:
        return None
    # mp4 を優先するのは、ffmpeg のシークが最も素直に効くため。
    best = max(usable, key=lambda f: (f["height"], f.get("ext") == "mp4", f.get("tbr") or 0))
    url: str = best["url"]
    return url


def _pick_audio_url(formats: list[dict[str, Any]]) -> str | None:
    """文字起こしに使う音声のみのストリームを選ぶ。

    yt-dlp の `-f ba` に相当する。**変換しないので ffmpeg を経由しない。**
    """
    usable = [
        fmt
        for fmt in formats
        if _is_direct(fmt)
        and fmt.get("acodec") not in (None, "none")
        and fmt.get("vcodec") in (None, "none")
    ]
    if not usable:
        return None
    best = max(usable, key=lambda f: f.get("abr") or f.get("tbr") or 0)
    url: str = best["url"]
    return url


def probe(video_url: str) -> Media:
    """1 本の動画について、字幕・音声・映像の所在を調べる。

    公式ドキュメントが "we do not guarantee the return value of
    `YoutubeDL.extract_info` to be json serializable, or even be a dictionary" と
    述べているため、受け取った値は必ず型を確かめてから使う。
    """
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.sanitize_info(downloader.extract_info(video_url, download=False))
    except Exception as error:  # yt_dlp は独自の例外階層を持つ。ここでは一括で失敗にする。
        raise MediaUnavailableError(str(error)) from error

    if not isinstance(info, dict):
        raise MediaUnavailableError("yt-dlp が辞書を返さなかった")

    raw_formats = info.get("formats")
    formats = (
        [fmt for fmt in raw_formats if isinstance(fmt, dict)]
        if isinstance(raw_formats, list)
        else []
    )

    return Media(
        video_url=_pick_video_url(formats),
        audio_url=_pick_audio_url(formats),
        manual_captions=_pick_caption_urls(info.get("subtitles")),
        auto_captions=_pick_caption_urls(info.get("automatic_captions")),
    )
