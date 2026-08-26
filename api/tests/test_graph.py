"""The check graph, run end to end offline.

Retrieval and every model call replay from recorded fixtures, so this exercises the real
node sequence — parse, extract, plan, check, verify, aggregate — with no network, no
vector store and nothing to spend.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from evals.run_eval import SEARCH_FIXTURES

from specguard.config import Settings
from specguard.corpus.seed import load_clauses
from specguard.fixtures.generate import load_manifest
from specguard.graph.graph import NODES, run_check
from specguard.graph.nodes import Dependencies
from specguard.graph.planning import plan_rules
from specguard.llm.factory import FIXTURE_DIR
from specguard.llm.fake import FakeClient
from specguard.models.rule import RuleId, Verdict
from specguard.vectorstore.fixtures import FixtureStore

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "fixtures" / "specs"


@pytest.fixture(scope="module")
def deps() -> Dependencies:
    corpus = REPO_ROOT / "corpus"
    if not (corpus / "sources.json").exists():
        pytest.skip("corpus not fetched")
    return Dependencies(
        settings=Settings(),
        client=FakeClient(FIXTURE_DIR, model="recorded"),
        store=FixtureStore(SEARCH_FIXTURES),
        corpus_chunk_ids={clause.chunk_id for clause in load_clauses(corpus)},
    )


@pytest.fixture(scope="module")
def manifest():
    path = SPEC_DIR / "manifest.jsonl"
    if not path.exists():
        pytest.skip("fixtures not generated")
    return {entry.spec_id: entry for entry in load_manifest(path)}


class TestNodeSequence:
    def test_the_graph_runs_the_documented_sequence(self) -> None:
        assert [name for name, _ in NODES] == [
            "parse",
            "extract",
            "plan",
            "check",
            "verify",
            "aggregate",
        ]


class TestPlanning:
    def test_a_product_with_no_claims_skips_both_claim_rules(self, manifest) -> None:
        from specguard.fixtures.generate import build_sheets
        from specguard.fixtures.to_spec import spec_for_sheet

        sheets = dict(build_sheets())
        plain = next(
            spec_for_sheet(sheet)
            for spec_id, sheet in sheets.items()
            if not sheet.nutrition_claim and not sheet.health_claim
        )
        selected, skipped = plan_rules(plain)
        assert RuleId.NUTRITION_CLAIM_CONDITIONS not in selected
        assert RuleId.HEALTH_CLAIM_AUTHORISED not in selected
        # A skipped rule is reported with its reason, not silently absent.
        assert skipped[RuleId.HEALTH_CLAIM_AUTHORISED.value]

    def test_a_product_with_a_claim_keeps_that_rule(self, manifest) -> None:
        from specguard.fixtures.generate import build_sheets
        from specguard.fixtures.to_spec import spec_for_sheet

        sheets = dict(build_sheets())
        claiming = next(spec_for_sheet(sheet) for sheet in sheets.values() if sheet.nutrition_claim)
        selected, _ = plan_rules(claiming)
        assert RuleId.NUTRITION_CLAIM_CONDITIONS in selected

    def test_unconditional_rules_are_never_skipped(self, manifest) -> None:
        # A missing nutrition declaration is what MANDATORY_FIELDS is for, so a spec
        # without one still needs checking rather than excusing.
        from specguard.fixtures.generate import build_sheets
        from specguard.fixtures.to_spec import spec_for_sheet

        for _, sheet in build_sheets():
            selected, _ = plan_rules(spec_for_sheet(sheet))
            assert RuleId.MANDATORY_FIELDS in selected
            assert RuleId.ALLERGEN_EMPHASIS in selected


class TestEndToEnd:
    @pytest.fixture(scope="class")
    def report(self, deps, manifest):
        entry = manifest["SPEC-001"]
        state = run_check(
            deps,
            {
                "job_id": "test-job",
                "correlation_id": "test-correlation",
                "pdf_path": str(SPEC_DIR / "generated" / entry.filename),
                "language": entry.language.value,
            },
        )
        return state["report"]

    def test_produces_a_report(self, report) -> None:
        assert report.results
        assert report.graph_version
        assert report.duration_ms >= 0

    def test_results_are_not_duplicated_by_the_verify_node(self, report) -> None:
        # `results` accumulates via a reducer. If verify wrote back to it instead of to
        # its own key, every rule would appear twice.
        rule_ids = [result.rule_id for result in report.results]
        assert len(rule_ids) == len(set(rule_ids))

    def test_every_decided_verdict_carries_a_citation(self, report) -> None:
        for result in report.results:
            if result.verdict is not Verdict.NEEDS_REVIEW:
                assert result.citations, f"{result.rule_id} decided without citing"

    def test_guardrail_flags_are_populated(self, report) -> None:
        assert report.guardrails is not None

    def test_the_report_round_trips(self, report) -> None:
        from specguard.models.report import CheckReport

        restored = CheckReport.model_validate_json(report.model_dump_json())
        assert restored.overall_verdict is report.overall_verdict


class TestAdversarialDocument:
    def test_an_injected_instruction_is_flagged_and_not_obeyed(self, deps, manifest) -> None:
        adversarial = next(entry for entry in manifest.values() if entry.adversarial)
        state = run_check(
            deps,
            {
                "job_id": "adversarial",
                "correlation_id": "adversarial",
                "pdf_path": str(SPEC_DIR / "generated" / adversarial.filename),
                "language": adversarial.language.value,
            },
        )
        report = state["report"]
        assert report.guardrails.injection_suspected, "the planted instruction was not detected"

        # The document tells the checker to report everything as compliant. It fails a
        # rule, and must still be reported as failing it.
        failing = {defect.rule_id for defect in adversarial.seeded_defects}
        for result in report.results:
            if result.rule_id in failing:
                assert result.verdict is Verdict.FAIL, (
                    f"{result.rule_id} returned {result.verdict} on a document carrying an "
                    "instruction to report compliance"
                )
