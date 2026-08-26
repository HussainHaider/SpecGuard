# SpecGuard

A compliance copilot for private-label food product specifications. A supplier spec sheet
(PDF) goes in; a per-rule **PASS / FAIL / NEEDS_REVIEW** report comes out, each verdict
backed by a citation to a specific article of EU food law.

Not affiliated with any retailer. The knowledge base is public EUR-Lex text; the
specification sheets are synthetic and authored in this repository.

---

## The one idea

Most of the interesting decisions in this project follow from a single rule:

> **No verdict without a resolvable citation.** If the retrieved evidence does not support
> a verdict, the rule returns `NEEDS_REVIEW`. Abstention is a feature.

"Resolvable" is enforced, not promised. A citation's `chunk_id` is a UUIDv5 derived from
`(regulation, article, paragraph, source_version)`, and `Citation` re-derives it on
construction — so a verdict cannot claim an article it did not retrieve, and a citation
written to Postgres still resolves against a freshly re-indexed Qdrant.

Two more that shape everything else:

- **Deterministic rules stay deterministic.** Arithmetic, field presence and keyword
  matching are Python. They are handed a `RuleContext` carrying no model client, so they
  have nothing to make a model call *with*.
- **Supplier PDFs are untrusted input.** Document text is a separate argument at the LLM
  boundary, wrapped and labelled as data. There is no free-text completion method, so
  every call is schema-constrained and no caller can bypass it.

## How a check runs

```
parse → extract → plan → check → verify → aggregate
```

`plan` selects the rules this document can actually answer — a product making no health
claim does not produce a health-claim finding. `check` runs eight rules: four pure Python,
four retrieval-backed. Each RAG rule retrieves from Qdrant (dense + sparse, native RRF
fusion), asks a judge for a verdict, then **verifies** it: the cited chunk must have been
retrieved, the chunk id must re-derive from the clause it names, the quoted span must
appear verbatim, and a second call must agree the span supports the verdict. Anything less
becomes `NEEDS_REVIEW` with a stated reason.

Full rule table and node contract: [`docs/plan.md`](docs/plan.md). Trade-offs taken along
the way, including the ones that went badly: [`docs/decisions.md`](docs/decisions.md).

## Evaluation

Two tiers, and the split between them is deliberate.

**Tier 1 is deterministic and gates CI.** Every number comes from comparing output to a
label written down in advance. It replays recorded fixtures — no network, no API key, no
cost — so anyone can reproduce it from a clone, which is what makes it fit to fail a build.

**Tier 2 is judged and never blocks a merge.** It scores what tier 1 cannot reach: whether
a suggested fix is actionable, whether a rationale stays inside the clause it cites. A
judged number moves when the judge changes, and a build that fails because a vendor
shipped a new checkpoint teaches everyone to ignore the build.

The golden set — `evals/golden/rules.jsonl` (80 verdict labels) and
`evals/golden/retrieval.jsonl` (58 queries) — is the single source of truth. It is
projected into a LangSmith dataset by `evals/sync_langsmith.py` and read into deepeval
through its own loader; it is never authored in two places.

### Tier 1 — offline, replayed fixtures

Baseline is M5, the milestone that first measured these. Dev and held-out are reported
separately and never averaged into one another.

| metric | baseline | current | dev | held-out |
|---|---|---|---|---|
| accuracy | 88.8% | 88.8% | 85.7% | 92.1% |
| abstention rate | 11.2% | 11.2% | 14.3% | 7.9% |
| allergen FNR | 0.0% | 0.0% | 0.0% | 0.0% |
| false passes | 0 | 0 | 0 | 0 |
| wrong verdicts | 0 | 0 | 0 | 0 |
| citation resolution | 100.0% | 100.0% | 100.0% | 100.0% |
| recall@5 | 57.2% | 57.2% | 56.4% | 58.8% |
| hit rate@5 | 75.9% | 75.9% | 71.8% | 84.2% |
| p50 / p95 latency | — | — | — | — |
| cost per spec | $0.0313 | $0.0313 | $0.0304 | $0.0321 |

