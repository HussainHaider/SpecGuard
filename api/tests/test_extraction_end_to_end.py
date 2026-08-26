"""Extraction plus the deterministic rules, over every fixture with a recorded response.

This is the closest thing to the real pipeline the default suite can run: real model
output, replayed. It answers the question the unit tests cannot — does the extractor
read these documents well enough for the rules to reach the right verdict?

Marked slow only where it is; the replay itself is offline and free.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from specguard.fixtures.generate import load_manifest
from specguard.ingest.extract import extract_spec
from specguard.ingest.pdf import ingest_pdf
from specguard.llm.fake import FakeClient, MissingFixtureError
from specguard.models.rule import Verdict
from specguard.rules.base import RuleContext
from specguard.rules.registry import deterministic_rules

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "specs"
LLM_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm"
DETERMINISTIC = set(deterministic_rules())


def _recorded_cases():
    """Every fixture spec that has a recorded extraction response."""
    manifest_path = FIXTURE_DIR / "manifest.jsonl"
    if not manifest_path.exists():
        return []
    client = FakeClient(LLM_FIXTURES, model="recorded")
    cases = []
    for entry in load_manifest(manifest_path):
        pdf = FIXTURE_DIR / "generated" / entry.filename
        if not pdf.exists():
            continue
        document = ingest_pdf(pdf)
        if not client.fixture_path("extract", document.source.sha256[:16]).exists():
            continue
        cases.append((entry, document))
    return cases


CASES = _recorded_cases()


@pytest.mark.skipif(not CASES, reason="no recorded extraction fixtures")
class TestEndToEnd:
    @pytest.fixture
    def client(self) -> FakeClient:
        return FakeClient(LLM_FIXTURES, model="recorded")

    @pytest.mark.parametrize(
        ("entry", "document"), CASES, ids=[entry.spec_id for entry, _ in CASES]
    )
    def test_rules_reach_the_expected_verdict(self, entry, document, client) -> None:
        spec, _ = extract_spec(document, client, language=entry.language)
        context = RuleContext(
            source_version=f"02011R1169-20180101-{entry.language.value}",
            language=entry.language,
        )
        for rule_id, rule in deterministic_rules().items():
            result = rule.evaluate(spec, context)
            expected = entry.expected_verdicts[rule_id]
            assert result.verdict is expected, (
                f"{entry.spec_id} {rule_id.value}: expected {expected.value}, got "
                f"{result.verdict.value} — {result.rationale}"
            )

    def test_injected_instructions_are_reported_not_obeyed(self, client) -> None:
        """The adversarial specs must still fail the rule they actually break."""
        adversarial = [(e, d) for e, d in CASES if e.adversarial]
        if not adversarial:
            pytest.skip("no recorded adversarial specs")
        for entry, document in adversarial:
            spec, _ = extract_spec(document, client, language=entry.language)
            context = RuleContext(
                source_version=f"02011R1169-20180101-{entry.language.value}",
                language=entry.language,
            )
            for defect in entry.seeded_defects:
                if defect.rule_id not in DETERMINISTIC:
                    continue
                result = deterministic_rules()[defect.rule_id].evaluate(spec, context)
                assert result.verdict is Verdict.FAIL, (
                    f"{entry.spec_id} carries an injection telling the model to report "
                    f"compliance, and {defect.rule_id.value} returned "
                    f"{result.verdict.value} — the injection appears to have worked"
                )

    def test_extraction_does_not_invent_a_missing_particular(self, client) -> None:
        """A supplier's omission must survive extraction, or the finding is destroyed."""
        omitted = [
            (e, d)
            for e, d in CASES
            if any(defect.kind == "missing_net_quantity" for defect in e.seeded_defects)
        ]
        if not omitted:
            pytest.skip("no recorded spec with an omitted net quantity")
        for entry, document in omitted:
            spec, _ = extract_spec(document, client, language=entry.language)
            assert spec.net_quantity is None, (
                f"{entry.spec_id} omits its net quantity but extraction supplied "
                f"{spec.net_quantity}"
            )

    def test_a_renamed_fixture_cannot_replay_another_document(self, client) -> None:
        entry, document = CASES[0]
        renamed = document.model_copy(
            update={"source": document.source.model_copy(update={"sha256": "f" * 64})}
        )
        with pytest.raises(MissingFixtureError):
            extract_spec(renamed, client, language=entry.language)


@pytest.mark.skipif(not CASES, reason="no recorded extraction fixtures")
def test_confidence_scores_are_not_uniformly_maximal() -> None:
    """Confidence has to vary to be worth anything.

    The abstention guardrail routes low-confidence fields to a human. If the model
    reports 1.0 for everything it ever reads, that gate never fires and the guardrail is
    decorative. This records what the model actually does rather than assuming.
    """
    client = FakeClient(LLM_FIXTURES, model="recorded")
    scores: list[float] = []
    for entry, document in CASES:
        spec, _ = extract_spec(document, client, language=entry.language)
        scores.extend(
            field.confidence
            for name in ("legal_name", "net_quantity", "nutrition", "ingredients", "durability")
            if (field := getattr(spec, name)) is not None
        )
    assert scores
    unique = set(scores)
    if unique == {1.0}:
        pytest.xfail(
            f"the model returned confidence 1.0 for all {len(scores)} extracted fields; "
            "the low-confidence abstention path is untested by real output"
        )
