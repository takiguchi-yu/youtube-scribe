"""画面キャプチャ。ffmpeg そのものは外部境界なので、報告のしかたを固める。"""

import subprocess
from pathlib import Path

import pytest

from youtube_scribe import frame


def test_no_seconds_means_no_work_and_no_report(tmp_path: Path) -> None:
    said: list[str] = []
    assert frame.extract("u", (), tmp_path, offset_sec=3, duration_sec=0, report=said.append) == ()
    assert said == []
    # 撮る時刻が無いなら、ディレクトリも作らない。
    assert list(tmp_path.iterdir()) == []


def test_failures_are_reported_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**黙って画像なしの記事を書かない。**

    ffmpeg が壊れていることに気づかないまま 9 本書いた事故があった。
    """

    class Failed:
        returncode = 1
        stdout = ""
        stderr = "dyld: Library not loaded: libx265.216.dylib"

    monkeypatch.setattr(frame, "_ffmpeg", lambda: "/bin/false")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Failed())

    said: list[str] = []
    got = frame.extract("u", (10, 20), tmp_path, offset_sec=3, duration_sec=0, report=said.append)
    assert got == ()
    assert len(said) == 1
    assert "2 枚中 0 枚" in said[0]
    # 原因が読み取れること。ここが空だと切り分けに回り道する。
    assert "libx265" in said[0]


def test_success_says_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command: list[str], **kwargs: object) -> Ok:
        Path(command[-1]).write_bytes(b"jpeg")
        return Ok()

    monkeypatch.setattr(frame, "_ffmpeg", lambda: "/bin/true")
    monkeypatch.setattr(subprocess, "run", fake_run)

    said: list[str] = []
    got = frame.extract("u", (10,), tmp_path, offset_sec=3, duration_sec=0, report=said.append)
    assert len(got) == 1
    assert got[0].requested_sec == 10
    assert got[0].at_sec == 13
    assert said == []
