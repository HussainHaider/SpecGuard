# Task runner. `just` with no argument lists everything.
#
# These are wrappers, not abstractions: each one is a command you could type, kept here
# so the flags that matter — --separate, the /workflows path — cannot be forgotten.

default:
    @just --list

# --- n8n ---------------------------------------------------------------------

# Export every workflow to n8n/ as one JSON file each, for committing.
#
# --separate writes one file per workflow rather than a single array, so a diff shows
# which workflow changed instead of one enormous blob. Credentials are deliberately not
# exported: they are encrypted with N8N_ENCRYPTION_KEY, they belong to a machine rather
# than to the repository, and n8n recreates them from environment on first run.
n8n-export:
    docker compose exec n8n n8n export:workflow --all --separate --output=/workflows
    @echo "Exported to n8n/. Review the diff before committing."

# Import the committed workflows into a running n8n.
#
# Idempotent by workflow id: re-importing updates in place rather than duplicating, so
# a clean clone and an existing instance converge on the same state.
n8n-import:
    docker compose exec n8n n8n import:workflow --separate --input=/workflows
    @echo "Imported from n8n/. Activate the workflows in the editor."

# --- stack -------------------------------------------------------------------

# Everything: datastores, API, worker, web, n8n.
up:
    docker compose --profile app up -d --build

down:
    docker compose --profile app down

# Index the regulation corpus into Qdrant. The one command a clean clone needs after
# `just up`; the corpus text itself is committed, so nothing is downloaded.
seed:
    docker compose exec api python -m specguard.corpus.seed

logs service="api":
    docker compose logs -f {{service}}

# --- checks ------------------------------------------------------------------

test:
    cd api && uv run pytest -m "not slow"
    cd web && npm run test

lint:
    cd api && uv run ruff format --check . && uv run ruff check . && uv run mypy
    cd web && npm run typecheck

# Tier 1: deterministic, offline, gates CI.
eval:
    cd api && uv run python -m evals.run_eval --fail-under-config

# Tier 2: judged by a model, reported only, costs money.
eval-judged:
    cd api && uv run pytest evals/test_generation_quality.py -m slow

# Regenerate the report the /ops page reads.
eval-report:
    cd api && uv run python -m evals.run_eval --json
