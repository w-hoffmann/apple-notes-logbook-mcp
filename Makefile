# Developer entry points. Requires `uv` (brew install uv) and, for hooks,
# `lefthook` (brew install lefthook).

.PHONY: setup sync hooks lint format typecheck test check run

# One-shot bootstrap: sync deps and install git hooks.
setup: sync hooks

sync:
	uv sync

# Install the Lefthook git hooks (pre-commit / pre-push). Skips gracefully
# if lefthook is not installed yet.
hooks:
	@command -v lefthook >/dev/null 2>&1 && lefthook install || \
		echo "lefthook not found — run 'brew install lefthook' then 'make hooks'"

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run pyright

test:
	uv run pytest

# The full local gate, mirroring CI.
check: lint typecheck test

run:
	uv run apple-notes-logbook-mcp
