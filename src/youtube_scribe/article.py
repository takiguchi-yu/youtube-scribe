"""記事 — 受け取ったデータを Markdown の文字列にし、その front matter を読み書きする。

**このモジュールは他のどのモジュールも呼ばない。** 保存先も、要約の作り方も、
字幕の取り方も知らない。だから記事の形式を変えたいときは、ここだけを触ればよい。
front matter を書くのも読むのもここなので、キーの名前が食い違うことがない。
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Mapping, Sequence
from typing import Protocol

from youtube_scribe.frame import Frame
from youtube_scribe.transcript import Transcript
from youtube_scribe.video import Video

_FENCE = "---"

# 既読の印。**読んだ日だけを書く。** 所感欄はツールが触らない場所なので使わない。
READ_AT = "read_at"


# Protocol の可変属性は不変（invariant）に扱われるため、`list[Section]` は
# `Sequence[SectionLike]` と一致しない。**読み取り専用のプロパティで宣言する。**
# `render` は受け取ったものを読むだけなので、これで過不足がない。
class SectionLike(Protocol):
    @property
    def heading(self) -> str: ...
    @property
    def body(self) -> str: ...
    @property
    def evidence_sec(self) -> int | None: ...


class TermLike(Protocol):
    @property
    def word(self) -> str: ...
    @property
    def original(self) -> str: ...
    @property
    def meaning(self) -> str: ...


class SummaryLike(Protocol):
    """`render` が要約に求めるかたち。

    実体は `summary.Summary` だが、`article` が `summary` を import すると
    Gemini SDK まで引き込むことになる。**記事の組み立てに API は要らない。**
    """

    @property
    def points(self) -> Sequence[str]: ...
    @property
    def sections(self) -> Sequence[SectionLike]: ...
    @property
    def terms(self) -> Sequence[TermLike]: ...


def format_timestamp(second: int) -> str:
    """秒を `1:23` / `1:02:03` にする。"""
    hours, rest = divmod(max(second, 0), 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _yaml_scalar(value: str) -> str:
    """front matter に安全に埋め込める文字列にする。

    JSON の文字列リテラルは YAML のダブルクォート文字列としてそのまま通る。
    """
    return json.dumps(value, ensure_ascii=False)


def render(
    video: Video,
    transcript: Transcript,
    summary: SummaryLike,
    frames: Mapping[int, Frame],
    *,
    generated_at: str,
    model: str,
    asset_prefix: str,
) -> str:
    """記事 1 本ぶんの Markdown を組み立てる。

    `frames` は「要約が指定した秒 → 切り出せた画像」。**撮れなかった時刻は
    単に入っていない** ので、本文はその節の画像を省いて続く。
    """
    lines: list[str] = [
        "---",
        f"video_id: {video.video_id}",
        f"title: {_yaml_scalar(video.title)}",
        f"url: {video.url}",
        f"published_at: {video.published_at}",
        f"generated_at: {generated_at}",
        f"source: {transcript.source.label}",
        f"language: {_yaml_scalar(transcript.language)}",
        f"model: {model}",
        "---",
        "",
        f"# {video.title}",
        "",
        f"[{video.url}]({video.url})",
        "",
        "## 要点",
        "",
    ]
    lines.extend(f"- {point}" for point in summary.points)

    if summary.sections:
        lines += ["", "## 詳細"]
        for section in summary.sections:
            lines += ["", f"### {section.heading}", "", section.body]
            frame = frames.get(section.evidence_sec) if section.evidence_sec is not None else None
            if frame is not None:
                stamp = format_timestamp(frame.at_sec)
                lines += [
                    "",
                    f"![{section.heading}]({asset_prefix}/{frame.path.name})",
                    "",
                    f"*{stamp} — [動画のこの位置を開く]({video.url_at(frame.at_sec)})*",
                ]
            elif section.evidence_sec is not None:
                stamp = format_timestamp(section.evidence_sec)
                link = video.url_at(section.evidence_sec)
                lines += ["", f"*{stamp} — [動画のこの位置を開く]({link})*"]

    if summary.terms:
        lines += ["", "## 用語", ""]
        for term in summary.terms:
            original = f"（{term.original}）" if term.original.strip() else ""
            lines.append(f"- **{term.word}**{original} — {term.meaning}")

    # 所感欄は**空のまま置く**。ここを埋めるのは人間の仕事である。
    lines += ["", "## 所感", "", "<!-- ここは自分で書く -->", ""]
    return "\n".join(lines)


def _front_matter_end(lines: Sequence[str]) -> int:
    """front matter を閉じる `---` の行番号。無ければ `ValueError`。"""
    if not lines or lines[0] != _FENCE:
        raise ValueError("front matter が無い")
    for number, line in enumerate(lines[1:], start=1):
        if line == _FENCE:
            return number
    raise ValueError("front matter が閉じていない")


def read_front_matter(markdown: str) -> dict[str, str]:
    """`render` が書いた front matter を読む。

    **YAML 全般は読まない。** `render` が書く形（`key: 値` と、JSON で引用した文字列）
    だけを受け付ける。読む側と書く側が同じモジュールにあるので、それで足りる。
    """
    lines = markdown.split("\n")
    fields: dict[str, str] = {}
    for line in lines[1 : _front_matter_end(lines)]:
        key, separator, value = line.partition(": ")
        if not separator:
            continue
        fields[key] = json.loads(value) if value.startswith('"') else value
    return fields


def mark_read(markdown: str, day: datetime.date) -> str:
    """front matter に読んだ日を書き足す。**本文には一切触れない。**

    既に読んだ日があれば書き換える。
    """
    lines = markdown.split("\n")
    end = _front_matter_end(lines)
    stamp = f"{READ_AT}: {day.isoformat()}"
    kept = [line for line in lines[1:end] if not line.startswith(f"{READ_AT}: ")]
    return "\n".join([lines[0], *kept, stamp, *lines[end:]])
