"""書き起こし — **どうやって手に入れたかを問わない言葉。**

字幕から取ったものも、音声から起こしたものも、等しく書き起こしと呼ぶ。
このモジュールが 3 つの入力手段を「秒 + テキスト」という同じ形に潰すので、
`summary` / `frame` / `article` は入力手段を一切知らずに済む。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from youtube_scribe.media import Media

_TIMING = re.compile(r"^(?:(?P<h>\d+):)?(?P<m>\d{1,2}):(?P<s>\d{2}[.,]\d{1,3})\s*-->\s")
_INLINE_TAG = re.compile(r"<[^>]*>")
_TIMEOUT_SEC = 60
_WHISPER_TIMEOUT_SEC = 60 * 60 * 3

# 書き起こしを要約へ渡すときの、1 ブロックの長さ。
# 短くすると時刻の精度は上がるがトークンが増える。15 秒あれば、画面キャプチャの
# 位置決めには十分である。
BLOCK_SEC = 15

# brew の `whisper-cpp` はモデルを落とさない（caveats に明記されている）。
DEFAULT_MODEL = "ggml-large-v3.bin"
_MODEL_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"


class TranscriptUnavailableError(RuntimeError):
    """字幕も文字起こしも得られなかった。動画 1 本を落とす理由になる。"""


class ThrottledError(TranscriptUnavailableError):
    """YouTube に絞られた。**残りの動画を試す意味がない。**

    絞られている最中に叩き続けると、解除がさらに遠のく。1 本目で分かった時点で
    全体を止め、時間をおいて再実行するのが正しい。`summary.ModelUnavailableError`
    と同じ考え方である。
    """


class Source(StrEnum):
    """書き起こしがどうやって作られたか。**3 つは品質が違う。**"""

    MANUAL_CAPTION = "manual_caption"
    AUTO_CAPTION = "auto_caption"
    SPEECH_TO_TEXT = "speech_to_text"

    @property
    def label(self) -> str:
        """記事の front matter に残す日本語表記。"""
        return {
            Source.MANUAL_CAPTION: "人手字幕",
            Source.AUTO_CAPTION: "自動字幕",
            Source.SPEECH_TO_TEXT: "文字起こし",
        }[self]


@dataclass(frozen=True)
class Cue:
    """ある時刻に話された一区切り。"""

    start_sec: float
    text: str


@dataclass(frozen=True)
class Transcript:
    source: Source
    language: str
    cues: tuple[Cue, ...]

    def as_prompt_text(self, *, block_sec: int = BLOCK_SEC) -> str:
        """要約に渡す形。**各行の先頭に秒を置く。**

        これがあるから、要約は「この論点の根拠は何秒か」を答えられる。
        画面キャプチャの時刻はここから来る。

        **cue をそのまま 1 行ずつにしない。** 自動字幕は 3 秒に 1 つ程度の細切れで、
        69 分の動画なら 1,400 行を超える。`[秒] ` の接頭辞だけで 1 万トークン近くを
        消費するうえ、断片のままでは読みにくい。`block_sec` 秒ごとにまとめる。

        画面キャプチャに要る精度は数秒あれば足りる（そもそも撮る時刻には
        オフセットを足している）ので、粒度を落として困らない。
        """
        return "\n".join(f"[{start}] {text}" for start, text in _blocks(self.cues, block_sec))


def _blocks(cues: tuple[Cue, ...], block_sec: int) -> list[tuple[int, str]]:
    """cue を `block_sec` 秒ごとにまとめ、`(開始秒, 本文)` の並びにする。

    **ブロックの開始秒は、最初に入れた cue の秒そのもの。** 切りのよい秒に丸めると、
    要約が返す時刻が実際の発話からずれる。
    """
    if block_sec <= 0:
        return [(int(cue.start_sec), cue.text) for cue in cues]

    blocks: list[tuple[int, str]] = []
    start: int | None = None
    parts: list[str] = []
    for cue in cues:
        second = int(cue.start_sec)
        if start is None:
            start = second
        elif second - start >= block_sec:
            blocks.append((start, " ".join(parts)))
            start = second
            parts = []
        parts.append(cue.text)
    if start is not None and parts:
        blocks.append((start, " ".join(parts)))
    return blocks


def _parse_timing(line: str) -> float | None:
    """`00:01:02.500 --> ...` の開始秒を返す。タイミング行でなければ None。"""
    matched = _TIMING.match(line)
    if matched is None:
        return None
    hours = int(matched.group("h") or 0)
    return hours * 3600 + int(matched.group("m")) * 60 + float(matched.group("s").replace(",", "."))


def parse_vtt(body: str) -> tuple[Cue, ...]:
    """WebVTT を Cue の並びにする。

    **自動生成字幕の癖を 2 つ吸収する。**
    1. `<00:00:01.500><c>語</c>` のようなインラインのタイミングタグが混ざる
    2. 直前の行を繰り返しながら 1 行ずつ流れていく（ローリング表示）

    2 については、直前の Cue に出ていた行を落とす。同じ行が本当に連続して
    2 回話された場合は片方が消えるが、要約の材料としては害がない。
    """
    cues: list[Cue] = []
    start: float | None = None
    buffer: list[str] = []
    previous_lines: list[str] = []

    def flush() -> None:
        nonlocal start, buffer, previous_lines
        if start is None:
            buffer = []
            return
        lines = [_INLINE_TAG.sub("", line).strip() for line in buffer]
        lines = [line for line in lines if line]
        fresh = [line for line in lines if line not in previous_lines]
        if fresh:
            cues.append(Cue(start_sec=start, text=" ".join(fresh)))
        previous_lines = lines
        start = None
        buffer = []

    for raw in body.splitlines():
        line = raw.rstrip()
        timing = _parse_timing(line)
        if timing is not None:
            flush()
            start = timing
            continue
        if not line.strip():
            flush()
            continue
        if start is not None:
            buffer.append(line)

    flush()
    return tuple(cues)


def parse_whisper_json(payload: dict[str, Any]) -> tuple[str, tuple[Cue, ...]]:
    """`whisper-cli --output-json` の出力を読む。

    `offsets.from` はミリ秒（実装上 `t0 * 10`）。判定された言語は `result.language`。
    """
    language = str(payload.get("result", {}).get("language", ""))
    segments = payload.get("transcription")
    if not isinstance(segments, list):
        return language, ()

    cues: list[Cue] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text", "")).strip()
        offset = segment.get("offsets", {}).get("from")
        if text and isinstance(offset, int):
            cues.append(Cue(start_sec=offset / 1000, text=text))
    return language, tuple(cues)


def default_model_path(name: str = DEFAULT_MODEL) -> Path:
    """モデルの置き場所。**リポジトリの中には置かない。**

    3GB 近いファイルなので、複数のリポジトリから使い回せる場所に置く。
    """
    return Path.home() / ".cache" / "whisper.cpp" / name


def ensure_model(path: Path, *, report: Callable[[str], None]) -> Path:
    """モデルが無ければ HuggingFace から落とす。

    途中で落ちても壊れたファイルが残らないよう、一時ファイルに書いてから置き換える。
    """
    if path.exists():
        return path

    url = f"{_MODEL_BASE}{path.name}"
    report(f"  whisper のモデルが無いので取得する: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SEC) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with temporary.open("wb") as stream:
                while chunk := response.read(1 << 20):
                    stream.write(chunk)
                    done += len(chunk)
                    if total and done % (64 << 20) < (1 << 20):
                        report(f"    {done / total:.0%} ({done >> 20} MiB / {total >> 20} MiB)")
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def to_json(transcript: Transcript) -> str:
    """書き起こしを控えに書ける形にする。"""
    return json.dumps(
        {
            "source": transcript.source.value,
            "language": transcript.language,
            "cues": [[cue.start_sec, cue.text] for cue in transcript.cues],
        },
        ensure_ascii=False,
    )


def from_json(text: str) -> Transcript | None:
    """控えを読み戻す。**形が違えば黙って捨てる。**

    控えは高速化のためだけにあるので、読めないなら取り直せばよい。
    ここで例外を投げて動画 1 本を落とす価値がない。
    """
    try:
        payload = json.loads(text)
        return Transcript(
            source=Source(payload["source"]),
            language=str(payload["language"]),
            cues=tuple(Cue(float(start), str(body)) for start, body in payload["cues"]),
        )
    except (ValueError, KeyError, TypeError):
        return None


def _download(url: str) -> str:
    request = urllib.request.Request(url, headers={"Accept": "text/vtt, */*"})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SEC) as response:
        body: bytes = response.read()
    return body.decode("utf-8", errors="replace")


def _preferred_caption(media: Media, languages: list[str]) -> tuple[Source, str, str] | None:
    """字幕の優先順を決める。**人手字幕を常に自動字幕より優先する。**

    自動字幕は句読点が無く固有名詞も崩れるので、英語の人手字幕のほうが
    日本語の自動字幕より要約の材料として良い。

    自動字幕の中では、さらに **原語（`-orig`）を機械翻訳版より優先する。**
    YouTube は自動字幕を 157 言語へ機械翻訳して並べるため、言語だけで選ぶと
    英語の動画でも「機械文字起こし → 機械翻訳」の二重劣化を踏んだ `ja` が当たる。
    原語をそのまま渡し、日本語化は要約側に一本化したほうが精度が高い。
    """
    if (found := _match(Source.MANUAL_CAPTION, media.manual_captions, languages)) is not None:
        return found
    if (found := _original_auto_caption(media.auto_captions, languages)) is not None:
        return found
    return _match(Source.AUTO_CAPTION, media.auto_captions, languages)


def _match(
    source: Source, tracks: dict[str, str], languages: list[str]
) -> tuple[Source, str, str] | None:
    """`languages` の順に、言語が一致するトラックを探す。"""
    for wanted in languages:
        for language, url in tracks.items():
            if language == wanted or language.startswith(f"{wanted}-"):
                return source, language, url
    return None


def _original_auto_caption(
    tracks: dict[str, str], languages: list[str]
) -> tuple[Source, str, str] | None:
    """自動字幕のうち、動画の原語のもの。

    YouTube は原語のトラックに `-orig` を付ける（`ja-orig` / `en-US-orig` など）。
    **これがあれば、何語であれ機械翻訳版より優先する。**

    **多言語音声を持つ動画は `-orig` が複数ある。** その場合は `languages` の順で選ぶ。
    どれも当てはまらなければ、原語であることを優先して先頭を返す。
    """
    originals = {language: url for language, url in tracks.items() if language.endswith("-orig")}
    if not originals:
        return None
    if (found := _match(Source.AUTO_CAPTION, originals, languages)) is not None:
        return found
    language, url = next(iter(originals.items()))
    return Source.AUTO_CAPTION, language, url


def _whisper_binary() -> str:
    """`whisper-cli` の場所。**モデルを落とす前に呼ぶこと。**

    順番を逆にすると、whisper-cpp が入っていない環境で 2.9GiB を落としてから
    失敗することになる。
    """
    binary = shutil.which("whisper-cli")
    if binary is None:
        raise TranscriptUnavailableError(
            "whisper-cli が PATH に無い（`brew install whisper-cpp` で入る）"
        )
    return binary


def _run_whisper(
    binary: str, audio_url: str, model: Path, language: str
) -> tuple[str, tuple[Cue, ...]]:
    """`whisper-cli` を呼んで文字起こしする。**stdout はパースしない。**

    JSON ファイルに書かせて、それを読む。
    """
    with tempfile.TemporaryDirectory() as workdir:
        prefix = Path(workdir) / "out"
        command = [
            binary,
            "--model",
            str(model),
            "--language",
            language,
            "--output-json",
            "--no-prints",
            "--output-file",
            str(prefix),
            audio_url,
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=_WHISPER_TIMEOUT_SEC, check=False
        )
        result = prefix.with_suffix(".json")
        if completed.returncode != 0 or not result.exists():
            detail = (completed.stderr or completed.stdout or "").strip()[-500:]
            raise TranscriptUnavailableError(f"whisper-cli が失敗した: {detail}")
        return parse_whisper_json(json.loads(result.read_text(encoding="utf-8")))


def fetch(
    media: Media,
    *,
    languages: list[str],
    whisper_model: Path | None,
    whisper_language: str,
    report: Callable[[str], None],
) -> Transcript:
    """字幕を優先し、取れなければ文字起こしに落とす。

    どちらも駄目なら `TranscriptUnavailableError`。**記事は作らない。**

    **モデルの取得は、文字起こしが実際に必要になってから行う。** 先に落としてしまうと、
    全動画に字幕があるだけの再生リストでも 2.9GiB を消費することになる。
    """
    reason = "この動画に字幕が無い"
    chosen = _preferred_caption(media, languages)
    if chosen is not None:
        source, language, url = chosen
        try:
            cues = parse_vtt(_download(url))
        except urllib.error.HTTPError as error:
            # **一時的な失敗で文字起こしへ落とさない。** YouTube は字幕の取得を
            # 絞ることがあり（429）、そこで 2.9GiB のモデルを落として音声認識を
            # 始めるのは明らかに割に合わない。記事を作らず終われば、次の実行で
            # そのまま再試行される。
            if error.code == 429 or error.code >= 500:
                raise ThrottledError(
                    f"YouTube が字幕の取得を拒否した（HTTP {error.code}）。"
                    "時間をおいて再実行すれば取得できる"
                ) from error
            reason = f"字幕を取得できなかった（HTTP {error.code}）"
            cues = ()
        except (urllib.error.URLError, OSError) as error:
            reason = f"字幕を取得できなかった（{error}）"
            cues = ()
        else:
            if not cues:
                reason = "字幕が空だった"
        if cues:
            return Transcript(source=source, language=language, cues=cues)

    # **握り潰さない。** 字幕が駄目だった理由を、そのまま次のメッセージへ運ぶ。
    if whisper_model is None:
        raise TranscriptUnavailableError(f"{reason}。文字起こしも無効になっている")
    if media.audio_url is None:
        raise TranscriptUnavailableError(f"{reason}。音声ストリームも見つからない")
    report(f"  {reason}ので、音声から文字起こしする")

    binary = _whisper_binary()
    model = ensure_model(whisper_model, report=report)
    language, cues = _run_whisper(binary, media.audio_url, model, whisper_language)
    if not cues:
        raise TranscriptUnavailableError("文字起こしの結果が空だった")
    return Transcript(source=Source.SPEECH_TO_TEXT, language=language, cues=cues)
