"""画面キャプチャ — 動画のある時刻を静止画として切り出す。

**`-ss` を `-i` の前に置くこと。** 入力側シークなら 600 秒の動画で実測 0.09 秒、
出力側シークなら 7.97 秒かかる。しかも `-accurate_seek`（既定で有効）のおかげで
両者の出力は同一で、速い代わりに精度が落ちるということはない。

シーン変化検出（`select='gt(scene,N)'`）は使わない。スコアが輝度のみで計算されるため
**色だけが変わるスライド切り替わりを原理的に検出できず**、しかも全フレームの復号が要る。
撮る時刻は `summary` が決める。
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_TIMEOUT_SEC = 120
_JPEG_QUALITY = "2"


class FfmpegMissingError(RuntimeError):
    """ffmpeg が PATH に無い。"""


@dataclass(frozen=True)
class Frame:
    """ある時刻の静止画。

    `requested_sec` は要約が指定した秒、`at_sec` はオフセットを足したあとの
    実際に切り出した秒。**本文の節と結びつけるのに使うのは `requested_sec` のほう。**
    """

    requested_sec: int
    at_sec: int
    path: Path


def _ffmpeg() -> str:
    binary = shutil.which("ffmpeg")
    if binary is None:
        raise FfmpegMissingError("ffmpeg が PATH に無い（`brew install ffmpeg` で入る）")
    return binary


def target_second(second: int, *, offset_sec: int, duration_sec: int) -> int:
    """実際に切り出す秒を決める。

    **`offset_sec` を足すのは、発話と画面の切り替わりがずれるため。**
    「この図を見てください」と言った時点では、まだ図が出ていないことのほうが多い。

    動画の末尾を越えると 1 枚も取れないので、長さがわかっているときは内側へ丸める。
    """
    at = second + offset_sec
    if duration_sec > 0:
        at = min(at, max(duration_sec - 1, 0))
    return max(at, 0)


def extract(
    media_url: str,
    seconds: tuple[int, ...],
    out_dir: Path,
    *,
    offset_sec: int,
    duration_sec: int,
    report: Callable[[str], None],
) -> tuple[Frame, ...]:
    """指定された各時刻を 1 枚ずつ切り出す。

    1 枚でも失敗したら例外にする、ということはしない。**画像は記事の付加物**であって、
    1 枚欠けたからといって記事全体を落とす価値がないためである。

    **ただし黙って諦めない。** 撮れなかったら理由を `report` に流す。これを怠ったせいで、
    ffmpeg が壊れていることに気づかないまま画像なしの記事を 9 本書いた事故があった。
    """
    if not seconds:
        return ()

    binary = _ffmpeg()
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Frame] = []
    # 動画の末尾へ丸め込まれた結果、別々の要求が同じ 1 枚に落ち着くことがある。
    # そのときは撮り直さず、同じファイルを指す。
    taken: dict[int, Path] = {}
    failure: str | None = None

    for second in seconds:
        at = target_second(second, offset_sec=offset_sec, duration_sec=duration_sec)
        if at in taken:
            frames.append(Frame(requested_sec=second, at_sec=at, path=taken[at]))
            continue

        path = out_dir / f"{at}.jpg"
        command = [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(at),
            "-i",
            media_url,
            "-frames:v",
            "1",
            "-q:v",
            _JPEG_QUALITY,
            str(path),
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=_TIMEOUT_SEC, check=False
        )
        if completed.returncode == 0 and path.exists() and path.stat().st_size > 0:
            taken[at] = path
            frames.append(Frame(requested_sec=second, at_sec=at, path=path))
        elif failure is None:
            # 最初の 1 件だけ理由を控える。全部出すと同じ話が並ぶだけになる。
            failure = (completed.stderr or completed.stdout or "").strip()[-300:]

    if len(frames) < len(seconds):
        detail = f"（{failure}）" if failure else ""
        report(f"  画面キャプチャ {len(seconds)} 枚中 {len(frames)} 枚しか撮れなかった{detail}")
    return tuple(frames)
