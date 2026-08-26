"""Retrieve, judge, verify.

The judge proposes; verification decides. A judged verdict is only allowed to stand if
four things hold, and any failure turns it into NEEDS_REVIEW with a stated reason:

1. the cited chunk was in the set retrieved for this rule — it cannot cite a clause it
   never saw;
2. the citation validates, which re-derives the chunk id from the clause it names, so
   the article it claims is the article it retrieved;
3. the quoted span appears verbatim in that chunk's text;
4. a second, independent call agrees the span actually supports the verdict.

Checks 1 to 3 are pure Python and cost nothing. Only a verdict that survives all three
is worth spending a second model call on, which is why the entailment check is last.
"""

from __future__ import annotations

import re
import time
from enum import StrEnum

from pydantic import Field

from specguard.models.citation import Citation
from specguard.models.common import SpecGuardModel
from specguard.models.corpus import Clause
from specguard.models.rule import (
    AbstentionReason,
    LlmUsage,
    RuleId,
    RuleResult,
    Verdict,
)
from specguard.models.spec import ProductSpec
from specguard.prompts.loader import load_prompt
from specguard.retrieval.query import build_query
from specguard.rules.base import RagContext, abstain
from specguard.vectorstore.protocol import SearchHit

VERIFY_PROMPT = "verify"
_WHITESPACE = re.compile(r"\s+")

#: A span shorter than this is too generic to prove anything — "the food", "shall be" —
#: and would let a citation pass verification while establishing nothing.
MIN_SPAN_CHARS = 20


#: A judged verdict may rest on more than one clause, but not on many: past a few, the
#: model is assembling a case rather than citing the provision that decides it.
MAX_CITATIONS = 3


class JudgeCitation(SpecGuardModel):
    """One clause a judge relied on."""

    chunk_id: str = Field(description="chunk_id of a clause from the retrieved set.")
    quoted_span: str = Field(description="Text copied verbatim from that clause.")


