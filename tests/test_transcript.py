"""書き起こし — **3 つの入力手段を 1 つの形に潰す**ところ。ここが崩れると全部崩れる。"""

import urllib.error
from pathlib import Path

import pytest

from youtube_scribe import transcript as transcript_module
from youtube_scribe.media import Media
from youtube_scribe.transcript import (
    Cue,
    Source,
    Transcript,
    parse_vtt,
    parse_whisper_json,
)
from youtube_scribe.transcript import _preferred_caption as preferred_caption

MANUAL_VTT = """WEBVTT

00:00:01.000 --> 00:00:04.000
最初の発話です。

00:01:05.500 --> 00:01:08.000
次の発話です。

01:00:00.000 --> 01:00:02.000
1 時間後の発話。
"""

# YouTube の自動生成字幕。インラインのタイミングタグが混ざり、
# 直前の行を繰り返しながら 1 行ずつ流れていく。
AUTO_VTT = """WEBVTT
Kind: captions
Language: ja

00:00:00.000 --> 00:00:02.000 align:start position:0%
こんにちは

00:00:02.000 --> 00:00:04.000 align:start position:0%
こんにちは
<00:00:02.500><c>今日は</c> <00:00:03.000><c>いい天気</c>

00:00:04.000 --> 00:00:06.000 align:start position:0%
今日は いい天気
散歩に行きます
"""


def test_parse_vtt_reads_timestamps_and_text() -> None:
    assert parse_vtt(MANUAL_VTT) == (
        Cue(1.0, "最初の発話です。"),
        Cue(65.5, "次の発話です。"),
        Cue(3600.0, "1 時間後の発話。"),
    )


def test_parse_vtt_strips_inline_tags_and_rolling_repeats() -> None:
    cues = parse_vtt(AUTO_VTT)
    texts = [cue.text for cue in cues]
    # 「こんにちは」は 2 つの Cue にまたがるが、1 回しか出てはいけない。
    assert texts.count("こんにちは") == 1
    # タイミングタグが本文に残ってはいけない。
    assert all("<" not in text for text in texts)
    assert texts == ["こんにちは", "今日は いい天気", "散歩に行きます"]


def test_parse_vtt_handles_comma_decimals() -> None:
    body = "WEBVTT\n\n00:00:02,250 --> 00:00:03,000\nテキスト\n"
    assert parse_vtt(body) == (Cue(2.25, "テキスト"),)


def test_parse_vtt_on_empty_input() -> None:
    assert parse_vtt("WEBVTT\n\n") == ()


def test_parse_whisper_json_reads_offsets_as_milliseconds() -> None:
    payload = {
        "result": {"language": "ja"},
        "transcription": [
            {"offsets": {"from": 0, "to": 2000}, "text": " はじめに"},
            {"offsets": {"from": 65500, "to": 68000}, "text": " つづき"},
        ],
    }
    language, cues = parse_whisper_json(payload)
    assert language == "ja"
    assert cues == (Cue(0.0, "はじめに"), Cue(65.5, "つづき"))


def test_parse_whisper_json_skips_malformed_segments() -> None:
    payload = {
        "result": {"language": "en"},
        "transcription": [
            {"offsets": {"from": 0}, "text": "  "},
            "壊れた要素",
            {"offsets": {}, "text": "時刻が無い"},
            {"offsets": {"from": 1000}, "text": "生きている"},
        ],
    }
    language, cues = parse_whisper_json(payload)
    assert language == "en"
    assert cues == (Cue(1.0, "生きている"),)


def test_parse_whisper_json_without_transcription() -> None:
    assert parse_whisper_json({"result": {"language": "ja"}}) == ("ja", ())


def test_source_labels_are_japanese() -> None:
    assert Source.MANUAL_CAPTION.label == "人手字幕"
    assert Source.AUTO_CAPTION.label == "自動字幕"
    assert Source.SPEECH_TO_TEXT.label == "文字起こし"


def test_as_prompt_text_puts_seconds_first() -> None:
    # 画面キャプチャの時刻はこの形式から生まれる。
    transcript = Transcript(
        source=Source.MANUAL_CAPTION,
        language="ja",
        cues=(Cue(0.0, "あ"), Cue(65.9, "い")),
    )
    assert transcript.as_prompt_text() == "[0] あ\n[65] い"


def _media(manual: dict[str, str] | None = None, auto: dict[str, str] | None = None) -> Media:
    return Media(
        video_url=None, audio_url=None, manual_captions=manual or {}, auto_captions=auto or {}
    )


