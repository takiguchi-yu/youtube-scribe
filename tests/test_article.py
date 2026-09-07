"""記事の組み立て。**ゴールデンで記事全体の形を固定する。**

ゴールデンを更新するときは `GOLDEN_UPDATE=1 uv run pytest tests/test_article.py`。
差分を必ず目で見てからコミットすること。
"""

import os
from dataclasses import dataclass
from pathlib import Path

from youtube_scribe.article import format_timestamp, render
from youtube_scribe.frame import Frame
from youtube_scribe.transcript import Cue, Source, Transcript
from youtube_scribe.video import Chapter, Video

GOLDEN = Path(__file__).parent / "golden" / "article.md"


@dataclass
class FakeSection:
    heading: str
    body: str
    evidence_sec: int | None


@dataclass
class FakeTerm:
    word: str
    original: str
    meaning: str


@dataclass
class FakeSummary:
    points: list[str]
    sections: list[FakeSection]
    terms: list[FakeTerm]


def sample_video() -> Video:
    return Video(
        video_id="dQw4w9WgXcQ",
        title='Rust の "所有権" 入門',
        description="0:00 イントロ\n1:00 本題",
        published_at="2026-01-15T09:00:00Z",
        duration_sec=1800,
        chapters=(Chapter(0, "イントロ"), Chapter(60, "本題")),
    )


def sample_transcript() -> Transcript:
    return Transcript(
        source=Source.MANUAL_CAPTION,
        language="ja",
        cues=(Cue(0.0, "はじめます"), Cue(60.0, "本題です")),
    )


def sample_summary() -> FakeSummary:
    return FakeSummary(
        points=["所有権はコンパイル時に検査される", "借用は所有権を移さない"],
        sections=[
            FakeSection("所有権とは", "値の持ち主が 1 つに定まる仕組み。", 60),
            FakeSection("借用", "参照を渡しても持ち主は変わらない。", 900),
            FakeSection("まとめ", "手で管理しなくてよくなる。", None),
        ],
        terms=[
            FakeTerm("所有権", "ownership", "値の持ち主を 1 つに定める規則。"),
            FakeTerm("借用", "borrowing", "所有権を移さずに参照する操作。"),
        ],
    )


def render_sample() -> str:
    # 900 秒の節は画像が撮れなかった、という状況にしている。
    frames = {60: Frame(requested_sec=60, at_sec=63, path=Path("assets/dQw4w9WgXcQ/63.jpg"))}
    return render(
        sample_video(),
        sample_transcript(),
        sample_summary(),
        frames,
        generated_at="2026-09-06T12:00:00+00:00",
        model="gemini-3.8-flash",
        asset_prefix="assets/dQw4w9WgXcQ",
    )


def test_format_timestamp() -> None:
    assert format_timestamp(0) == "0:00"
    assert format_timestamp(63) == "1:03"
    assert format_timestamp(3723) == "1:02:03"
    assert format_timestamp(-5) == "0:00"


def test_article_matches_golden() -> None:
    produced = render_sample()
    if os.environ.get("GOLDEN_UPDATE"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(produced, encoding="utf-8")
    assert produced == GOLDEN.read_text(encoding="utf-8")


def test_front_matter_quotes_titles_containing_quotes() -> None:
    # タイトルに " が入っても front matter が壊れないこと。
    assert 'title: "Rust の \\"所有権\\" 入門"' in render_sample()


def test_section_without_a_frame_still_links_to_the_video() -> None:
    produced = render_sample()
    # 画像が撮れなかった 900 秒の節も、時刻リンクだけは残る。
    assert "&t=900s" in produced
    assert "assets/dQw4w9WgXcQ/63.jpg" in produced


def test_section_without_evidence_has_no_timestamp_line() -> None:
    produced = render_sample()
    tail = produced.split("### まとめ", 1)[1].split("## 用語", 1)[0]
    assert "動画のこの位置を開く" not in tail


def test_reflection_section_is_left_empty() -> None:
    # 所感欄を埋めるのは人間の仕事。ツールは書かない。
    produced = render_sample()
    assert produced.rstrip().endswith("<!-- ここは自分で書く -->")


def test_empty_summary_still_renders() -> None:
    produced = render(
        sample_video(),
        sample_transcript(),
        FakeSummary(points=[], sections=[], terms=[]),
        {},
        generated_at="2026-09-06T12:00:00+00:00",
        model="gemini-3.8-flash",
        asset_prefix="assets/dQw4w9WgXcQ",
    )
    assert "## 要点" in produced
    assert "## 詳細" not in produced
    assert "## 用語" not in produced
    assert "## 所感" in produced
