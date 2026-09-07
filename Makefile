# タスクの一覧。`make` だけで説明が出る。
#
# API キーは .env から読む（.gitignore 済み）。各タスクが自分で読み込むので、
# 呼ぶ前に set -a && source .env する必要はない。

SHELL := /bin/sh
.DEFAULT_GOAL := help

# .env を読んでから uv run する。**タスクごとに書かないための共通部分。**
RUN = set -a && . ./.env && set +a && uv run youtube-scribe

.PHONY: help
help: ## このヘルプを出す
	@grep -hE '^[a-z-]+:.*##' $(MAKEFILE_LIST) \
		| sed -E 's/^([a-z-]+):.*## (.*)/  \1|\2/' \
		| awk -F'|' '{printf "  make %-12s %s\n", $$1, $$2}'
	@echo
	@echo '  URL=... で対象を渡す。ARGS=... で追加のオプションを渡す。'
	@echo '  例: make scribe URL="https://www.youtube.com/playlist?list=PL..."'
	@echo '      make scribe URL="..." ARGS="--limit 3 --no-frames"'

.PHONY: scribe
scribe: guard-URL ## 記事を作る（URL=... 必須）
	@$(RUN) "$(URL)" $(ARGS)

.PHONY: dry-run
dry-run: guard-URL ## 対象を並べるだけ。API は叩かない（URL=... 必須）
	@$(RUN) "$(URL)" --dry-run $(ARGS)

# 記事の末尾にある所感欄。**空のままなら、まだ読んでいない。**
# ツールはここを埋めない（README・CONTEXT.md にそう書いてある）ので、
# 埋まっていること自体が人が読んだ証拠になる。新しい状態を持たずに済む。
PLACEHOLDER = <!-- ここは自分で書く -->

.PHONY: unread
unread: ## まだ読んでいない記事を並べる（所感欄が空のもの）
	@files=$$(grep -l '$(PLACEHOLDER)' articles/*.md 2>/dev/null); \
	total=$$(ls articles/*.md 2>/dev/null | wc -l | tr -d ' '); \
	count=$$(printf '%s' "$$files" | grep -c . || true); \
	if [ -z "$$files" ]; then \
		echo "全部読み終えている（$$total 本）"; \
	else \
		echo "$$files" | sed -E 's|articles/||; s|--[A-Za-z0-9_-]{11}\.md$$||' \
			| nl -w3 -s'. '; \
		echo; \
		echo "未読 $$count 本 / 全 $$total 本"; \
	fi

.PHONY: read
read: ## 読み終えた記事を並べる（所感欄が埋まっているもの）
	@files=$$(grep -L '$(PLACEHOLDER)' articles/*.md 2>/dev/null); \
	if [ -z "$$files" ]; then \
		echo "まだ 1 本も読み終えていない"; \
	else \
		echo "$$files" | sed -E 's|articles/||; s|--[A-Za-z0-9_-]{11}\.md$$||' | nl -w3 -s'. '; \
	fi

.PHONY: check
check: ## 整形・lint・型・テストを全部
	@sh tools/check.sh

.PHONY: golden
golden: ## 記事の形を変えたときにゴールデンを更新する
	@GOLDEN_UPDATE=1 uv run pytest tests/test_article.py
	@echo '差分を目で見てからコミットすること。'

.PHONY: sync
sync: ## 依存を入れ直す（証明書エラーが出る環境向けに --system-certs つき）
	@uv sync --system-certs

# URL のような必須変数が無いときに、意味の分かる形で止める。
.PHONY: guard-%
guard-%:
	@test -n "$($*)" || { \
		echo "$* を指定してください。例: make $(MAKECMDGOALS) $*=\"https://...\""; \
		exit 1; \
	}
