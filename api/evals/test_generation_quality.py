"""Tier 2: judged metrics over open-ended output. Reported, never merge-blocking.

Non-negotiable #6 — no LLM judge gates the build. These are marked ``slow`` and excluded
from the default run, and the nightly workflow that runs them posts a report rather than
failing a merge. The reason is not squeamishness about model judges: it is that a judged
number moves when the judge changes, and a build that fails because a vendor shipped a
new checkpoint teaches everyone to ignore the build.

What is judged here is what tier 1 cannot reach. A verdict can be checked against a
label; "is this suggested fix actually actionable" cannot, and it is the part of the
output a compliance officer actually has to work from.

Three metrics, each answering a different question:

* **GEval on suggested_fix** — does the remediation address the violation that was cited,
  and could someone act on it without asking a follow-up question.
* **Faithfulness on the rationale** — is the reasoning grounded in the retrieved clause,
  or has the model produced a plausible argument the text does not support.
* **Contextual relevancy on retrieval** — was the clause set worth reasoning over at all.
  A rationale can be perfectly faithful to irrelevant context.

The goldens come from ``evals/golden/*.jsonl`` through deepeval's own loader. The JSON it
reads is derived at runtime and never committed — the JSONL is the source of truth, and a
second copy on disk is a second thing to keep in step.

    uv run pytest evals/test_generation_quality.py -m slow
    TIER2_LIMIT=2 uv run pytest evals/test_generation_quality.py -m slow
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

# Judged evals talk to a vendor. Nothing about that needs to also talk to deepeval's
# telemetry, and a test suite that phones home by default is a surprise in someone
# else's CI.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

from deepeval import assert_test
from deepeval.dataset import EvaluationDataset
from deepeval.metrics import (
    ContextualRelevancyMetric,
    FaithfulnessMetric,
    GEval,
)
from deepeval.test_case import LLMTestCase, SingleTurnParams

from evals.golden import load_rules
from evals.run_eval import build_stores
from specguard.config import get_settings
from specguard.models.rule import RULE_KINDS, RuleKind, Verdict
from specguard.retrieval.query import build_query
from specguard.rules.registry import rag_rules

pytestmark = pytest.mark.slow

#: Cap the number of judged cases *per verdict*. Nightly runs the whole set; a smoke run
#: does not need to, and a judged eval is the one part of this project that costs real
#: money per run. Per verdict rather than overall so a capped run still exercises both
#: paths — a fix is only judged on a FAIL, and a cap that happened to select passes
#: would report success while never running that metric at all.
LIMIT = int(os.environ.get("TIER2_LIMIT", "0"))


def _judge() -> str:
    """The judge model, pinned in config.

    A judged number is only comparable against another number from the same judge, so
    this is configuration rather than a deepeval default that can move underneath us.
    """
    return get_settings().judge_model


def _cases() -> list[dict[str, Any]]:
    """Run the pipeline offline and collect every decided RAG result worth judging."""
    import evals.pipeline as pipeline

    settings = get_settings()
    store, client = build_stores(settings, live=False)

    golden = [record for record in load_rules() if RULE_KINDS[record.rule_id] is RuleKind.RAG]
    wanted = {(record.spec_id, record.rule_id) for record in golden}
    report = pipeline.run(store, client, retrieval_limit=settings.retrieval_top_k, only=wanted)
    produced = {(o.spec_id, o.rule_id): o.result for o in report.outcomes}

    specs = {entry.spec_id: spec for entry, spec in pipeline.load_specs()}
    rules = rag_rules()

    rows: list[dict[str, Any]] = []
    for record in sorted(golden, key=lambda r: r.golden_id):
        result = produced.get((record.spec_id, record.rule_id))
        if result is None or result.verdict is Verdict.NEEDS_REVIEW:
            # An abstention has no rationale to be faithful to and no fix to judge. It is
            # tier 1's business, where it is counted rather than scored.
            continue
        spec = specs.get(record.spec_id)
        if spec is None or record.rule_id not in rules:
            continue

        query = build_query(record.rule_id, spec)
        if not query:
            # The rule reached PASS through the not-applicable path: nothing was
            # retrieved and nothing was generated, so there is nothing here to judge.
            continue
        hits = store.search(query, language=record.language, limit=settings.retrieval_top_k)
        # Faithfulness is judged against the clause the verdict actually rests on, not
        # against everything retrieved. Judging prose against five full articles asks the
        # judge to hold most of a regulation in mind to check one sentence — it is slow,
        # it is expensive, and it is not the question. Relevancy still sees all five.
        cited = next(
            (
                hit.clause.text
                for hit in hits
                if result.citations and hit.clause.chunk_id == result.citations[0].chunk_id
            ),
            "",
        )
        rows.append(
            {
                "name": record.golden_id,
                "input": query,
                # The rationale is the actual_output for faithfulness; the fix travels in
                # metadata and becomes its own test case.
                "actual_output": result.rationale,
                "expected_output": record.expected_verdict.value,
                "retrieval_context": [hit.clause.text for hit in hits],
                "additional_metadata": {
                    "rule_id": record.rule_id.value,
                    "split": record.split.value,
                    "verdict": result.verdict.value,
                    "suggested_fix": result.suggested_fix or "",
                    "citation": (result.citations[0].reference if result.citations else ""),
                    "quoted_span": (result.citations[0].quoted_span if result.citations else ""),
                    "cited_clause": cited,
                },
            }
        )
    if not LIMIT:
        return rows
    capped: list[dict[str, Any]] = []
    for verdict in (Verdict.FAIL.value, Verdict.PASS.value):
        capped.extend(
            [row for row in rows if row["additional_metadata"]["verdict"] == verdict][:LIMIT]
        )
    return capped


@pytest.fixture(scope="session")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> EvaluationDataset:
    """The golden set, loaded through deepeval's own loader.

    Written to a temporary JSON rather than a committed one so there is exactly one
    place a golden record is authored. deepeval reads JSON; the repository holds JSONL.
    """
    rows = _cases()
    if not rows:
        pytest.skip("no decided RAG results to judge")

    path = tmp_path_factory.mktemp("goldens") / "golden.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = EvaluationDataset()
    loaded.add_goldens_from_json_file(file_path=str(path))
    return loaded


def _ids(dataset: EvaluationDataset) -> list[str]:
    return [golden.name or str(index) for index, golden in enumerate(dataset.goldens)]


def test_rationale_is_grounded_in_the_retrieved_clause(dataset: EvaluationDataset) -> None:
    """Faithfulness: the reasoning may only rest on text that was actually retrieved.

    This is the judged counterpart to the verification pass. Verification asks whether a
    quoted span supports the verdict; this asks whether the prose a reviewer reads stays
    inside what the clause says.
    """
    judged = 0
    for golden in dataset.goldens:
        clause = str((golden.additional_metadata or {}).get("cited_clause") or "")
        if not clause:
            continue
        judged += 1
        case = LLMTestCase(
            input=golden.input,
            actual_output=golden.actual_output or "",
            retrieval_context=[clause],
        )
        assert_test(case, [FaithfulnessMetric(threshold=0.7, model=_judge())])

    if judged == 0:
        pytest.skip("no verdict in this selection cites a clause that was retrieved")


def test_retrieval_is_relevant_to_the_question_asked(dataset: EvaluationDataset) -> None:
    """Contextual relevancy: a faithful rationale over irrelevant clauses is still wrong."""
    for golden in dataset.goldens:
        case = LLMTestCase(
            input=golden.input,
            actual_output=golden.actual_output or "",
            retrieval_context=golden.retrieval_context or [],
        )
        assert_test(case, [ContextualRelevancyMetric(threshold=0.5, model=_judge())])


def test_suggested_fix_addresses_the_cited_violation(dataset: EvaluationDataset) -> None:
    """GEval: is the remediation about the violation that was cited, and can it be acted on.

    Only FAIL verdicts carry a fix. A PASS has nothing to remediate, and scoring an empty
    string would produce a low number that means nothing.
    """
    rubric = GEval(
        name="ActionableFix",
        model=_judge(),
        threshold=0.7,
        evaluation_params=[
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.RETRIEVAL_CONTEXT,
        ],
        evaluation_steps=[
            "Read the violation and the regulation clause given in the input.",
            "Check that the suggested fix addresses that specific violation, and not a "
            "different requirement or a general statement about compliance.",
            "Check that the fix is concrete enough to act on: it says what to change on "
            "the specification, not merely that something is wrong or should be reviewed.",
            "Penalise a fix that restates the violation without saying what to do, that "
            "defers to a human, or that would not resolve the cited breach if applied.",
        ],
    )

    judged = 0
    for golden in dataset.goldens:
        meta = golden.additional_metadata or {}
        if meta.get("verdict") != Verdict.FAIL.value or not meta.get("suggested_fix"):
            continue
        judged += 1
        case = LLMTestCase(
            input=(
                f"Violation found by rule {meta.get('rule_id')}: {golden.actual_output}\n"
                f"Cited clause: {meta.get('citation')}\n"
                f"Quoted text relied on: {meta.get('quoted_span')}"
            ),
            actual_output=str(meta.get("suggested_fix")),
            retrieval_context=golden.retrieval_context or [],
        )
        assert_test(case, [rubric])

    if judged == 0:
        pytest.skip("no FAIL verdicts with a suggested fix in this selection")
