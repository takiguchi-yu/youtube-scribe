"""引数の解釈。**配線の入口なので、既定値がずれると全部ずれる。**"""

import datetime
from pathlib import Path

import pytest

from youtube_scribe import summary, transcript
from youtube_scribe.cli import (
    DEFAULT_OFFSET_SEC,
    DEFAULT_OUT,
    ShelfArgs,
    _model_chain,
    main,
    parse_args,
    parse_shelf_args,
    run_shelf,
)
from youtube_scribe.frame import target_second
from youtube_scribe.store import ArticleStore

PLAYLIST = "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"


def test_defaults() -> None:
    args = parse_args([PLAYLIST])
    assert args.target == PLAYLIST
    assert args.limit is None
    assert args.offset_sec == DEFAULT_OFFSET_SEC
    assert args.languages == ["ja", "en"]
    assert args.with_frames is True
    assert args.dry_run is False
    assert args.whisper_model == transcript.default_model_path()
    # 既定は「本命 + フォールバック」の並び。クォータはモデルごとに独立している。
    assert args.models[0] == summary.DEFAULT_MODEL
    assert len(args.models) > 1


def test_no_transcribe_disables_the_fallback() -> None:
    # 文字起こしを切ったら、モデルの場所そのものを持たない。
    assert parse_args([PLAYLIST, "--no-transcribe"]).whisper_model is None


def test_whisper_model_can_be_overridden() -> None:
    args = parse_args([PLAYLIST, "--whisper-model", "/tmp/ggml-small.bin"])
    assert args.whisper_model == Path("/tmp/ggml-small.bin")


def test_whisper_model_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHISPER_MODEL", "/models/ggml-medium.bin")
    assert parse_args([PLAYLIST]).whisper_model == Path("/models/ggml-medium.bin")


def test_explicit_flag_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHISPER_MODEL", "/models/from-env.bin")
    args = parse_args([PLAYLIST, "--whisper-model", "/models/from-flag.bin"])
    assert args.whisper_model == Path("/models/from-flag.bin")


def test_sub_langs_is_split_and_trimmed() -> None:
    assert parse_args([PLAYLIST, "--sub-langs", " ja , en ,"]).languages == ["ja", "en"]


def test_flags() -> None:
    args = parse_args([PLAYLIST, "--no-frames", "--dry-run", "--limit", "3", "--offset-sec", "10"])
    assert args.with_frames is False
    assert args.dry_run is True
    assert args.limit == 3
    assert args.offset_sec == 10


def test_target_second_applies_the_offset() -> None:
    assert target_second(60, offset_sec=3, duration_sec=1800) == 63


def test_target_second_clamps_to_the_end_of_the_video() -> None:
    # 末尾を越えると 1 枚も撮れないので、内側へ丸める。
    assert target_second(1799, offset_sec=5, duration_sec=1800) == 1799


def test_target_second_without_a_known_duration() -> None:
    assert target_second(10, offset_sec=3, duration_sec=0) == 13


def test_target_second_never_goes_negative() -> None:
    assert target_second(0, offset_sec=-10, duration_sec=1800) == 0


def test_model_chain_puts_the_primary_first() -> None:
    chain = _model_chain("gemini-3.8-flash", "gemini-3.7-flash,gemini-3.5-flash-lite")
    assert chain == ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash-lite"]


def test_model_chain_drops_a_duplicate_primary() -> None:
    # 本命がフォールバックにも入っていたら、二度試さない。
    chain = _model_chain("gemini-3.7-flash", "gemini-3.7-flash,gemini-3.5-flash-lite")
    assert chain == ["gemini-3.7-flash", "gemini-3.5-flash-lite"]


def test_model_chain_can_be_disabled() -> None:
    assert _model_chain("gemini-3.8-flash", "") == ["gemini-3.8-flash"]


def test_model_chain_uses_the_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_FALLBACK_MODELS", raising=False)
    chain = _model_chain(None, None)
    assert chain[0] == summary.DEFAULT_MODEL
    assert list(summary.DEFAULT_FALLBACK_MODELS) == chain[1:]


def test_model_chain_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", " gemini-3.6-flash , gemini-3 ")
    assert _model_chain("gemini-3.8-flash", None)[1:] == ["gemini-3.6-flash", "gemini-3"]


def test_shelf_commands_default_to_the_repository_articles() -> None:
    assert parse_shelf_args(["unread"]) == ShelfArgs(command="unread", targets=[], out=DEFAULT_OUT)


def test_done_takes_several_targets() -> None:
    assert parse_shelf_args(["done", "3", "dQw4w9WgXcQ"]).targets == ["3", "dQw4w9WgXcQ"]


def test_done_needs_a_target() -> None:
    with pytest.raises(SystemExit):
        parse_shelf_args(["done"])


def _shelf(tmp_path: Path) -> ArticleStore:
    store = ArticleStore(tmp_path)
    for video_id, title in [("aaaaaaaaaaa", "一本目"), ("bbbbbbbbbbb", "二本目")]:
        front = f'---\nvideo_id: {video_id}\ntitle: "{title}"\n'
        store.save(video_id, title, front + "generated_at: 2026-09-01T12:00:00+00:00\n---\n")
    return store


def test_done_marks_and_rebuilds_the_index(tmp_path: Path) -> None:
    store = _shelf(tmp_path)
    args = ShelfArgs(command="done", targets=["aaaaaaaaaaa"], out=tmp_path)
    assert run_shelf(args, today=datetime.date(2026, 10, 5)) == 0
    assert "read_at: 2026-10-05" in (tmp_path / "一本目--aaaaaaaaaaa.md").read_text(
        encoding="utf-8"
    )
    assert "未読 1 本 / 全 2 本" in store.index_path().read_text(encoding="utf-8")


def test_done_writes_nothing_when_any_target_is_missing(tmp_path: Path) -> None:
    store = _shelf(tmp_path)
    before = {path: path.read_text(encoding="utf-8") for path in store.articles()}
    args = ShelfArgs(command="done", targets=["1", "9"], out=tmp_path)
    assert run_shelf(args, today=datetime.date(2026, 10, 5)) == 1
    assert {path: path.read_text(encoding="utf-8") for path in store.articles()} == before
    assert not store.index_path().exists()


def test_main_routes_shelf_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _shelf(tmp_path)
    assert main(["unread", "--out", str(tmp_path)]) == 0
    assert "未読 2 本 / 全 2 本" in capsys.readouterr().out
