"""取り込みの入口。**配線とメッセージだけを持つ。**

    uv run youtube-scribe "https://www.youtube.com/playlist?list=PL..."
    uv run youtube-scribe "https://www.youtube.com/watch?v=..." --limit 1
    uv run youtube-scribe PL... --dry-run

必要な環境変数:
    YOUTUBE_API_KEY   再生リストと動画メタデータの取得に使う
    GEMINI_API_KEY    要約に使う（SDK が自動で読む）
    GEMINI_MODEL      使うモデルを変えたいとき（任意）
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import truststore

from youtube_scribe import article, frame, media, playlist, summary, transcript
from youtube_scribe.store import ArticleStore
from youtube_scribe.video import Video
from youtube_scribe.video import fetch as fetch_videos

# 既定の出力先は**リポジトリを基準にする**。cwd を基準にすると、別のディレクトリから
# 叩いたときにそちらへ記事が散らばる。
DEFAULT_OUT = Path(__file__).resolve().parent.parent.parent / "articles"

# 「この図を見てください」の発話より少しあとに画面が出る、という経験則ぶん。
DEFAULT_OFFSET_SEC = 3

# 動画と動画の間に置く待ち。
# 無料枠の上限は 20 リクエスト（`generate_content_free_tier_requests`、実測）で、
# 失敗した 1 本は SDK のリトライで最大 5 リクエストを消費する。**60 秒空ければ、
# 1 分の窓に収まるのは 1 本ぶんだけ**になり、上限に余裕ができる。
DEFAULT_DELAY_SEC = 60


@dataclass(frozen=True)
class Args:
    target: str
    out: Path
    limit: int | None
    offset_sec: int
    whisper_model: Path | None
    whisper_language: str
    languages: list[str]
    models: list[str]
    with_frames: bool
    dry_run: bool
    delay_sec: int


def parse_args(argv: Sequence[str]) -> Args:
    parser = argparse.ArgumentParser(
        prog="youtube-scribe",
        description="YouTube の再生リストから、動画 1 本ごとの学習メモを作る",
    )
    parser.add_argument("target", help="再生リストの URL/ID、または単体動画の URL")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="記事の出力先")
    parser.add_argument("--limit", type=int, default=None, help="処理する本数の上限")
    parser.add_argument(
        "--offset-sec",
        type=int,
        default=DEFAULT_OFFSET_SEC,
        help="画面キャプチャを、要約が指定した時刻から何秒後に撮るか",
    )
    parser.add_argument(
        "--sub-langs",
        default="ja,en",
        help="字幕を探す言語の優先順（カンマ区切り）",
    )
    parser.add_argument(
        "--whisper-model",
        type=Path,
        default=None,
        help=f"whisper のモデル（既定: {transcript.default_model_path()}）",
    )
    parser.add_argument("--whisper-language", default="ja", help="文字起こしの言語")
    parser.add_argument(
        "--model",
        default=None,
        help=f"要約に使う Gemini モデル（既定: {summary.DEFAULT_MODEL}、GEMINI_MODEL でも指定可）",
    )
    parser.add_argument(
        "--fallback-models",
        default=None,
        help=(
            "既定のモデルが 1 日ぶんのクォータを使い切ったときに順に試すモデル"
            "（カンマ区切り、GEMINI_FALLBACK_MODELS でも指定可。空文字で無効）"
        ),
    )
    parser.add_argument(
        "--no-transcribe",
        action="store_true",
        help="字幕が無い動画で文字起こしに落とさず、スキップする",
    )
    parser.add_argument(
        "--delay-sec",
        type=int,
        default=DEFAULT_DELAY_SEC,
        help=f"動画と動画の間に置く待ち秒数（既定: {DEFAULT_DELAY_SEC}）",
    )
    parser.add_argument("--no-frames", action="store_true", help="画面キャプチャを撮らない")
    parser.add_argument("--dry-run", action="store_true", help="対象を並べるだけで何も作らない")

    parsed = parser.parse_args(argv)

    model: Path | None = None
    if not parsed.no_transcribe:
        model = parsed.whisper_model or Path(
            os.environ.get("WHISPER_MODEL", "") or transcript.default_model_path()
        )

    return Args(
        target=parsed.target,
        out=parsed.out,
        limit=parsed.limit,
        offset_sec=parsed.offset_sec,
        whisper_model=model,
        whisper_language=parsed.whisper_language,
        models=_model_chain(parsed.model, parsed.fallback_models),
        languages=[part.strip() for part in parsed.sub_langs.split(",") if part.strip()],
        with_frames=not parsed.no_frames,
        dry_run=parsed.dry_run,
        delay_sec=max(parsed.delay_sec, 0),
    )


def _model_chain(model: str | None, fallbacks: str | None) -> list[str]:
    """試す順にモデルを並べる。**クォータはモデルごとに独立している。**

    無料枠は Flash 系が RPD 20、Flash Lite 系が RPD 500（AI Studio で実測）。
    1 本 = 1 リクエストなので、使い切っても次のモデルへ移れば同じ日に続けられる。
    """
    primary = model or summary.model_name()
    rest = (
        tuple(part.strip() for part in fallbacks.split(",") if part.strip())
        if fallbacks is not None
        else summary.fallback_models()
    )
    chain = [primary]
    chain.extend(name for name in rest if name not in chain)
    return chain


def resolve_targets(target: str, api_key: str) -> list[str]:
    """入力を**動画ID の並び**に正規化する。再生リストでも単体動画でも同じ形にする。"""
    single = playlist.video_id(target)
    if single is not None:
        return [single]
    return playlist.video_ids(playlist.playlist_id(target), api_key)


def _process(
    video: Video,
    args: Args,
    store: ArticleStore,
    client: summary.Client,
    model: str,
) -> Path:
    """動画 1 本を記事にする。失敗したら例外を投げ、**記事は作らない。**"""
    cache = store.cache_path(video.video_id)
    text = transcript.from_json(cache.read_text(encoding="utf-8")) if cache.exists() else None
    if text is None:
        found = media.probe(video.url)
        text = transcript.fetch(
            found,
            languages=args.languages,
            whisper_model=args.whisper_model,
            whisper_language=args.whisper_language,
            report=print,
        )
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(transcript.to_json(text), encoding="utf-8")
        video_url = found.video_url
    else:
        print(f"  書き起こしの控えを使う（{text.source.label}）")
        # 画像を撮るなら映像の URL が要る。控えには入っていない（期限切れになる）。
        video_url = media.probe(video.url).video_url if args.with_frames else None
    written = summary.write(video, text, client, model)

    frames: dict[int, frame.Frame] = {}
    if args.with_frames and video_url is not None:
        taken = frame.extract(
            video_url,
            written.evidence_seconds(),
            store.asset_dir(video.video_id),
            offset_sec=args.offset_sec,
            duration_sec=video.duration_sec,
            report=print,
        )
        frames = {item.requested_sec: item for item in taken}

    markdown = article.render(
        video,
        text,
        written,
        frames,
        generated_at=datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        model=model,
        asset_prefix=store.asset_prefix(video.video_id),
    )
    return store.save(video.video_id, video.title, markdown)


def run(args: Args) -> int:
    truststore.inject_into_ssl()

    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        print("YOUTUBE_API_KEY が設定されていない", file=sys.stderr)
        return 2

    store = ArticleStore(args.out)
    try:
        video_ids = resolve_targets(args.target, api_key)
    except Exception as error:
        print(f"対象を解決できなかった: {error}", file=sys.stderr)
        return 1

    pending = [vid for vid in video_ids if not store.has(vid)]
    skipped = len(video_ids) - len(pending)
    if args.limit is not None:
        pending = pending[: args.limit]

    print(f"対象 {len(video_ids)} 本 / 処理済み {skipped} 本 / これから {len(pending)} 本")
    if not pending:
        return 0

    try:
        videos = fetch_videos(pending, api_key)
    except Exception as error:
        print(f"動画のメタデータを取得できなかった: {error}", file=sys.stderr)
        return 1

    if args.dry_run:
        for video_id in pending:
            found = videos.get(video_id)
            print(f"  {video_id}  {found.title if found else '(メタデータ無し)'}")
        return 0

    client = summary.new_client()
    failures: list[tuple[str, str]] = []
    created = 0

    stopped = False
    # いま使っているモデル。クォータを使い切ったら次へ送る。
    # **クォータはモデルごとに独立している**ので、これで同じ日に続けられる。
    model_index = 0

    for index, video_id in enumerate(pending, start=1):
        video = videos.get(video_id)
        if video is None:
            failures.append((video_id, "メタデータが取得できなかった"))
            continue
        if created or failures:
            # **動画の間を空ける。** 無料枠は RPM 5（実測）しかないので、
            # 詰めて投げると長い再生リストで確実に当たる。
            time.sleep(args.delay_sec)
        print(f"[{index}/{len(pending)}] {video.title}")

        while True:
            model = args.models[model_index]
            try:
                path = _process(video, args, store, client, model)
            except summary.ModelUnavailableError as error:
                # **理由をそのまま出す。** レート制限と混雑は対処が違うので、
                # 「今は使えない」だけでは次の一手が決まらない。
                print(f"  {model} が今は使えない: {error}", file=sys.stderr)
                model_index += 1
                if model_index < len(args.models):
                    # **同じ動画を、次のモデルでそのまま試す。**
                    print(f"  {args.models[model_index]} に切り替える", file=sys.stderr)
                    continue
                store.discard_assets(video_id)
                failures.append((video_id, str(error)))
                print(
                    f"  試せるモデルを全部試したので、残り {len(pending) - index} 本は"
                    "試さずに終了する",
                    file=sys.stderr,
                )
                stopped = True
            except transcript.ThrottledError as error:
                # **残りを試さない。** 絞られている最中に投げ続けても、
                # 解除が遠のくだけで得るものがない。
                store.discard_assets(video_id)
                failures.append((video_id, str(error)))
                print(f"  失敗: {error}", file=sys.stderr)
                print(
                    f"  相手側の制限に当たったので、残り {len(pending) - index} 本は"
                    "試さずに終了する",
                    file=sys.stderr,
                )
                stopped = True
            except Exception as error:
                # **記事を書かないので、次回の実行でそのまま再試行される。**
                store.discard_assets(video_id)
                failures.append((video_id, str(error)))
                print(f"  失敗: {error}", file=sys.stderr)
            else:
                created += 1
                print(f"  書いた: {path.name}")
            break

        if stopped:
            break

    remaining = len(pending) - created - len(failures) if stopped else 0
    print(
        f"\n作成 {created} 本 / 失敗 {len(failures)} 本"
        + (f" / 未着手 {remaining} 本" if remaining else "")
    )
    for video_id, reason in failures:
        print(f"  失敗 {video_id}: {reason}", file=sys.stderr)
    return 0 if not failures else 1


def main(argv: Sequence[str] | None = None) -> int:
    return run(parse_args(sys.argv[1:] if argv is None else argv))