def test_manual_caption_beats_auto_caption_even_across_languages() -> None:
    # 英語の人手字幕は、日本語の自動字幕より要約の材料として良い。
    chosen = preferred_caption(
        _media(manual={"en": "manual-en"}, auto={"ja": "auto-ja"}), ["ja", "en"]
    )
    assert chosen == (Source.MANUAL_CAPTION, "en", "manual-en")


def test_language_order_is_respected_within_the_same_source() -> None:
    chosen = preferred_caption(_media(manual={"en": "en", "ja": "ja"}), ["ja", "en"])
    assert chosen == (Source.MANUAL_CAPTION, "ja", "ja")


def test_regional_variants_match_the_base_language() -> None:
    chosen = preferred_caption(_media(auto={"ja-JP": "auto"}), ["ja"])
    assert chosen == (Source.AUTO_CAPTION, "ja-JP", "auto")


def test_no_caption_at_all() -> None:
    assert preferred_caption(_media(), ["ja", "en"]) is None
    assert preferred_caption(_media(manual={"fr": "fr"}), ["ja", "en"]) is None


def test_fetch_never_touches_the_model_when_captions_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """字幕が取れる限り、2.9GiB のモデルには一切触れてはいけない。"""

    def explode(*args: object, **kwargs: object) -> Path:
        raise AssertionError("字幕があるのにモデルを取得しようとした")

    monkeypatch.setattr(transcript_module, "_download", lambda url: MANUAL_VTT)
    monkeypatch.setattr(transcript_module, "ensure_model", explode)

    result = transcript_module.fetch(
        _media(manual={"ja": "https://example.invalid/sub.vtt"}),
        languages=["ja"],
        whisper_model=Path("/does/not/exist.bin"),
        whisper_language="ja",
        report=lambda _: None,
    )
    assert result.source is Source.MANUAL_CAPTION
    assert result.cues[0].text == "最初の発話です。"


def test_fetch_falls_back_when_the_caption_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # 字幕トラックはあるが中身が空、という動画は実在する。文字起こしへ進むこと。
    monkeypatch.setattr(transcript_module, "_download", lambda url: "WEBVTT\n\n")
    monkeypatch.setattr(transcript_module, "_whisper_binary", lambda: "/bin/true")
    monkeypatch.setattr(transcript_module, "ensure_model", lambda path, report: path)
    monkeypatch.setattr(
        transcript_module,
        "_run_whisper",
        lambda binary, url, model, language: ("ja", (Cue(0.0, "起こした"),)),
    )

    media = Media(
        video_url=None,
        audio_url="https://example.invalid/a.m4a",
        manual_captions={"ja": "https://example.invalid/sub.vtt"},
    )
    result = transcript_module.fetch(
        media,
        languages=["ja"],
        whisper_model=Path("/model.bin"),
        whisper_language="ja",
        report=lambda _: None,
    )
    assert result.source is Source.SPEECH_TO_TEXT
    assert result.cues == (Cue(0.0, "起こした"),)


def test_fetch_without_captions_and_without_transcription() -> None:
    with pytest.raises(transcript_module.TranscriptUnavailableError, match="無効"):
        transcript_module.fetch(
            _media(),
            languages=["ja"],
            whisper_model=None,
            whisper_language="ja",
            report=lambda _: None,
        )


def test_fetch_without_captions_and_without_audio() -> None:
    with pytest.raises(transcript_module.TranscriptUnavailableError, match="音声ストリーム"):
        transcript_module.fetch(
            _media(),
            languages=["ja"],
            whisper_model=Path("/model.bin"),
            whisper_language="ja",
            report=lambda _: None,
        )


def test_original_auto_caption_beats_a_machine_translation() -> None:
    """英語の動画で、機械翻訳された ja を選んではいけない。

    YouTube は自動字幕を 157 言語へ機械翻訳して並べる。言語だけで選ぶと
    「機械文字起こし → 機械翻訳」の二重劣化を踏む。
    """
    chosen = preferred_caption(
        _media(auto={"ja": "translated-ja", "en": "translated-en", "en-orig": "original-en"}),
        ["ja", "en"],
    )
    assert chosen == (Source.AUTO_CAPTION, "en-orig", "original-en")


def test_manual_caption_still_beats_the_original_auto_caption() -> None:
    # 人が付けた字幕は、原語の自動字幕よりさらに質が高い。
    chosen = preferred_caption(
        _media(manual={"en": "manual-en"}, auto={"ja-orig": "auto-ja"}),
        ["ja", "en"],
    )
    assert chosen == (Source.MANUAL_CAPTION, "en", "manual-en")


def test_falls_back_to_a_translation_when_no_original_is_marked() -> None:
    # `-orig` が無い動画もある。そのときは従来どおり言語の優先順で選ぶ。
    chosen = preferred_caption(_media(auto={"en": "en", "ja": "ja"}), ["ja", "en"])
    assert chosen == (Source.AUTO_CAPTION, "ja", "ja")


