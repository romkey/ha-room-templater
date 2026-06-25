.PHONY: install generate test lint help

help:
	@echo "Targets:"
	@echo "  install   Create venv and install dependencies"
	@echo "  generate  Run gen_templates.py (requires .env)"
	@echo "  test      Run unit and functional tests (pytest)"
	@echo "  lint      Run ruff check and format check"

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	.venv/bin/pip install -r requirements-dev.txt
	@test -f .env || cp .env.example .env
	@echo "Edit .env with HA_URL and HA_TOKEN, then: make generate"

generate:
	.venv/bin/python gen_templates.py

test:
	.venv/bin/pytest tests/ -v

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
