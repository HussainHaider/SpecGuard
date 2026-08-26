"""Tracing, and the guarantee that it stays off unless it is switched on.

The second half matters more than the first. No test in the default run may reach a live
API, and a tracing layer is a network call attached to every model call — so the tests
that assert it does nothing are the ones protecting that rule.
"""

from __future__ import annotations

import pytest

from specguard.config import Settings
from specguard.guardrails.verdicts import apply_gates
from specguard.llm.factory import FIXTURE_DIR, build_client
from specguard.llm.fake import FakeClient
from specguard.llm.protocol import LLMClient
from specguard.models.citation import Citation
from specguard.models.rule import RuleId, RuleResult, Verdict
from specguard.tracing import (
    TracedClient,
    configure_tracing,
    current_rule_id,
    record_feedback,
    rule_span,
    span,
    tracing_enabled,
)


@pytest.fixture(autouse=True)
def _no_ambient_tracing(monkeypatch):
    """Every test starts with tracing off, whatever the developer's environment says."""
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)


class TestConfiguration:
    def test_off_by_default(self):
        # Explicit rather than bare Settings(): the developer running this may well have
        # tracing switched on in their own .env, and the default under test is the
        # code's, not theirs.
        assert configure_tracing(Settings(langsmith_tracing=False)) is False
        assert tracing_enabled() is False

    def test_switched_on_without_a_key_stays_off(self):
        """Fails closed. Tracing enabled with no key would fail on every span, not once."""
        settings = Settings(langsmith_tracing=True, langsmith_api_key=None)
        assert configure_tracing(settings) is False
        assert tracing_enabled() is False

    def test_switched_on_with_a_key_publishes_the_environment(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_API_KEY", "")
        settings = Settings(
            langsmith_tracing=True,
            langsmith_api_key="ls-test",
            langsmith_project="specguard-test",
        )
        assert configure_tracing(settings) is True
        assert tracing_enabled() is True

    def test_the_flag_is_read_from_the_environment_not_cached(self, monkeypatch):
        """The API and the worker are different processes; a module flag would lie about one."""
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        assert tracing_enabled() is True
        monkeypatch.setenv("LANGSMITH_TRACING", "false")
        assert tracing_enabled() is False


class TestSpansWhenDisabled:
    def test_a_span_does_nothing_and_has_no_run(self):
        with span("node:parse") as handle:
            handle.tag(anything="at all")
            assert handle.run_id is None

    def test_a_rule_span_still_scopes_the_rule_id(self):
        """The context is what the model boundary reads, and it must work either way."""
        assert current_rule_id() is None
        with rule_span(RuleId.ALLERGEN_EMPHASIS):
            assert current_rule_id() == "ALLERGEN_EMPHASIS"
        assert current_rule_id() is None

    def test_nested_rule_spans_restore_the_outer_scope(self):
        with rule_span(RuleId.ORIGIN_DECLARATION):
            with rule_span(RuleId.LEGAL_NAME_AND_QUID):
                assert current_rule_id() == "LEGAL_NAME_AND_QUID"
            assert current_rule_id() == "ORIGIN_DECLARATION"

    def test_feedback_is_not_pushed(self):
        assert record_feedback("run-1", corrected_verdict="PASS") is None


class TestTracedClient:
    def test_it_satisfies_the_client_protocol(self):
        assert isinstance(TracedClient(FakeClient(FIXTURE_DIR)), LLMClient)

    def test_it_reports_the_wrapped_provider(self):
        traced = TracedClient(FakeClient(FIXTURE_DIR, model="recorded"))
        assert (traced.provider, traced.model) == ("fake", "recorded")

    def test_the_factory_does_not_wrap_when_tracing_is_off(self):
        assert not isinstance(build_client(Settings(llm_provider="fake")), TracedClient)

    def test_the_factory_wraps_when_tracing_is_on(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        assert isinstance(build_client(Settings(llm_provider="fake")), TracedClient)


class TestTheRunIdSurvivesTheGates:
    """A gated verdict is rebuilt field by field, so the trace has to be carried across."""

    def _result(self, verdict: Verdict, confidence: float) -> RuleResult:
        return RuleResult(
            rule_id=RuleId.ORIGIN_DECLARATION,
            verdict=verdict,
            citations=[
                Citation.for_clause(
                    regulation="Regulation (EU) No 1169/2011",
                    article="26",
                    paragraph="2",
                    quoted_span="indication of the country of origin shall be mandatory",
                    source_version="02011R1169-20180101-en",
                )
            ],
            rationale="origin is declared",
            suggested_fix="declare it" if verdict is Verdict.FAIL else None,
            confidence=confidence,
            langsmith_run_id="run-abc",
        )

    def test_a_downgraded_verdict_keeps_the_run_that_produced_it(self):
        outcome = apply_gates(
            self._result(Verdict.PASS, 0.1), known_chunk_ids=set(), min_confidence=0.6
        )
        assert outcome.result.verdict is Verdict.NEEDS_REVIEW
        assert outcome.result.langsmith_run_id == "run-abc"

    def test_an_untouched_verdict_keeps_it_too(self):
        known = {self._result(Verdict.PASS, 0.9).citations[0].chunk_id}
        outcome = apply_gates(
            self._result(Verdict.PASS, 0.9), known_chunk_ids=known, min_confidence=0.6
        )
        assert not outcome.changed
        assert outcome.result.langsmith_run_id == "run-abc"
