"""既読と目次。**番号は未読の並びを指す**ので、並び順が変わると別の記事を既読にしてしまう。"""

import datetime
import json
from pathlib import Path

import pytest

from youtube_scribe import reading
from youtube_scribe.store import ArticleStore

BODY = "\n# 題\n\n## 所感\n\n<!-- ここは自分で書く -->\n"


def write_article(
    store: ArticleStore,
    video_id: str,
    title: str,
    generated_at: str,
    read_at: str | None = None,
) -> Path:
    lines = [
        "---",
        f"video_id: {video_id}",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"generated_at: {generated_at}",
    ]
    if read_at is not None:
        lines.append(f"read_at: {read_at}")
    lines.append("---")
    return store.save(video_id, title, "\n".join(lines) + "\n" + BODY)


@pytest.fixture
def store(tmp_path: Path) -> ArticleStore:
    store = ArticleStore(tmp_path)
    write_article(store, "aaaaaaaaaaa", "古い記事", "2026-09-01T12:00:00+00:00")
    write_article(store, "bbbbbbbbbbb", "新しい記事", "2026-09-20T12:00:00+00:00")
    write_article(
        store, "ccccccccccc", "読んだ記事", "2026-09-10T12:00:00+00:00", read_at="2026-10-01"
    )
    return store


def test_load_puts_the_newest_first(store: ArticleStore) -> None:
    titles = [entry.title for entry in reading.load(store)]
    assert titles == ["新しい記事", "読んだ記事", "古い記事"]


def test_unread_keeps_the_load_order_and_skips_read(store: ArticleStore) -> None:
    titles = [entry.title for entry in reading.unread(reading.load(store))]
    assert titles == ["新しい記事", "古い記事"]


def test_read_is_ordered_by_the_day_it_was_read(tmp_path: Path) -> None:
    store = ArticleStore(tmp_path)
    write_article(store, "aaaaaaaaaaa", "先に読んだ", "2026-09-20T12:00:00+00:00", "2026-10-01")
    write_article(store, "bbbbbbbbbbb", "後で読んだ", "2026-09-01T12:00:00+00:00", "2026-10-03")
    assert [entry.title for entry in reading.read(reading.load(store))] == [
        "後で読んだ",
        "先に読んだ",
    ]


def test_generated_on_is_a_date(store: ArticleStore) -> None:
    # 12:00 UTC なら、日本でも UTC でも同じ日になる。
    assert reading.load(store)[0].generated_on == "2026-09-20"


def test_index_is_not_an_article(store: ArticleStore) -> None:
    reading.write_index(store)
    assert store.index_path().exists()
    assert len(reading.load(store)) == 3


def test_pick_by_number_follows_the_unread_order(store: ArticleStore) -> None:
    entries = reading.load(store)
    assert [entry.title for entry in reading.pick(entries, ["2"])] == ["古い記事"]


def test_pick_by_video_id_reaches_read_articles_too(store: ArticleStore) -> None:
    entries = reading.load(store)
    assert [entry.title for entry in reading.pick(entries, ["ccccccccccc"])] == ["読んだ記事"]


def test_pick_collapses_duplicates(store: ArticleStore) -> None:
    entries = reading.load(store)
    assert len(reading.pick(entries, ["1", "bbbbbbbbbbb"])) == 1


def test_pick_treats_an_eleven_digit_target_as_a_video_id(tmp_path: Path) -> None:
    store = ArticleStore(tmp_path)
    write_article(store, "12345678901", "数字だけの動画ID", "2026-09-01T12:00:00+00:00")
    entries = reading.load(store)
    assert [entry.title for entry in reading.pick(entries, ["12345678901"])] == ["数字だけの動画ID"]


@pytest.mark.parametrize("target", ["0", "3", "zzzzzzzzzzz", "nonsense"])
def test_pick_refuses_what_it_cannot_find(store: ArticleStore, target: str) -> None:
    with pytest.raises(LookupError):
        reading.pick(reading.load(store), ["1", target])


def test_mark_read_writes_only_the_day(store: ArticleStore) -> None:
    entry = reading.pick(reading.load(store), ["1"])[0]
    before = entry.path.read_text(encoding="utf-8")

    marked = reading.mark_read(store, [entry], datetime.date(2026, 10, 5))

    after = entry.path.read_text(encoding="utf-8")
    assert marked == [entry]
    assert "read_at: 2026-10-05\n---\n" in after
    # 本文と所感欄は 1 文字も変えない。
    assert after.split("---\n", 2)[2] == before.split("---\n", 2)[2]
    assert [item.title for item in reading.unread(reading.load(store))] == ["古い記事"]


def test_mark_read_keeps_the_first_day(store: ArticleStore) -> None:
    entry = reading.pick(reading.load(store), ["ccccccccccc"])[0]
    assert reading.mark_read(store, [entry], datetime.date(2026, 10, 5)) == []
    assert "read_at: 2026-10-01" in entry.path.read_text(encoding="utf-8")


def test_index_numbers_unread_like_the_command(store: ArticleStore) -> None:
    index = reading.render_index(reading.load(store))
    assert "未読 2 本 / 全 3 本" in index
    assert "1. 2026-09-20 [新しい記事](" in index
    assert "2. 2026-09-01 [古い記事](" in index


def test_index_leaves_out_read_articles(store: ArticleStore) -> None:
    # 既読まで載せると、記事が増えるほど目次が膨らみ続ける。
    assert "読んだ記事" not in reading.render_index(reading.load(store))


def test_index_links_survive_hashes_and_brackets(tmp_path: Path) -> None:
    # `#` はそのままだとリンクの途中で切れ、`[` `]` `(` `)` は Markdown の記法とぶつかる。
    store = ArticleStore(tmp_path)
    write_article(store, "aaaaaaaaaaa", "Lib #124 [x] (y)", "2026-09-01T12:00:00+00:00")
    index = reading.render_index(reading.load(store))
    assert "[Lib #124 \\[x\\] (y)](Lib-%23124-%5Bx%5D-%28y%29--aaaaaaaaaaa.md)" in index


def test_index_when_everything_is_read(tmp_path: Path) -> None:
    store = ArticleStore(tmp_path)
    write_article(store, "aaaaaaaaaaa", "読んだ記事", "2026-09-01T12:00:00+00:00", "2026-10-01")
    index = reading.render_index(reading.load(store))
    assert "未読 0 本 / 全 1 本" in index
    assert "全部読み終えている" in index


def test_index_without_articles() -> None:
    assert "未読 0 本 / 全 0 本" in reading.render_index([])
