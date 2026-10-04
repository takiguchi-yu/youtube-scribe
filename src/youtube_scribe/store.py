"""保存先 — `articles/` への読み書きと、処理済み判定。

**このモジュールだけが不変条件を守る責任を負う。**

    記事ファイルが存在する ＝ その動画の処理は成功している

だから書き込みは一時ファイル経由で行い、最後に置き換える。途中で落ちても
中途半端な記事が残らない。残ってしまうと、その動画は二度と作り直されない。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

# ファイル名に使えない文字と、macOS / Finder で扱いにくい文字。
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_SPACE = re.compile(r"[\s　]+")

_SEPARATOR = "--"
_MAX_SLUG = 60

# 記事のファイル名。末尾が `--` と 11 桁の動画ID。目次などの他の .md と区別する。
_ARTICLE_NAME = re.compile(rf"{_SEPARATOR}[A-Za-z0-9_-]{{11}}\.md$")

# 目次。GitHub はディレクトリにある README.md をファイル一覧の下に表示する。
_INDEX_NAME = "README.md"


def slugify(title: str) -> str:
    """タイトルをファイル名に使える形にする。**日本語はそのまま残す。**

    一覧で中身が読めることが目的なので、ローマ字化はしない。
    """
    text = _UNSAFE.sub("", title)
    text = _SPACE.sub("-", text.strip())
    text = text.strip("-. ")[:_MAX_SLUG].strip("-. ")
    return text or "untitled"


class ArticleStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def has(self, video_id: str) -> bool:
        """その動画の記事が既にあるか。

        **タイトルは投稿者があとから変えられる**ので、末尾の動画ID だけで照合する。
        """
        return any(self.root.glob(f"*{_SEPARATOR}{video_id}.md"))

    def existing(self, video_id: str) -> Path | None:
        return next(iter(sorted(self.root.glob(f"*{_SEPARATOR}{video_id}.md"))), None)

    def article_path(self, video_id: str, title: str) -> Path:
        return self.root / f"{slugify(title)}{_SEPARATOR}{video_id}.md"

    def articles(self) -> list[Path]:
        """記事ファイルをすべて並べる。**目次は記事ではないので含めない。**"""
        if not self.root.is_dir():
            return []
        return sorted(path for path in self.root.glob("*.md") if _ARTICLE_NAME.search(path.name))

    def index_path(self) -> Path:
        """目次の置き場所。記事から毎回作り直すので、消えても困らない。"""
        return self.root / _INDEX_NAME

    def asset_dir(self, video_id: str) -> Path:
        return self.root / "assets" / video_id

    def asset_prefix(self, video_id: str) -> str:
        """記事から画像を参照するときの相対パス。"""
        return f"assets/{video_id}"

    def discard_assets(self, video_id: str) -> None:
        """記事にならなかった画像を片付ける。

        記事を書かずに終わった動画の画像が残ると、次の実行で撮り直したものと
        混ざる。**成果物は記事だけ**なので、記事が出ないなら画像も残さない。
        """
        shutil.rmtree(self.asset_dir(video_id), ignore_errors=True)

    def cache_path(self, video_id: str) -> Path:
        """書き起こしの控え。**成果物ではなく、ただの高速化である。**

        消しても記事の作られ方は変わらない。あるのは、要約が失敗して再実行に
        なったときに YouTube を何度も叩かないため。失敗が続くほど YouTube を
        追い込む、という事故を実際に起こした。
        """
        return self.root / ".cache" / f"{video_id}.json"

    def save(self, video_id: str, title: str, markdown: str) -> Path:
        """記事を書く。**書き終わってから初めてファイルが存在する。**"""
        path = self.article_path(video_id, title)
        self.write(path, markdown)
        return path

    def write(self, path: Path, markdown: str) -> None:
        """一時ファイル経由で書き、最後に置き換える。

        既にある記事を書き換えるとき（既読の印など）もここを通す。途中で落ちて
        中身が欠けた記事が残ると、「存在する＝成功」のせいで二度と作り直されない。
        """
        self.root.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.root, suffix=".md.tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(markdown)
                stream.flush()
                os.fsync(stream.fileno())
            Path(temporary).replace(path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