def test_original_auto_caption_is_used_even_for_an_unwanted_language() -> None:
    # 原語がドイツ語でも、機械翻訳の ja より原語を渡す。日本語化は要約側の仕事。
    chosen = preferred_caption(_media(auto={"ja": "translated", "de-orig": "original"}), ["ja"])
    assert chosen == (Source.AUTO_CAPTION, "de-orig", "original")


def test_language_order_decides_among_multiple_original_tracks() -> None:
    """多言語音声の動画は `-orig` が複数ある。実データで見つかった形。

    `en-US-orig` と `ja-orig` の両方を持つ動画があり、辞書順で選ぶと英語に倒れた。
    原語が複数あるなら、そこは利用者の言語優先順で決める。
    """
    chosen = preferred_caption(
        _media(auto={"en-US-orig": "english", "ja-orig": "japanese", "ja": "translated"}),
        ["ja", "en"],
    )
    assert chosen == (Source.AUTO_CAPTION, "ja-orig", "japanese")


def test_regional_original_tracks_match_the_base_language() -> None:
    # `en-US-orig` は "en" の指定で拾えないといけない。
    chosen = preferred_caption(_media(auto={"en-US-orig": "english", "ja": "translated"}), ["en"])
    assert chosen == (Source.AUTO_CAPTION, "en-US-orig", "english")


def test_a_transient_youtube_error_does_not_trigger_transcription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YouTube が字幕を絞った（429）だけで 2.9GiB を落としてはいけない。

    実際に起きた事故。一時的な失敗なので、記事を作らず終われば次回そのまま再試行される。
    """

    def throttled(url: str) -> str:
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    def explode(*args: object, **kwargs: object) -> Path:
        raise AssertionError("一時的な失敗でモデルを取得しようとした")

    monkeypatch.setattr(transcript_module, "_download", throttled)
    monkeypatch.setattr(transcript_module, "ensure_model", explode)

    media = Media(
        video_url=None,
        audio_url="https://example.invalid/a.m4a",
        auto_captions={"ja-orig": "https://example.invalid/sub.vtt"},
    )
    with pytest.raises(transcript_module.TranscriptUnavailableError, match="HTTP 429"):
        transcript_module.fetch(
            media,
            languages=["ja"],
            whisper_model=Path("/model.bin"),
            whisper_language="ja",
            report=lambda _: None,
        )


def test_the_caption_failure_reason_survives_into_the_final_message() -> None:
    """「whisper-cli が無い」だけを見せて、本当の理由を捨てない。"""
    media = Media(video_url=None, audio_url=None, auto_captions={})
    with pytest.raises(transcript_module.TranscriptUnavailableError, match="字幕が無い"):
        transcript_module.fetch(
            media,
            languages=["ja"],
            whisper_model=Path("/model.bin"),
            whisper_language="ja",
            report=lambda _: None,
        )


def test_a_permanent_caption_error_still_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # 404 のような恒久的な失敗なら、文字起こしに落ちてよい。
    def missing(url: str) -> str:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(transcript_module, "_download", missing)
    monkeypatch.setattr(transcript_module, "_whisper_binary", lambda: "/bin/true")
    monkeypatch.setattr(transcript_module, "ensure_model", lambda path, report: path)
    monkeypatch.setattr(
        transcript_module,
        "_run_whisper",
        lambda binary, url, model, language: ("ja", (Cue(0.0, "起こした"),)),
    )

    media = Media(
        video_url=None,
        audio_url="https://example.invalid/a.m4a",
        auto_captions={"ja-orig": "https://example.invalid/sub.vtt"},
    )
    result = transcript_module.fetch(
        media,
        languages=["ja"],
        whisper_model=Path("/model.bin"),
        whisper_language="ja",
        report=lambda _: None,
    )
    assert result.source is Source.SPEECH_TO_TEXT


def test_throttling_is_a_stop_the_run_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """YouTube に絞られたら、残りの動画を試さずに全体を止めるための型を投げる。

    1 本目で分かったのに 3 本とも叩いて絞りを深めた、という事故があった。
    """

    def throttled(url: str) -> str:
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(transcript_module, "_download", throttled)
    media = Media(
        video_url=None,
        audio_url=None,
        auto_captions={"ja-orig": "https://example.invalid/sub.vtt"},
    )
    with pytest.raises(transcript_module.ThrottledError):
        transcript_module.fetch(
            media,
            languages=["ja"],
            whisper_model=None,
            whisper_language="ja",
            report=lambda _: None,
        )
