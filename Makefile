.PHONY: help install run dry-run setup-mac member-new member-export clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install Python dependencies
	pip install feedparser

run: ## Run arXiv daily digest (dry-run, no email)
	python3 Arxiv_filter.py

send: ## Run arXiv daily digest + send email
	python3 Arxiv_filter.py --send

setup-mac: ## Install macOS launchd scheduled task
	./setup.zsh

setup-win: ## Install Windows scheduled task (run in admin PowerShell)
	@echo "Run: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned; .\setup.ps1"

# --- Members ---

member-new: ## Scaffold a new member workspace. Usage: make member-new NAME=zhangsan
	@test -n "$(NAME)" || (echo "Usage: make member-new NAME=<name>"; exit 1)
	@test ! -d members/$(NAME) || (echo "members/$(NAME) already exists"; exit 1)
	cp -r members/_template members/$(NAME)
	@echo "Created members/$(NAME)/"

member-export: ## Export a member's personal content. Usage: make member-export NAME=zhangsan
	@test -n "$(NAME)" || (echo "Usage: make member-export NAME=<name>"; exit 1)
	./offboarding/extract.sh $(NAME)

# --- Paper notes ---

note-new: ## Create a new paper note from template. Usage: make note-new NAME=zhangsan FILE=作者-关键词
	@test -n "$(NAME)" || (echo "Usage: make note-new NAME=<name> FILE=<author-keyword>"; exit 1)
	@test -n "$(FILE)" || (echo "Usage: make note-new NAME=<name> FILE=<author-keyword>"; exit 1)
	cp paper-notes/template.md members/$(NAME)/paper-notes/2026/$(FILE).md
	@echo "Created members/$(NAME)/paper-notes/2026/$(FILE).md — go fill it in."

# --- Maintenance ---

download: ## Download PDFs from daily_digest.md. Opts: make download ARGS="--dest /path --dry-run"
	python3 download_papers.py $(ARGS)

clean: ## Remove generated files
	rm -f daily_digest.md
	rm -f launchd_stdout.log launchd_stderr.log
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
