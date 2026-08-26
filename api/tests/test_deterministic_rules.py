"""The four deterministic rules, checked against the seeded defects in the manifest.

Ground truth comes from the fixture generator rather than from extraction, so a failure
here is unambiguously the rule's fault. Extraction has its own tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from specguard.fixtures.generate import build_sheets, load_manifest
from specguard.fixtures.to_spec import spec_for_sheet
from specguard.models.common import Language
from specguard.models.rule import RuleId, RuleKind, Verdict
from specguard.rules.base import RuleContext
from specguard.rules.registry import deterministic_rules, missing_ids, registered_ids

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "specs"
MANIFEST = FIXTURE_DIR / "manifest.jsonl"
DETERMINISTIC = {
    RuleId.MANDATORY_FIELDS,
    RuleId.NUTRITION_ARITHMETIC,
    RuleId.NUTRITION_PER_100,
    RuleId.ALLERGEN_EMPHASIS,
}


def _context(language: Language) -> RuleContext:
    return RuleContext(source_version=f"02011R1169-20180101-{language.value}", language=language)


@pytest.fixture(scope="module")
def cases():
    """(fixture, ground-truth spec) for every generated spec sheet."""
    if not MANIFEST.exists():
        pytest.skip("fixtures not generated")
    manifest = {entry.spec_id: entry for entry in load_manifest(MANIFEST)}
    built = []
    for spec_id, sheet in build_sheets():
        entry = manifest[spec_id]
        built.append((entry, spec_for_sheet(sheet, FIXTURE_DIR / "generated" / entry.filename)))
    return built


class TestRegistry:
    def test_registers_exactly_the_deterministic_rules(self) -> None:
        assert registered_ids() == DETERMINISTIC

    def test_knows_which_rules_are_still_missing(self) -> None:
        # The RAG four arrive in M3; the registry should say so rather than pretend
        # the rule set is complete.
        assert missing_ids() == set(RuleId) - DETERMINISTIC

    def test_every_registered_rule_is_declared_deterministic(self) -> None:
        from specguard.models.rule import RULE_KINDS

        for rule_id in registered_ids():
            assert RULE_KINDS[rule_id] is RuleKind.DETERMINISTIC


class TestAgainstTheManifest:
    """Each rule must agree with the ground truth on every one of the thirty specs."""

    @pytest.mark.parametrize("rule_id", sorted(DETERMINISTIC))
    def test_verdicts_match_expected(self, cases, rule_id: RuleId) -> None:
        rule = deterministic_rules()[rule_id]
        wrong: list[str] = []
        for entry, spec in cases:
            result = rule.evaluate(spec, _context(entry.language))
            expected = entry.expected_verdicts[rule_id]
            if result.verdict is not expected:
                wrong.append(
                    f"{entry.spec_id} ({entry.product_name}): expected {expected.value}, "
                    f"got {result.verdict.value} — {result.rationale}"
                )
        assert not wrong, "\n".join(wrong)

    def test_no_rule_fires_on_a_compliant_spec(self, cases) -> None:
        # The check that catches a rule which simply always fails.
        for entry, spec in cases:
            if not entry.compliant:
                continue
            for rule_id, rule in deterministic_rules().items():
                result = rule.evaluate(spec, _context(entry.language))
                assert result.verdict is Verdict.PASS, (
                    f"{rule_id} failed compliant {entry.spec_id}: {result.rationale}"
                )

    def test_injections_do_not_change_any_verdict(self, cases) -> None:
        # Both adversarial specs genuinely fail a rule. A deterministic rule reads
        # structured fields and never sees free text, so the planted instruction has
        # nothing to act on — this proves that rather than assuming it.
        for entry, spec in cases:
            if not entry.adversarial:
                continue
            failing = {defect.rule_id for defect in entry.seeded_defects}
            for rule_id in failing & DETERMINISTIC:
                result = deterministic_rules()[rule_id].evaluate(spec, _context(entry.language))
                assert result.verdict is Verdict.FAIL


class TestVerdictQuality:
    def test_every_decision_carries_a_resolvable_citation(self, cases) -> None:
        for entry, spec in cases:
            for rule in deterministic_rules().values():
                result = rule.evaluate(spec, _context(entry.language))
                if result.verdict is Verdict.NEEDS_REVIEW:
                    continue
                assert result.citations, f"{result.rule_id} decided without citing"
                for citation in result.citations:
                    assert citation.source_version.startswith("02011R1169")

    def test_failures_explain_how_to_fix_them(self, cases) -> None:
        for entry, spec in cases:
            for rule in deterministic_rules().values():
                result = rule.evaluate(spec, _context(entry.language))
                if result.verdict is Verdict.FAIL:
                    assert result.suggested_fix

    def test_arithmetic_reports_the_numbers_behind_its_verdict(self, cases) -> None:
        # A reviewer disagreeing with the tolerance needs to see both figures and the
        # threshold, not just the word FAIL.
        rule = deterministic_rules()[RuleId.NUTRITION_ARITHMETIC]
        entry, spec = cases[0]
        result = rule.evaluate(spec, _context(entry.language))
        assert {"declared_kJ", "computed_kJ", "tolerance_pct"} <= set(result.metrics)

    def test_low_confidence_abstains_rather_than_failing(self, cases) -> None:
        # A badly-read field is our problem, not the supplier's, so it must never
        # produce a FAIL against them.
        entry, spec = next((e, s) for e, s in cases if e.compliant)
        blurred = spec.model_copy(
            update={"nutrition": spec.nutrition.model_copy(update={"confidence": 0.1})}
        )
        for rule_id in (RuleId.NUTRITION_ARITHMETIC, RuleId.NUTRITION_PER_100):
            result = deterministic_rules()[rule_id].evaluate(blurred, _context(entry.language))
            assert result.verdict is Verdict.NEEDS_REVIEW
            assert result.abstention_reason is not None
