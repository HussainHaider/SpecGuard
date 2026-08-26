"""The weekly watcher's decision: which stored checks a corpus change invalidates.

The whole point of this is selectivity. Re-running every stored check on every
consolidation is wasteful; re-running none makes the watcher decorative. These tests pin
the rule that decides between the two, and the property that makes it safe to run weekly:
running it twice changes nothing.
"""

from __future__ import annotations

import pytest

from specguard.corpus.sources import source_version_for
from specguard.models.common import Language
from specguard.models.corpus import Clause
from specguard.ops.affected import assess

REGULATION = "Regulation (EU) No 1169/2011"
SPAN = "the name of the food shall be its legal name"


def clause(text: str, *, article: str = "17", paragraph: str | None = "1") -> Clause:
    return Clause.build(
        regulation=REGULATION,
        celex="02011R1169-20180101",
        article=article,
        paragraph=paragraph,
        heading="Name of the food",
        language=Language.EN,
        source_version=source_version_for(REGULATION, Language.EN),
        text=text,
    )


def report(span: str = SPAN, *, article: str = "17", paragraph: str | None = "1") -> dict:
    return {
        "spec": {"language": "en"},
        "overall_verdict": "PASS",
        "results": [
            {
                "rule_id": "LEGAL_NAME_AND_QUID",
                "verdict": "PASS",
                "citations": [
                    {
                        "regulation": REGULATION,
                        "article": article,
                        "paragraph": paragraph,
                        "quoted_span": span,
                    }
                ],
            }
        ],
    }


def index(*clauses: Clause) -> dict:
    return {(c.regulation, c.article, c.paragraph, c.language.value): c for c in clauses}


class TestWhatCountsAsAffected:
    def test_an_unchanged_clause_leaves_the_check_alone(self):
        assert assess(report(), index(clause(f"1. {SPAN}, and where none exists…"))) == []

    def test_reformatting_around_the_span_is_not_a_change(self):
        # The comparison is whitespace-normalised, the same rule the verification pass
        # applies. A re-wrapped consolidation is not an amendment.
        reflowed = clause(f"1.   {SPAN.upper().replace(' ', '  ')} ,\n and where none exists…")
        assert assess(report(span=SPAN.upper()), index(reflowed)) == []

    def test_the_relied_on_words_changing_makes_it_affected(self):
        current = clause("1. The food shall be described by its customary designation.")
        reasons = assess(report(), index(current))
        assert len(reasons) == 1
        assert "has changed" in reasons[0]

    def test_the_clause_disappearing_makes_it_affected(self):
        reasons = assess(report(), index(clause("unrelated", article="9", paragraph="1")))
        assert len(reasons) == 1
        assert "no longer in the corpus" in reasons[0]

    def test_a_clause_the_report_never_cited_is_irrelevant(self):
        # A consolidation that rewrites Article 40 does not invalidate a verdict that
        # rested on Article 17. Most consolidations are exactly this case.
        current = index(
            clause(f"1. {SPAN}, and where none exists…"),
            clause("something else entirely", article="40", paragraph=None),
        )
        assert assess(report(), current) == []


class TestIdempotence:
    def test_assessing_twice_gives_the_same_answer(self):
        """The watcher runs weekly and may fail partway. Re-running must be safe."""
        current = index(clause("1. The food shall be described by its customary designation."))
        first = assess(report(), current)
        second = assess(report(), current)
        assert first == second

    def test_it_reads_nothing_and_writes_nothing(self):
        """The worklist is derived from the corpus, never stored.

        A list written down by a previous run would drift the moment a run died halfway
        through re-queueing; recomputing it means a failed run is resumed by running it
        again.
        """
        import inspect

        import specguard.ops.affected as module

        source = inspect.getsource(module)
        for forbidden in ("open(", ".write_text(", "json.dump("):
            assert forbidden not in source, forbidden


class TestChunkIdSurvivesReindex:
    def test_the_same_clause_reindexes_to_the_same_id(self):
        """Non-negotiable #3, which is what makes a weekly re-index safe at all.

        If a re-index moved ids, every citation stored in Postgres would stop resolving
        against Qdrant and every stored report would be unreadable — so the watcher would
        be destroying the archive it exists to protect.
        """
        first = clause(f"1. {SPAN}")
        again = clause(f"1. {SPAN}")
        assert first.chunk_id == again.chunk_id

    def test_a_different_paragraph_is_a_different_id(self):
        assert clause("x", paragraph="1").chunk_id != clause("x", paragraph="2").chunk_id


@pytest.mark.parametrize("verdict", ["FAIL", "NEEDS_REVIEW"])
def test_only_passed_checks_are_the_default_target(verdict):
    """A check that already failed or abstained is going to a person anyway.

    The interesting case is the one somebody signed off on and would never look at again.
    """
    payload = report()
    payload["overall_verdict"] = verdict
    # assess() itself is verdict-agnostic; the filter lives in find_affected, and this
    # records that the distinction is deliberate rather than forgotten.
    assert assess(payload, index(clause("wholly rewritten"))) != []