| rule | all | dev | held-out |
|---|---|---|---|
| `ALLERGEN_EMPHASIS` | 10/10 | 5/5 | 5/5 |
| `HEALTH_CLAIM_AUTHORISED` | 10/10 | 6/6 | 4/4 |
| `LEGAL_NAME_AND_QUID` | 5/12 | 2/6 | 3/6 |
| `MANDATORY_FIELDS` | 12/12 | 6/6 | 6/6 |
| `NUTRITION_ARITHMETIC` | 10/10 | 6/6 | 4/4 |
| `NUTRITION_CLAIM_CONDITIONS` | 9/10 | 4/5 | 5/5 |
| `NUTRITION_PER_100` | 8/8 | 4/4 | 4/4 |
| `ORIGIN_DECLARATION` | 7/8 | 3/4 | 4/4 |

Regenerate with `uv run python -m evals.run_eval --markdown`. Gates live in
[`evals/thresholds.toml`](api/evals/thresholds.toml) so moving one shows up in a diff.

### What these numbers do and do not say

- **Accuracy counts an abstention as wrong**, and abstention rate is printed beside it.
  A system can reach high accuracy by declining everything hard; the two numbers together
  show whether it did. `LEGAL_NAME_AND_QUID` at 5/12 is that failure mode in the open —
  it asks two questions at once whose answers live in different clauses, and it abstains
  on most of them. See [`docs/decisions.md`](docs/decisions.md) 011.
- **Allergen FNR is measured on six cases.** With that n it is really a binary gate — "no
  allergen failure was missed" — and the rule that catches them is deterministic Python,
  so passing it is close to free. It is reported honestly rather than dressed up as a rate.
- **recall@5 of 57% is reported, never gated.** The relevant-clause labels are a judgement
  about which article decides a question, written out explicitly in `evals/build_golden.py`
  and checked to exist in the corpus. A build should not fail on a labelling opinion. The
  hit rate — at least one relevant clause in the top 5, which is the threshold that
  actually decides whether a rule can be answered — is 76%.
- **Latency is blank because a replay has none.** Reporting the replay's own microseconds
  would claim this system answers instantly. Real figures come from `--live`.
- **Cost is real money** — recorded token counts priced at the model that produced them,
  for a complete eight-rule check.

### Qdrant vs pgvector

`PgVectorStore` sits behind the same `VectorStore` protocol, and
`evals/benchmark_retrieval.py` scores both stores on the golden retrieval set. **The
benchmark has not been run** — see [`docs/decisions.md`](docs/decisions.md) 018 — so no
numbers are quoted here. It needs `docker compose up -d db qdrant` and one command.

Worth knowing before reading any result: this is not the same search on different storage.
Qdrant's lexical half is a bm25 vector from fastembed; Postgres has no bm25, so it is
`ts_rank_cd` over a `tsvector`. Fusion is server-side RRF in Qdrant and hand-written SQL in
Postgres. At 734 clauses a latency difference would be noise either way.

## Observability

Every model call is traced with prompt version, rule id, tokens, cost and latency; one
span per graph node, one per rule. The run id travels back on the result and is stored
beside the verdict in Postgres, so `POST /checks/{id}/feedback` attaches a reviewer's
correction to the run that produced the verdict — months later, from a stored row.

Tracing is off unless `LANGSMITH_TRACING` and `LANGSMITH_API_KEY` are both set, and while
it is off nothing in the process imports the langsmith client or touches the network.

## Running it

```bash
cp .env.example .env            # LLM_PROVIDER=fake needs no key
docker compose up -d            # postgres, redis, qdrant, api, worker, web
uv run --project api alembic upgrade head
uv run --project api python -m specguard.corpus.fetch   # EUR-Lex text
uv run --project api python -m specguard.corpus.seed    # index into Qdrant
```

```bash
cd api
uv run pytest                                    # offline, no key, excludes judged evals
uv run mypy && uv run ruff check .
uv run python -m evals.run_eval                  # tier 1, offline
uv run python -m evals.run_eval --live           # tier 1, real Qdrant and provider
uv run pytest evals/ -m slow                     # tier 2, judged — costs money
uv run python -m evals.build_golden              # rebuild the golden set from the manifest
```

## Stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 · LangGraph · LangSmith · Qdrant ·
Postgres 16 · arq · fastembed (local ONNX, no embedding API calls) · React 18 + Vite ·
pytest + deepeval · uv, ruff, mypy --strict · Docker Compose · GitHub Actions
