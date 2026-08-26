"""The nodes of the check graph.

Each node does one thing and writes one part of the state. They are plain functions
taking and returning state, so every one is testable without constructing a graph.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from specguard.config import Settings
from specguard.graph.planning import plan_rules
from specguard.graph.state import CheckState
from specguard.guardrails.injection import scan
from specguard.guardrails.pii import scrub
from specguard.guardrails.upload import check_page_count
from specguard.guardrails.verdicts import apply_gates
from specguard.ingest.extract import extract_spec
from specguard.ingest.pdf import ingest_pdf
from specguard.llm.protocol import LLMClient
from specguard.models.common import Language
from specguard.models.document import IngestedDocument, Page
from specguard.models.report import CheckReport, GuardrailFlags
from specguard.models.rule import AbstentionReason, RuleId, RuleResult, Verdict
from specguard.models.spec import ProductSpec
from specguard.rules.base import RagContext, RuleContext
from specguard.rules.registry import deterministic_rules, rag_rules
from specguard.tracing import Span, rule_span
from specguard.vectorstore.protocol import VectorStore

#: Fields whose confidence is worth reporting when it is low. Not every field matters
#: equally: a badly-read storage instruction changes nothing, a badly-read nutrition
#: table changes three rules.
_WATCHED_FIELDS = (
    "legal_name",
    "ingredients",
    "net_quantity",
    "nutrition",
    "durability",
    "business_operator",
)


@dataclass(frozen=True)
class Dependencies:
    """Everything the nodes need from outside. Passed in so tests can substitute fakes."""

    settings: Settings
    client: LLMClient
    store: VectorStore
    corpus_chunk_ids: set[str]


def _redact(document: IngestedDocument) -> tuple[IngestedDocument, int]:
    """Return the document with personal data removed from its page text.

    The redaction is applied to the document itself rather than kept beside it. An
    earlier version put the scrubbed text in a separate state key that nothing ever
    read, so the guarantee this node advertises — that no later node can pass the
    unredacted text — was not one the state actually enforced. It is now: the
    unredacted text does not survive this function.

    Only ``Page.text`` is rewritten. The spans carry font weight for ALLERGEN_EMPHASIS
    and are matched against ingredient names, never against a contact block, so
    redacting them would cost the evidence Art. 21(1)(b) needs and remove nothing.
    """
    redacted: list[Page] = []
    removed = 0
    for page in document.pages:
        text, redaction = scrub(page.text)
        removed += redaction.total
        redacted.append(page.model_copy(update={"text": text}))
    return document.model_copy(update={"pages": redacted}), removed


def parse(state: CheckState, deps: Dependencies) -> CheckState:
    """Read the PDF, screen it, and redact personal data before anything sees it.

    Scrubbing happens here rather than at the model boundary so that no later node can
    accidentally pass the unredacted text — the state simply does not carry it onward.
    """
    del deps  # Parsing depends on nothing outside; the signature is uniform on purpose.
    document = ingest_pdf(Path(state["pdf_path"]))
    check_page_count(len(document.pages))

    # Screened before redaction: an injected instruction sitting next to a phone number
    # must still be seen, and a redaction marker in its place is not a signal.
    injection = scan(document.text)
    document, redactions = _redact(document)

    return {
        "started_at": time.perf_counter(),
        "document": document,
        "guardrails": GuardrailFlags(
            injection_suspected=injection.suspected,
            injection_signals=injection.signals(),
            unreadable_pages=[p.number for p in document.pages if not p.text.strip()],
        ),
        "skipped_rules": {"_redactions": str(redactions)} if redactions else {},
    }


def extract(state: CheckState, deps: Dependencies) -> CheckState:
    """One schema-constrained call producing a ProductSpec with per-field confidence."""
    language = Language(state.get("language", "en"))
    spec, _usage = extract_spec(state["document"], deps.client, language=language)

    flags = state.get("guardrails", GuardrailFlags())
    low_confidence = [
        name
        for name in _WATCHED_FIELDS
        if (field := getattr(spec, name, None)) is not None
        and field.confidence < deps.settings.min_extraction_confidence
    ]
    return {
        "spec": spec,
        "guardrails": flags.model_copy(update={"low_confidence_fields": low_confidence}),
    }


def plan(state: CheckState, deps: Dependencies) -> CheckState:
    """Select the rules this specification can actually answer."""
    del deps
    selected, skipped = plan_rules(state["spec"])
    existing = {k: v for k, v in state.get("skipped_rules", {}).items() if k.startswith("_")}
    return {"selected_rules": selected, "skipped_rules": {**existing, **skipped}}


def check(state: CheckState, deps: Dependencies) -> CheckState:
    """Run the selected rules.

    The deterministic rules are handed a RuleContext, which carries no store and no
    client. That is the enforcement of non-negotiable #2 at the graph level: they have
    nothing to make a model call with.
    """
    spec: ProductSpec = state["spec"]
    selected = set(state["selected_rules"])
    language = spec.language
    source_version = f"02011R1169-20180101-{language.value}"

    plain = RuleContext(
        source_version=source_version,
        language=language,
        min_confidence=deps.settings.min_extraction_confidence,
    )
    rag = RagContext(
        source_version=source_version,
        language=language,
        min_confidence=deps.settings.min_extraction_confidence,
        store=deps.store,
        client=deps.client,
        retrieval_limit=deps.settings.retrieval_top_k,
        min_retrieval_score=deps.settings.min_retrieval_score,
    )

    results: list[RuleResult] = []
    for rule_id, rule in deterministic_rules().items():
        if rule_id in selected:
            with rule_span(rule_id) as traced:
                result = rule.evaluate(spec, plain)
                traced.tag(verdict=result.verdict.value, confidence=result.confidence)
            results.append(_traced(result, traced))
    for rule_id, rag_rule in rag_rules().items():
        if rule_id in selected:
            with rule_span(rule_id) as traced:
                result = _guarded(rag_rule, spec, rag, rule_id)
                traced.tag(
                    verdict=result.verdict.value,
                    confidence=result.confidence,
                    cost_usd=sum(u.cost_usd for u in result.llm_usage),
                    llm_calls=len(result.llm_usage),
                )
            results.append(_traced(result, traced))
    return {"results": results}


def _traced(result: RuleResult, span: Span) -> RuleResult:
    """Stamp the result with the run that produced it, when there was one."""
    if span.run_id is None:
        return result
    return result.model_copy(update={"langsmith_run_id": span.run_id})


def _guarded(rule: object, spec: ProductSpec, context: RagContext, rule_id: RuleId) -> RuleResult:
    """Run a RAG rule, turning an unexpected failure into an abstention.

    A rule that raises must not take the whole report down with it. The other seven
    findings are still worth having, and a rule that errored is exactly a case for a
    human rather than a silent omission.
    """
    try:
        return rule.evaluate(spec, context)  # type: ignore[attr-defined,no-any-return]
    except Exception as error:
        return RuleResult(
            rule_id=rule_id,
            verdict=Verdict.NEEDS_REVIEW,
            rationale=f"This rule could not be evaluated: {type(error).__name__}: {error}",
            confidence=0.0,
            abstention_reason=AbstentionReason.RULE_ERROR,
        )


def verify(state: CheckState, deps: Dependencies) -> CheckState:
    """Apply the report-level gates to every result.

    Per-rule citation verification already happened inside each RAG rule. This is the
    second, different question: does the citation resolve against the corpus we actually
    have, was the verdict confident enough to report, and does it need a person?
    """
    gated: list[RuleResult] = []
    escalated: list[str] = []

    for result in state.get("results", []):
        outcome = apply_gates(
            result,
            known_chunk_ids=deps.corpus_chunk_ids,
            min_confidence=deps.settings.min_extraction_confidence,
        )
        gated.append(outcome.result)
        if any("human review" in note for note in outcome.notes):
            escalated.append(result.rule_id.value)

    flags = state.get("guardrails", GuardrailFlags())
    if escalated:
        flags = flags.model_copy(
            update={
                "low_confidence_fields": [
                    *flags.low_confidence_fields,
                    *(f"escalated:{rule}" for rule in escalated),
                ]
            }
        )
    return {"gated_results": gated, "guardrails": flags}


def aggregate(state: CheckState, deps: Dependencies) -> CheckState:
    """Assemble the report."""
    started = state.get("started_at")
    report = CheckReport(
        job_id=None,
        spec=state["spec"],
        # Gated results if verify ran, raw ones otherwise — never both.
        results=state.get("gated_results", state.get("results", [])),
        guardrails=state.get("guardrails", GuardrailFlags()),
        corpus_version=f"02011R1169-20180101-{state['spec'].language.value}",
        graph_version=deps.settings.graph_version,
        duration_ms=int((time.perf_counter() - started) * 1000) if started else 0,
    )
    return {"report": report}
