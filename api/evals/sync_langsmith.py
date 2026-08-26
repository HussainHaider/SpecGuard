"""Push the golden set to a LangSmith dataset.

    uv run python -m evals.sync_langsmith
    uv run python -m evals.sync_langsmith --dry-run

The JSONL files are the source of truth; this is a projection of them. Nothing reads
back from LangSmith into the repository, and nothing is authored in the LangSmith UI —
a dataset maintained in two places is a dataset that disagrees with itself, and the copy
that wins is whichever one someone happened to edit last.

The sync is idempotent and keyed on ``golden_id``: records are created, updated in place,
and deleted when they leave the files, so the dataset converges on the repository rather
than accumulating whatever every previous run left behind.
"""

from __future__ import annotations

import argparse
from typing import Any

from evals.golden import GoldenRetrieval, GoldenRule, load_retrieval, load_rules
from specguard.config import get_settings
from specguard.tracing import configure_tracing


def _rule_example(record: GoldenRule) -> dict[str, Any]:
    """One verdict record as a dataset example.

    Inputs are what a run is given; outputs are what it must produce. The defect and the
    provenance go in metadata rather than inputs — they explain the label, and a system
    under evaluation must not be handed the answer.
    """
    return {
        "inputs": {
            "spec_id": record.spec_id,
            "filename": record.filename,
            "rule_id": record.rule_id.value,
            "language": record.language.value,
        },
        "outputs": {"verdict": record.expected_verdict.value},
        "metadata": {
            "kind": "rule_verdict",
            "golden_id": record.golden_id,
            "adversarial": record.adversarial,
            "defect_kind": record.defect.kind if record.defect else None,
            "defect_detail": record.defect.detail if record.defect else None,
            **record.provenance.model_dump(mode="json"),
        },
        "split": record.split.value,
    }


def _retrieval_example(record: GoldenRetrieval) -> dict[str, Any]:
    return {
        "inputs": {
            "query": record.query,
            "rule_id": record.rule_id.value,
            "language": record.language.value,
        },
        "outputs": {
            "relevant_chunk_ids": record.relevant_chunk_ids,
            "relevant_references": record.relevant_references,
        },
        "metadata": {
            "kind": "retrieval",
            "golden_id": record.golden_id,
            "search_key": record.search_key,
            "spec_ids": record.spec_ids,
            **record.provenance.model_dump(mode="json"),
        },
        "split": record.split.value,
    }


def build_examples() -> list[dict[str, Any]]:
    """Every golden record, both files, as dataset examples."""
    return [
        *(_rule_example(record) for record in load_rules()),
        *(_retrieval_example(record) for record in load_retrieval()),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", help="Override LANGSMITH_DATASET.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be pushed without touching LangSmith.",
    )
    args = parser.parse_args()

    settings = get_settings()
    name = args.dataset or settings.langsmith_dataset
    examples = build_examples()

    if args.dry_run:
        by_kind: dict[str, int] = {}
        for example in examples:
            kind = str(example["metadata"]["kind"])
            by_kind[kind] = by_kind.get(kind, 0) + 1
        print(f"would sync {len(examples)} examples to {name!r}: {by_kind}")
        return

    if not configure_tracing(settings):
        parser.error(
            "LangSmith is not configured — set LANGSMITH_TRACING=true and "
            "LANGSMITH_API_KEY, or pass --dry-run."
        )

    from langsmith import Client

    client = Client()
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
    else:
        dataset = client.create_dataset(
            name,
            description=(
                "SpecGuard golden set. Source of truth is evals/golden/*.jsonl in the "
                "repository; this dataset is a projection and is overwritten by "
                "evals/sync_langsmith.py."
            ),
        )

    existing = {
        str((example.metadata or {}).get("golden_id")): example
        for example in client.list_examples(dataset_id=dataset.id)
    }
    wanted = {str(example["metadata"]["golden_id"]): example for example in examples}

    created = [example for key, example in wanted.items() if key not in existing]
    updated = [
        {"id": existing[key].id, **example} for key, example in wanted.items() if key in existing
    ]
    removed = [example for key, example in existing.items() if key not in wanted]

    if created:
        client.create_examples(dataset_id=dataset.id, examples=created)
    if updated:
        client.update_examples(dataset_id=dataset.id, updates=updated)
    for example in removed:
        # Deleted rather than left behind: a stale example is a label nobody wrote,
        # scoring runs against ground truth that no longer exists in the repository.
        client.delete_example(example.id)

    print(
        f"synced {len(wanted)} examples to {name!r}: "
        f"{len(created)} created, {len(updated)} updated, {len(removed)} deleted"
    )


if __name__ == "__main__":
    main()
