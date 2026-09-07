#!/bin/sh
# 整形・lint・型・テストを一息で回す。CI は無いので、これが最後の砦。
set -eu
cd "$(dirname "$0")/.."

echo "--- ruff format"
uv run ruff format --check .
echo "--- ruff check"
uv run ruff check .
echo "--- mypy (strict)"
uv run mypy
echo "--- pytest"
uv run pytest -q
