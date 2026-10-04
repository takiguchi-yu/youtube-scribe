"""既読 — どの記事を読んだか、と、それを並べた目次。

**既読は記事の front matter にある読んだ日だけで決まる。** 専用の管理ファイルは
持たない。目次（`articles/README.md`）は記事から毎回作り直す一覧にすぎず、
消えても失うものは無い。

記事の形式は `article` が、ファイルの置き場所と書き方は `store` が知っている。
ここが決めるのは、何を未読と呼び、どの順で並べ、目次をどう見せるかだけである。
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from youtube_scribe import article
from youtube_scribe.store import ArticleStore

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")

# リンクの文字列の中で Markdown として解釈されてしまう文字。
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>])")


@dataclass(frozen=True)
class Entry:
    """一覧に載せる記事 1 本ぶん。"""

    path: Path
    video_id: str
    title: str
    generated_at: str
    read_at: str | None

    @property
    def generated_on(self) -> str:
        """作成日。**手元の暦で見せる**（front matter の時刻は UTC）。"""
        try:
            moment = datetime.datetime.fromisoformat(self.generated_at)
        except ValueError:
            return self.generated_at[:10]
        return moment.astimezone().date().isoformat()


def load(store: ArticleStore) -> list[Entry]:
    """記事を**作成日の新しい順**に並べる。新しく増えたものが先頭に来る。"""
    entries: list[Entry] = []
    for path in store.articles():
        try:
            fields = article.read_front_matter(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ValueError(f"{path.name}: {error}") from error
        entries.append(
            Entry(
                path=path,
                video_id=fields.get("video_id", ""),
                title=fields.get("title", path.stem),
                generated_at=fields.get("generated_at", ""),
                read_at=fields.get(article.READ_AT) or None,
            )
        )
    # 時刻はどれも `cli` が UTC で書いた ISO 8601 なので、文字列の順がそのまま時刻の順になる。
    return sorted(entries, key=lambda entry: entry.generated_at, reverse=True)


def unread(entries: Sequence[Entry]) -> list[Entry]:
    """未読。**並び順は `load` のまま**にする。番号がこの並びを指すため。"""
    return [entry for entry in entries if entry.read_at is None]


def read(entries: Sequence[Entry]) -> list[Entry]:
    """既読を、読んだ日の新しい順に。"""
    done = [entry for entry in entries if entry.read_at is not None]
    return sorted(done, key=lambda entry: entry.read_at or "", reverse=True)


def pick(entries: Sequence[Entry], targets: Sequence[str]) -> list[Entry]:
    """番号（未読の並びでの位置）か動画ID で記事を選ぶ。

    **1 つでも見つからなければ何も選ばない。** 一部だけ既読にすると、
    どれが反映されてどれがされなかったのかを人が追うことになる。
    """
    queue = unread(entries)
    by_id = {entry.video_id: entry for entry in entries}
    picked: list[Entry] = []
    missing: list[str] = []
    for target in targets:
        if target.isdigit() and not _VIDEO_ID.fullmatch(target):
            number = int(target)
            if 1 <= number <= len(queue):
                picked.append(queue[number - 1])
            else:
                missing.append(f"{target}（未読は {len(queue)} 本）")
        elif target in by_id:
            picked.append(by_id[target])
        else:
            missing.append(f"{target}（その動画ID の記事は無い）")
    if missing:
        raise LookupError("見つからない: " + "、".join(missing))
    # 同じ記事を 2 回指しても 1 回だけにする。
    return list(dict.fromkeys(picked))


def mark_read(store: ArticleStore, entries: Sequence[Entry], day: datetime.date) -> list[Entry]:
    """記事に読んだ日を書く。**既に読んだ日がある記事は書き換えない。**

    書いた記事だけを返す。
    """
    marked: list[Entry] = []
    for entry in entries:
        if entry.read_at is not None:
            continue
        markdown = entry.path.read_text(encoding="utf-8")
        store.write(entry.path, article.mark_read(markdown, day))
        marked.append(entry)
    return marked


def _link(entry: Entry) -> str:
    # ファイル名には `#` や括弧が入る。そのままだとリンクが途中で切れる。
    title = _MARKDOWN_SPECIAL.sub(r"\\\1", entry.title)
    return f"[{title}]({quote(entry.path.name)})"


def render_index(entries: Sequence[Entry]) -> str:
    """目次の Markdown。未読を先に、`unread` と同じ番号で並べる。"""
    queue = unread(entries)
    done = read(entries)
    lines = [
        "# 記事の一覧",
        "",
        "<!-- youtube-scribe が記事から作り直す。ここを手で書き換えても次に消える。 -->",
        "",
        f"未読 {len(queue)} 本 / 全 {len(entries)} 本。"
        "既読にするには `make done N=<番号>`（番号はこの未読の並び。動画ID でもよい）。",
        "",
        "## 未読",
        "",
    ]
    lines += [
        f"{number}. {entry.generated_on} {_link(entry)}"
        for number, entry in enumerate(queue, start=1)
    ] or ["なし"]
    lines += ["", "## 既読", ""]
    lines += [f"- {entry.read_at} {_link(entry)}" for entry in done] or ["まだ無い"]
    return "\n".join(lines) + "\n"


def write_index(store: ArticleStore) -> Path:
    """目次を記事から作り直す。"""
    path = store.index_path()
    store.write(path, render_index(load(store)))
    return path
