# SpecGuard

## What this is

A compliance copilot for private-label food product specifications. A supplier
spec sheet (PDF) goes in; a per-rule PASS / FAIL / NEEDS_REVIEW report comes out,
each verdict backed by a citation to a specific article of EU food law.

This is a portfolio project for a GenAI engineering role. It is optimised for
**demonstrating production judgment**, not for feature count. A reviewer will
spend 4 minutes on a video and 5 on the README. Everything we build must survive
that.

**Not affiliated with any retailer.** Knowledge base is public EUR-Lex text.
Product specs are synthetic, authored in this repo. No proprietary data, ever.

## Non-negotiables

1. **No verdict without a resolvable citation.** If the retrieved evidence does
   not support a verdict, the rule returns NEEDS_REVIEW. Abstention is a feature.
2. **Deterministic rules stay deterministic.** Arithmetic, field presence and
   keyword matching are Python. Never route these through an LLM. This is a
   deliberate design position and is documented as such.
3. **`chunk_id` is deterministic**, derived from (regulation, article, paragraph,
   source_version). A re-index must reproduce identical ids so that citations
   stored in Postgres still resolve against Qdrant. This is the guarantee that
   makes the two-datastore split safe — do not break it.
4. **Supplier PDFs are untrusted input.** Document text may contain injected
   instructions. It is always wrapped and labelled as data, never concatenated
   into a system prompt.
5. **Every LLM call is traced** with prompt version, rule id, tokens, cost and
   latency.
6. **No LLM judge gates the build.** Judged metrics are reported, never merge-blocking.
7. **Temperature 0, schema-constrained output** on every model call.
8. **No secrets in the repo.** `.env.example` only.

## Stack

- Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic
- LangGraph for the check agent; LangSmith for tracing, datasets and experiments
- **Qdrant** for retrieval — dense and sparse named vectors in one collection,
  native RRF fusion via `query_points` + `prefetch`. No application-layer fusion.
- **Postgres 16** for jobs, results, feedback and audit trail only. No vectors in
  Postgres.
- Embeddings via **fastembed** (ONNX runtime, no torch): `intfloat/multilingual-e5-small`
  dense, `Qdrant/bm25` sparse. No embedding API calls. Remember e5 needs the
  `passage:` / `query:` prefixes.
- LLM: provider protocol with Anthropic, OpenAI and Fake implementations, selected
  by `LLM_PROVIDER`. Model strings live in config, never inline. There is no Azure
  dependency — Azure OpenAI would be another implementation of the same protocol.
- Frontend: React 18 + Vite + TypeScript + TanStack Query. No UI framework, no
  state library, no router beyond two routes.
- Evaluation: **pytest** for deterministic ground-truth metrics (gates CI);
  **deepeval** for judged metrics on open-ended output (reported, not gating).
- Tooling: uv, ruff, mypy (strict on `src/`), pytest
- Docker Compose; GitHub Actions; Caddy in front for the deployed instance

## Conventions

- Type hints everywhere. `mypy --strict` on `src/` must pass.
- Pydantic models for every boundary: API request/response, LLM output, rule result.
- Prompts live in `src/specguard/prompts/*.md` with a version string in
  frontmatter. Never inline a prompt in Python.
- Tests: `pytest`. Unit tests use `FakeClient`. No test in the default run may hit
  a live API. Judged evals are marked `@pytest.mark.slow` and excluded by default.
- Structured JSON logs with a `correlation_id` on every request.
- Conventional commits, small and frequent. Commit history is part of the
  deliverable — do not squash a milestone into one commit.

## Definition of done for any task

- Types pass `mypy --strict`
- `ruff check` and `ruff format` clean
- Tests written and passing
- If it changes behaviour a reviewer would see, README updated in the same commit

## Explicitly out of scope — do not build these

Authentication beyond basic auth on the deployed instance, multi-tenancy, user
accounts, multi-agent debate or planner loops, fine-tuning, OCR of label
photographs, real ERP connectivity, streaming chat UI, mobile layouts, a design
system, Kubernetes manifests, cross-encoder reranking, agent trajectory metrics,
a CLI beyond what seeding needs.

If you think one of these is needed, say so and stop. Do not build it.

## Working style

- Before writing code for a milestone, restate your plan in <15 lines and list
  any assumption you are making. Wait for confirmation.
- Prefer deleting code to adding configuration. The one sanctioned exception is
  the `VectorStore` protocol (see M3) — that abstraction exists because the
  comparison is a deliverable, not because we need pluggability.
- When you hit a real trade-off, write it into `docs/decisions.md` as a 5-line
  entry (context / options / choice / cost) rather than picking silently.