# Convenience targets. Every target has a plain command equivalent in the README.
PY ?= python

.PHONY: install dev lint format test secrets hooks ingest import process serve demo evals reset

install:
	$(PY) -m pip install -e .

dev:
	$(PY) -m pip install -e ".[dev]"

lint:
	ruff check . && ruff format --check .

format:
	ruff format . && ruff check --fix .

test:
	pytest -q

secrets:
	$(PY) scripts/check_secrets.py

hooks:
	printf '#!/bin/sh
exec python scripts/check_secrets.py --staged
' > .git/hooks/pre-commit
	@echo "pre-commit hook installed: commits that contain something like a key are refused"

ingest:
	inboxdraft ingest

import:
	inboxdraft import

process:
	inboxdraft process

serve:
	inboxdraft serve

demo:
	inboxdraft demo

evals:
	inboxdraft evals

reset:
	inboxdraft reset --yes
