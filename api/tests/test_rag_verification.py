"""The verification pass, tested without a model or a network.

Every structural check is pure Python, so the cases that matter most — a judge citing a
clause it never saw, quoting text that is not in the clause it names, or quoting three
words and calling it evidence — are testable directly. They are the checks that stop a
fabricated citation reaching a report, so they get tested at that level rather than only
through a replayed fixture.
"""

from __future__ import annotations

import pytest

from specguard.models.common import Language
from specguard.models.corpus import Clause
from specguard.models.rule import RuleId, Verdict
from specguard.rules.rag.base import (
    MAX_CITATIONS,
    MIN_SPAN_CHARS,
    JudgeCitation,
    JudgeVerdict,
    RagRule,
    span_appears_in,
)
from specguard.vectorstore.protocol import SearchHit

REGULATION = "Regulation (EU) No 1169/2011"
SOURCE_VERSION = "02011R1169-20180101-en"
CLAUSE_TEXT = (
    "The energy value and the amount of nutrients referred to in Article 30(1) to (5) "
    "shall be expressed per 100 g or per 100 ml."
)


def _clause(article: str = "32", paragraph: str | None = "2", text: str = CLAUSE_TEXT) -> Clause:
    return Clause.build(
        regulation=REGULATION,
        celex="02011R1169-20180101",
        article=article,
        paragraph=paragraph,
        heading="Expression per 100 g or per 100 ml",
        language=Language.EN,
        source_version=SOURCE_VERSION,
        text=text,
    )


class _Rule(RagRule):
    rule_id = RuleId.ORIGIN_DECLARATION
    judge_prompt = "judge_origin_declaration"
    governing_regulation = REGULATION
    governing_article = "26"
    governing_paragraph = "2"
    governing_quote = "Indication of the country of origin"


def _verdict(*citations: JudgeCitation) -> JudgeVerdict:
    return JudgeVerdict(
        verdict=Verdict.PASS,
        rationale="because the clause says so",
        citations=list(citations),
        confidence=0.9,
    )


class TestSpanMatching:
    def test_accepts_a_verbatim_span(self) -> None:
        assert span_appears_in("shall be expressed per 100 g", _clause())

    def test_tolerates_rewrapped_whitespace(self) -> None:
        # The clause text is re-wrapped before the model sees it, so line breaks are
        # normalised. Nothing else is relaxed.
        assert span_appears_in("shall be   expressed\nper 100 g", _clause())

    def test_is_case_insensitive(self) -> None:
        assert span_appears_in("SHALL BE EXPRESSED PER 100 G", _clause())

    def test_rejects_a_paraphrase(self) -> None:
        # The whole point: a fluent restatement is not a quotation, and this is what
        # stops an invented citation from passing as a real one.
        assert not span_appears_in("must be given per one hundred grams", _clause())

    def test_rejects_text_from_a_different_clause(self) -> None:
        assert not span_appears_in("country of origin or place of provenance", _clause())


class TestCitationValidation:
    def test_accepts_a_sound_citation(self) -> None:
        clause = _clause()
        hits = [SearchHit(clause=clause, score=0.9)]
        verdict = _verdict(
            JudgeCitation(
                chunk_id=clause.chunk_id, quoted_span="shall be expressed per 100 g or per 100 ml"
            )
        )
        validated, failures = _Rule()._validate_citations(verdict, hits)
        assert len(validated) == 1
        assert not failures
        citation, _ = validated[0]
        assert citation.chunk_id == clause.chunk_id
        assert citation.retrieval_score == pytest.approx(0.9)

    def test_rejects_a_chunk_that_was_never_retrieved(self) -> None:
        # A judge must not be able to cite a clause it did not see.
        hits = [SearchHit(clause=_clause(), score=0.9)]
        verdict = _verdict(
            JudgeCitation(chunk_id=_clause(article="9").chunk_id, quoted_span="a" * 40)
        )
        validated, failures = _Rule()._validate_citations(verdict, hits)
        assert not validated
        assert "not among the clauses retrieved" in failures[0]

    def test_rejects_a_span_that_is_not_in_the_clause(self) -> None:
        clause = _clause()
        hits = [SearchHit(clause=clause, score=0.9)]
        verdict = _verdict(
            JudgeCitation(
                chunk_id=clause.chunk_id, quoted_span="must be given per one hundred grams"
            )
        )
        validated, failures = _Rule()._validate_citations(verdict, hits)
        assert not validated
        assert "does not appear in that clause" in failures[0]

    def test_rejects_a_span_too_short_to_prove_anything(self) -> None:
        # "shall be" appears in half the regulation and establishes nothing.
        clause = _clause()
        hits = [SearchHit(clause=clause, score=0.9)]
        verdict = _verdict(JudgeCitation(chunk_id=clause.chunk_id, quoted_span="shall be"))
        validated, failures = _Rule()._validate_citations(verdict, hits)
        assert not validated
        assert "too short" in failures[0]

    def test_keeps_the_sound_citations_and_reports_the_rest(self) -> None:
        clause = _clause()
        hits = [SearchHit(clause=clause, score=0.9)]
        verdict = _verdict(
            JudgeCitation(chunk_id=clause.chunk_id, quoted_span="shall be expressed per 100 g"),
            JudgeCitation(chunk_id=clause.chunk_id, quoted_span="invented text not in the clause"),
        )
        validated, failures = _Rule()._validate_citations(verdict, hits)
        assert len(validated) == 1
        assert len(failures) == 1

    def test_ignores_citations_beyond_the_cap(self) -> None:
        clause = _clause()
        hits = [SearchHit(clause=clause, score=0.9)]
        span = "shall be expressed per 100 g"
        verdict = _verdict(*[JudgeCitation(chunk_id=clause.chunk_id, quoted_span=span)] * 6)
        validated, _ = _Rule()._validate_citations(verdict, hits)
        assert len(validated) <= MAX_CITATIONS


class TestSchema:
    def test_a_judge_may_return_no_citation_at_all(self) -> None:
        # It must be able to say "I could not find support", which is what makes
        # NEEDS_REVIEW reachable rather than forcing a fabricated citation.
        verdict = JudgeVerdict(verdict=Verdict.NEEDS_REVIEW, rationale="no clause settled it")
        assert verdict.citations == []

    def test_minimum_span_is_long_enough_to_be_evidence(self) -> None:
        assert MIN_SPAN_CHARS >= 20
