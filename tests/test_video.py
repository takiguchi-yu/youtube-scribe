"""`videos.list` の生の値を、扱える形にする部分。"""

from youtube_scribe.video import Chapter, Video, parse_chapters, parse_duration


def test_parse_duration_reads_iso8601() -> None:
    assert parse_duration("PT15M33S") == 933
    assert parse_duration("PT1H2M3S") == 3723
    assert parse_duration("PT2H") == 7200
    assert parse_duration("PT45S") == 45
    assert parse_duration("P1DT1H") == 90000


def test_parse_duration_returns_zero_for_garbage() -> None:
    # 長さは記事の必須情報ではないので、ここで動画 1 本を落とさない。
    assert parse_duration("") == 0
    assert parse_duration("15:33") == 0
    assert parse_duration("PT") == 0


def test_parse_chapters_reads_description_timestamps() -> None:
    description = "\n".join(
        [
            "この動画の内容です。",
            "0:00 イントロ",
            "1:30 本題",
            "1:02:03 まとめ",
        ]
    )
    assert parse_chapters(description) == (
        Chapter(0, "イントロ"),
        Chapter(90, "本題"),
        Chapter(3723, "まとめ"),
    )


def test_parse_chapters_accepts_brackets_and_dashes() -> None:
    description = "(0:00) はじめに\n[2:00] - 次の話"
    assert parse_chapters(description) == (Chapter(0, "はじめに"), Chapter(120, "次の話"))


def test_parse_chapters_requires_a_zero_start() -> None:
    # 本文中にたまたま出てくる時刻表記を、チャプターと誤認しないため。
    description = "詳しくは 3:45 あたりで話しています。\n10:00 まとめ"
    assert parse_chapters(description) == ()


def test_parse_chapters_needs_more_than_one_entry() -> None:
    assert parse_chapters("0:00 全部") == ()


def test_parse_chapters_ignores_empty_titles() -> None:
    assert parse_chapters("0:00\n1:00\n") == ()


def test_video_urls() -> None:
    video = Video(
        video_id="abcdefghijk",
        title="題",
        description="",
        published_at="2026-01-01T00:00:00Z",
        duration_sec=100,
        chapters=(),
    )
    assert video.url == "https://www.youtube.com/watch?v=abcdefghijk"
    assert video.url_at(42) == "https://www.youtube.com/watch?v=abcdefghijk&t=42s"