class JudgeVerdict(SpecGuardModel):
    """What a judge call must return.

    Citations are a list because real requirements span clauses. Art. 22(1) says *when*
    a quantitative declaration is required; Annex VIII says *how* it must be made. A
    judge allowed only one citation has to pick, and verification then correctly rejects
    the verdict for resting on a clause that establishes only half of it — which is how
    a single-citation schema turns sound reasoning into an abstention.
    """

    verdict: Verdict
    rationale: str = Field(min_length=1)
    suggested_fix: str | None = None
    citations: list[JudgeCitation] = Field(
        default_factory=list,
        description="Clauses relied on. At least one is required for PASS or FAIL.",
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class Support(StrEnum):
    """Whether a cited clause supports the verdict attached to it."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    INSUFFICIENT = "insufficient"


class Verification(SpecGuardModel):
    """What the verification call must return."""

    support: Support
    reason: str = Field(min_length=1)


def _normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()


def span_appears_in(span: str, clause: Clause) -> bool:
    """Whether the quoted span is really in the clause, ignoring whitespace and case.

    Whitespace is normalised because the text passed to the model has been re-wrapped;
    nothing else is relaxed. A paraphrase must fail this.
    """
    return _normalise(span) in _normalise(clause.text)


class RagRule:
    """Base for the four retrieval-backed rules.

    Subclasses supply only their rule id and prompt name. The pipeline — including every
    way it can decline to answer — is shared, so no rule can quietly implement a laxer
    version of the verification contract.
    """

    rule_id: RuleId
    judge_prompt: str

    #: The provision this rule enforces, cited when the requirement is not triggered.
    #: A product that makes no claim complies with the rules on claims, and saying so
    #: with the governing clause attached is a real PASS — not something to abstain on.
    governing_regulation: str
    governing_article: str
    governing_quote: str
    governing_paragraph: str | None = None

    def evaluate(self, spec: ProductSpec, context: RagContext) -> RuleResult:
        """Run retrieve, judge and verify for one spec."""
        started = time.perf_counter()
        usage: list[LlmUsage] = []

        query = build_query(self.rule_id, spec)
        if not query:
            return self._not_applicable(context, started)

        hits = context.store.search(query, language=spec.language, limit=context.retrieval_limit)
        if not hits:
            return self._abstain(
                "Retrieval returned no clauses for this rule.",
                AbstentionReason.NO_RELEVANT_CLAUSE_RETRIEVED,
                usage,
                started,
            )
        if hits[0].score < context.min_retrieval_score:
            return self._abstain(
                f"The best retrieved clause scored {hits[0].score:.2f}, below the "
                f"{context.min_retrieval_score:.2f} threshold, so nothing retrieved is "
                "relevant enough to judge against.",
                AbstentionReason.NO_RELEVANT_CLAUSE_RETRIEVED,
                usage,
                started,
            )

        judged = context.client.generate(
            prompt=load_prompt(self.judge_prompt),
            schema=JudgeVerdict,
            document=self._render(spec, hits),
            cache_key=f"{self.rule_id.value}__{spec.source.sha256[:16]}",
        )
        usage.append(judged.usage)
        verdict = judged.value

        if verdict.verdict is Verdict.NEEDS_REVIEW:
            return self._abstain(
                verdict.rationale, AbstentionReason.JUDGE_UNCERTAIN, usage, started
            )

        validated, failures = self._validate_citations(verdict, hits)
        if not validated:
            return self._abstain(
                f"{verdict.rationale} (citation rejected: {'; '.join(failures)})",
                AbstentionReason.CITATION_UNVERIFIED,
                usage,
                started,
            )

        # Verify the cited clauses in turn and stop at the first that supports the
        # verdict. One clause that genuinely establishes the point is enough; paying for
        # entailment checks on the rest proves nothing further.
        supported: Citation | None = None
        rejections: list[str] = []
        for citation, clause in validated:
            checked = context.client.generate(
                prompt=load_prompt(VERIFY_PROMPT),
                schema=Verification,
                document=self._render_verification(verdict, citation, clause),
                cache_key=(
                    f"verify__{self.rule_id.value}__{citation.chunk_id[:8]}"
                    f"__{spec.source.sha256[:16]}"
                ),
            )
            usage.append(checked.usage)
            if checked.value.support is Support.SUPPORTS:
                supported = citation
                break
            rejections.append(f"{citation.reference} {checked.value.support.value}")

        if supported is None:
            return self._abstain(
                f"{verdict.rationale} (verification: no cited clause supports this "
                f"verdict — {'; '.join(rejections)})",
                AbstentionReason.CITATION_UNVERIFIED,
                usage,
                started,
            )

        # The verified clause leads; the others passed the structural checks and were
        # genuinely relied on, so they are reported rather than discarded.
        others = [citation for citation, _ in validated if citation.chunk_id != supported.chunk_id]
        return RuleResult(
            rule_id=self.rule_id,
            verdict=verdict.verdict,
            citations=[supported, *others],
            rationale=verdict.rationale,
            suggested_fix=verdict.suggested_fix
            or (
                "Review this requirement against the cited clause."
                if verdict.verdict is Verdict.FAIL
                else None
            ),
            confidence=verdict.confidence,
            llm_usage=usage,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _validate_citations(
        self, verdict: JudgeVerdict, hits: list[SearchHit]
    ) -> tuple[list[tuple[Citation, Clause]], list[str]]:
        """The cheap structural checks, applied to every citation the judge offered.

        Free and deterministic, so they run before any entailment call: only a citation
        that is structurally sound is worth paying a model to reason about.
        """
        retrieved = {hit.clause.chunk_id: hit for hit in hits}
        validated: list[tuple[Citation, Clause]] = []
        failures: list[str] = []

        for offered in verdict.citations[:MAX_CITATIONS]:
            hit = retrieved.get(offered.chunk_id)
            if hit is None:
                failures.append(
                    f"chunk_id {offered.chunk_id[:8]!r} was not among the clauses retrieved"
                )
                continue

            span = offered.quoted_span.strip()
            if len(span) < MIN_SPAN_CHARS:
                failures.append(
                    f"a quoted span was too short to establish anything ({len(span)} chars)"
                )
                continue

            if not span_appears_in(span, hit.clause):
                failures.append(
                    f"the span quoted for {hit.clause.to_citation('x').reference} does not "
                    "appear in that clause"
                )
                continue

            try:
                # Citation re-derives the chunk id from the clause it names, so a
                # citation that renames the article it retrieved cannot be built at all.
                citation = hit.clause.to_citation(span, retrieval_score=hit.score)
            except ValueError as error:
                failures.append(f"citation did not validate: {error}")
                continue
            validated.append((citation, hit.clause))

        return validated, failures

    def _render(self, spec: ProductSpec, hits: list[SearchHit]) -> str:
        """The judge's inputs: retrieved clauses, then the spec, both clearly labelled."""
        clauses = "\n\n".join(
            f"[chunk_id: {hit.clause.chunk_id}]\n"
            f"{hit.clause.to_citation('x').reference}"
            + (f" — {hit.clause.heading}" if hit.clause.heading else "")
            + f"\n{hit.clause.text}"
            for hit in hits
        )
        return (
            "## Retrieved regulation clauses\n\n"
            f"{clauses}\n\n"
            "## Product specification (extracted from a supplier PDF)\n\n"
            f"{spec.model_dump_json(indent=2, exclude={'source'})}"
        )

    def _render_verification(
        self, verdict: JudgeVerdict, citation: Citation, clause: Clause
    ) -> str:
        return (
            "## The clause\n\n"
            f"{citation.reference}\n{clause.text}\n\n"
            "## The quoted span the verdict rests on\n\n"
            f"{citation.quoted_span}\n\n"
            "## The verdict reached\n\n"
            f"{verdict.verdict.value}: {verdict.rationale}"
        )

    def _abstain(
        self,
        rationale: str,
        reason: AbstentionReason,
        usage: list[LlmUsage],
        started: float,
    ) -> RuleResult:
        result = abstain(self.rule_id, rationale, reason)
        return result.model_copy(
            update={
                "llm_usage": usage,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
        )

    def _not_applicable(self, context: RagContext, started: float) -> RuleResult:
        """Nothing to check, which is compliance rather than uncertainty.

        A product making no health claim satisfies the rules on health claims. Returning
        NEEDS_REVIEW here would bury the genuine abstentions — the cases where evidence
        really was missing — under a pile of rules that simply did not apply.
        """
        # A fixed-clause citation, exactly as the deterministic rules build one: the
        # provision is known in advance, so it is named rather than searched for, which
        # also keeps VectorStore at the three methods it is meant to have.
        citation = context.cite(
            self.governing_regulation,
            self.governing_article,
            self.governing_quote,
            self.governing_paragraph,
        )
        return RuleResult(
            rule_id=self.rule_id,
            verdict=Verdict.PASS,
            citations=[citation],
            rationale=(
                "Nothing in the specification triggers this requirement, so it is met vacuously."
            ),
            confidence=0.9,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
