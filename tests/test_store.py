"""保存先 — **「記事が存在する＝処理が成功した」という不変条件**を守る側。"""

from pathlib import Path

import pytest

from youtube_scribe.store import ArticleStore, slugify


def test_slugify_keeps_japanese() -> None:
    # 一覧で中身が読めることが目的なので、ローマ字化はしない。
    assert slugify("Rust の所有権とは") == "Rust-の所有権とは"


def test_slugify_removes_path_separators() -> None:
    assert "/" not in slugify("a/b:c")
    assert slugify("a/b:c") == "abc"


def test_slugify_collapses_whitespace_including_ideographic_space() -> None:
    assert slugify("a　b  c") == "a-b-c"


def test_slugify_truncates_long_titles() -> None:
    assert len(slugify("あ" * 200)) == 60


def test_slugify_never_returns_empty() -> None:
    assert slugify("///") == "untitled"
    assert slugify("   ") == "untitled"


def test_has_matches_on_video_id_not_title(tmp_path: Path) -> None:
    store = ArticleStore(tmp_path)
    store.save("abcdefghijk", "元のタイトル", "本文")
    assert store.has("abcdefghijk")
    # タイトルが変わっても、同じ動画だと分かる必要がある。
    existing = store.existing("abcdefghijk")
    assert existing is not None
    assert store.article_path("abcdefghijk", "変わったタイトル").name != existing.name
    assert store.has("abcdefghijk")


def test_has_is_false_for_unknown_video(tmp_path: Path) -> None:
    assert ArticleStore(tmp_path).has("zzzzzzzzzzz") is False


def test_save_writes_the_whole_file(tmp_path: Path) -> None:
    store = ArticleStore(tmp_path)
    path = store.save("abcdefghijk", "題", "本文\n")
    assert path.read_text(encoding="utf-8") == "本文\n"
    assert path.name == "題--abcdefghijk.md"


def test_save_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    store = ArticleStore(tmp_path)
    store.save("abcdefghijk", "題", "本文")
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_creates_the_directory(tmp_path: Path) -> None:
    store = ArticleStore(tmp_path / "articles")
    path = store.save("abcdefghijk", "題", "本文")
    assert path.exists()


def test_asset_paths(tmp_path: Path) -> None:
    store = ArticleStore(tmp_path)
    assert store.asset_dir("abcdefghijk") == tmp_path / "assets" / "abcdefghijk"
    assert store.asset_prefix("abcdefghijk") == "assets/abcdefghijk"


def test_discard_assets_removes_orphaned_images(tmp_path: Path) -> None:
    store = ArticleStore(tmp_path)
    directory = store.asset_dir("abcdefghijk")
    directory.mkdir(parents=True)
    (directory / "10.jpg").write_bytes(b"x")

    store.discard_assets("abcdefghijk")
    assert not directory.exists()


def test_discard_assets_is_safe_when_nothing_exists(tmp_path: Path) -> None:
    ArticleStore(tmp_path).discard_assets("abcdefghijk")


@pytest.mark.parametrize("title", ["a" * 300, "…", "1/2 の話"])
def test_article_path_is_always_usable(tmp_path: Path, title: str) -> None:
    store = ArticleStore(tmp_path)
    path = store.save("abcdefghijk", title, "本文")
    assert path.exists()
    assert store.has("abcdefghijk")
