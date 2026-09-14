.PHONY: sync lint format typecheck test check lock

sync:
	uv sync --group dev

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy

test:
	uv run pytest

check:
	uv run ruff check .
	uv run mypy
	uv run pytest

lock:
	uv lock
