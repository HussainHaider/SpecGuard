"""Tier 1 eval: deterministic ground-truth scoring over the golden set. Gates CI.

The golden set decides what is measured — the pipeline runs only the (spec, rule) pairs
``evals/golden/rules.jsonl`` asks about, and retrieval is scored only on the queries in
``evals/golden/retrieval.jsonl``. Nothing here judges anything with a model.

Two paths, same code:

* **offline** (default) — retrieval and every model call are replayed from recorded
  fixtures. No network, no API key, no cost. This is what CI runs when no secrets are
  present, and what makes the number in the README reproducible from a clone.
* **live** — real Qdrant, real provider. Slower, costs money, and is how the latency
  figures are actually measured; a replay cannot invent them.

    uv run python -m evals.run_eval
    uv run python -m evals.run_eval --live
    uv run python -m evals.run_eval --fail-under-config
    uv run python -m evals.run_eval --markdown
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

import evals.metrics as metrics
import evals.pipeline as pipeline
from evals.golden import load_retrieval, load_rules
from specguard.config import Settings, get_settings
from specguard.corpus.seed import load_clauses
from specguard.llm.factory import FIXTURE_DIR, build_client
from specguard.llm.fake import FakeClient
from specguard.llm.protocol import LLMClient
from specguard.models.rule import RuleId
from specguard.vectorstore.fixtures import FixtureStore
from specguard.vectorstore.protocol import VectorStore

SEARCH_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "searches"
THRESHOLDS = Path(__file__).resolve().parent / "thresholds.toml"


def build_stores(settings: Settings, *, live: bool) -> tuple[VectorStore, LLMClient]:
    """The store and client for this mode.

    The offline client prices replayed calls at the model that recorded them, so the
    spend a replay reports is real money that was really spent, not a zero.
    """
    if not live:
        return (
            FixtureStore(SEARCH_FIXTURES),
            FakeClient(FIXTURE_DIR, model="recorded", price_model=settings.openai_model),
        )

    from qdrant_client import QdrantClient

    from specguard.embedding.encoder import Encoder
    from specguard.vectorstore.qdrant import QdrantVectorStore

    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=60)
    store = QdrantVectorStore(
        client,
        Encoder(settings.dense_embedding_model, settings.sparse_embedding_model),
        collection=settings.qdrant_collection,
    )
    return store, build_client(settings)


def score_rules(store: VectorStore, client: LLMClient, settings: Settings) -> list[metrics.Scored]:
    """Run the pipeline over exactly the pairs the golden set labels."""
    golden = load_rules()
    wanted = {(record.spec_id, record.rule_id) for record in golden}
    report = pipeline.run(store, client, retrieval_limit=settings.retrieval_top_k, only=wanted)

    produced = {(outcome.spec_id, outcome.rule_id): outcome.result for outcome in report.outcomes}
    scored: list[metrics.Scored] = []
    for record in golden:
        result = produced.get((record.spec_id, record.rule_id))
        if result is None:
            # A golden record the pipeline produced nothing for is a broken eval, not a
            # zero. Silently scoring it as wrong would hide the wiring fault.
            raise RuntimeError(
                f"{record.golden_id}: the pipeline produced no result for "
                f"{record.spec_id}/{record.rule_id.value}"
            )
        scored.append(metrics.Scored(golden=record, result=result))
    return scored


def full_check_cost(
    store: VectorStore, client: LLMClient, settings: Settings, spec_ids: set[str]
) -> dict[str, float]:
    """What a complete eight-rule check costs, per specification.

    Run separately from the scored records because the golden set samples rules, and a
    cost taken over a sample answers a question nobody asked.
    """
    report = pipeline.run(
        store,
        client,
        retrieval_limit=settings.retrieval_top_k,
        only={(spec_id, rule_id) for spec_id in spec_ids for rule_id in RuleId},
    )
    costs: dict[str, float] = dict.fromkeys(spec_ids, 0.0)
    for outcome in report.outcomes:
        costs[outcome.spec_id] += outcome.cost_usd
    return costs


def score_retrieval(store: VectorStore, settings: Settings) -> list[metrics.RetrievalScored]:
    """Replay every golden query and record what came back."""
    scored: list[metrics.RetrievalScored] = []
    for record in load_retrieval():
        hits = store.search(record.query, language=record.language, limit=settings.retrieval_top_k)
        scored.append(
            metrics.RetrievalScored(golden=record, retrieved=[hit.clause.chunk_id for hit in hits])
        )
    return scored


def run(*, live: bool = False) -> dict[str, metrics.Metrics]:
    """Score the golden set and return metrics for the combined set and each split."""
    settings = get_settings()
    store, client = build_stores(settings, live=live)
    known = {clause.chunk_id for clause in load_clauses(settings.corpus_dir)}
    scored = score_rules(store, client, settings)
    return metrics.by_split(
        scored,
        score_retrieval(store, settings),
        known_chunk_ids=known,
        spec_cost=full_check_cost(
            store, client, settings, {item.golden.spec_id for item in scored}
        ),
    )


def check_gates(measured: metrics.Metrics) -> list[str]:
    """Compare the combined split against the committed thresholds."""
    gates = tomllib.loads(THRESHOLDS.read_text(encoding="utf-8"))["gates"]
    failures: list[str] = []

    if measured.accuracy < gates["min_accuracy"]:
        failures.append(
            f"accuracy {measured.accuracy:.1%} is below the {gates['min_accuracy']:.0%} floor"
        )
    if measured.false_passes > gates["max_false_passes"]:
        failures.append(f"{measured.false_passes} non-compliant spec(s) reported as compliant")
    if measured.wrong_verdicts > gates["max_wrong_verdicts"]:
        failures.append(
            f"{measured.wrong_verdicts} wrong verdicts, above the "
            f"{gates['max_wrong_verdicts']} allowed"
        )
    fnr = measured.allergen_fnr
    if fnr is not None and fnr > gates["max_allergen_false_negative_rate"]:
        failures.append(
            f"allergen false-negative rate {fnr:.1%} is above "
            f"{gates['max_allergen_false_negative_rate']:.0%}"
        )
    resolution = measured.citation_resolution_rate
    if resolution is not None and resolution < gates["min_citation_resolution"]:
        failures.append(
            f"citation resolution {resolution:.1%} is below "
            f"{gates['min_citation_resolution']:.0%} — a verdict is citing a clause that "
            "does not resolve against the corpus"
        )
    return failures


def _cell(value: float | None, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "pct":
        return f"{value:.1%}"
    if kind == "usd":
        return f"${value:.4f}"
    return f"{value:.0f}"


def _baseline() -> dict[str, float]:
    """The committed reference figures for the combined split."""
    config = tomllib.loads(THRESHOLDS.read_text(encoding="utf-8"))
    baseline = config.get("baseline", {}).get("all", {})
    return {key: float(value) for key, value in baseline.items()}


def markdown(results: dict[str, metrics.Metrics]) -> str:
    """The README table: the committed baseline beside the current figure, per split."""
    rows: list[tuple[str, str, str]] = [
        ("accuracy", "pct", "accuracy"),
        ("abstention rate", "pct", "abstention_rate"),
        ("allergen FNR", "pct", "allergen_fnr"),
        ("false passes", "raw", "false_passes"),
        ("wrong verdicts", "raw", "wrong_verdicts"),
        ("citation resolution", "pct", "citation_resolution_rate"),
        ("recall@5", "pct", "recall_at_5"),
        ("hit rate@5", "pct", "hit_rate_at_5"),
        ("p50 latency (ms)", "raw", "p50_latency_ms"),
        ("p95 latency (ms)", "raw", "p95_latency_ms"),
        ("cost per spec", "usd", "cost_per_spec_usd"),
    ]
    splits = ["all", "dev", "held_out"]
    baseline = _baseline()
    lines = [
        "| metric | baseline | current | dev | held-out |",
        "|---|---|---|---|---|",
    ]
    for label, kind, attribute in rows:
        cells = [_cell(getattr(results[split], attribute), kind) for split in splits]
        reference = baseline.get(attribute)
        lines.append(
            f"| {label} | {_cell(reference, kind) if reference is not None else '—'} | "
            + " | ".join(cells)
            + " |"
        )

    per_rule = [
        "",
        "| rule | all | dev | held-out |",
        "|---|---|---|---|",
    ]
    for rule_id in sorted(RuleId, key=lambda r: r.value):
        cells = []
        for split in splits:
            correct, total = results[split].per_rule.get(rule_id, (0, 0))
            cells.append(f"{correct}/{total}" if total else "—")
        per_rule.append(f"| `{rule_id.value}` | " + " | ".join(cells) + " |")
    return "\n".join([*lines, *per_rule])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use real Qdrant and a real provider instead of recorded fixtures.",
    )
    parser.add_argument(
        "--fail-under-config",
        action="store_true",
        help="Exit non-zero if the combined split is below evals/thresholds.toml.",
    )
    parser.add_argument("--markdown", action="store_true", help="Print the README table.")
    args = parser.parse_args()

    results = run(live=args.live)

    if args.markdown:
        print(markdown(results))
        return

    print(f"mode: {'live' if args.live else 'offline (replayed fixtures, no network)'}\n")
    for split in ("all", "dev", "held_out"):
        print(metrics.render(results[split]))
        print()

    if not args.fail_under_config:
        return

    failures = check_gates(results["all"])
    if failures:
        print("FAILED: " + "; ".join(failures), file=sys.stderr)
        raise SystemExit(1)
    print("gates passed")


if __name__ == "__main__":
    main()
