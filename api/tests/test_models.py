"""The core models carry the project's non-negotiables. These tests hold them there."""

import json
import uuid

import pytest
from pydantic import ValidationError

from specguard.models import (
    AbstentionReason,
    CheckReport,
    Citation,
    Claim,
    ClaimKind,
    EmphasisStyle,
    ExtractedField,
    Ingredient,
    IngredientList,
    LlmUsage,
    NutrientBasis,
    NutritionDeclaration,
    ProductSpec,
    RuleId,
    RuleKind,
    RuleResult,
    SourceDocument,
    Verdict,
    chunk_id_for,
)

REGULATION = "Regulation (EU) No 1169/2011"
CORPUS_VERSION = "2024-11-01"


@pytest.fixture
def citation() -> Citation:
    return Citation.for_clause(
        regulation=REGULATION,
        article="21",
        paragraph="1",
        point="b",
        quoted_span="emphasised by a typeset that clearly distinguishes it",
        source_version=CORPUS_VERSION,
    )


@pytest.fixture
def spec() -> ProductSpec:
    return ProductSpec(
        source=SourceDocument(
            filename="strawberry-yogurt.pdf", sha256="0" * 64, page_count=2, byte_size=1024
        ),
        extractor_model="claude-sonnet-5",
        extractor_prompt_version="extract@v1",
        legal_name=ExtractedField[str](value="Strawberry yogurt", confidence=0.94, page=1),
        ingredients=ExtractedField[IngredientList](
            value=IngredientList(
                raw_text="Yogurt (MILK), strawberries 12%, sugar",
                items=[
                    ExtractedField[Ingredient](
                        value=Ingredient(
                            name="Yogurt",
                            percentage=80.0,
                            sub_ingredients=[
                                Ingredient(
                                    name="milk",
                                    emphasised=True,
                                    emphasis_style=EmphasisStyle.UPPERCASE,
                                )
                            ],
                        ),
                        confidence=0.9,
                    )
                ],
                dominant_emphasis_style=EmphasisStyle.UPPERCASE,
            ),
            confidence=0.88,
            page=1,
        ),
        nutrition=ExtractedField[NutritionDeclaration](
            value=NutritionDeclaration(
                basis=ExtractedField[NutrientBasis](value=NutrientBasis.PER_100G, confidence=0.99),
                fat_g=ExtractedField[float](value=3.1, confidence=0.9),
            ),
            confidence=0.9,
        ),
        claims=[
            ExtractedField[Claim](
                value=Claim(text="source of fibre", kind=ClaimKind.NUTRITION, nutrient="fibre"),
                confidence=0.8,
            )
        ],
    )


class TestChunkId:
    """Non-negotiable #3: chunk_id is deterministic and derived from the clause."""

    def test_is_stable_across_cosmetic_drift(self) -> None:
        assert chunk_id_for(REGULATION, "21", "1", CORPUS_VERSION) == chunk_id_for(
            REGULATION, "  21 ", "1", CORPUS_VERSION
        )

    def test_is_a_valid_qdrant_point_id(self) -> None:
        # Qdrant point ids must be an unsigned int or a UUID; ours is the latter,
        # so the chunk id *is* the point id with no mapping table in between.
        assert uuid.UUID(chunk_id_for(REGULATION, "9", None, CORPUS_VERSION))

    def test_changes_with_corpus_version(self) -> None:
        assert chunk_id_for(REGULATION, "9", None, "2024-11-01") != chunk_id_for(
            REGULATION, "9", None, "2025-06-01"
        )

    def test_paragraph_is_part_of_the_identity(self) -> None:
        assert chunk_id_for(REGULATION, "32", "2", CORPUS_VERSION) != chunk_id_for(
            REGULATION, "32", "3", CORPUS_VERSION
        )


class TestCitation:
    def test_rejects_a_chunk_id_that_does_not_match_its_clause(self) -> None:
        # A judge that names Art. 9 while quoting the chunk it retrieved for Art. 21
        # must not be able to construct the citation at all.
        with pytest.raises(ValidationError, match="does not match the clause"):
            Citation(
                regulation=REGULATION,
                article="9",
                chunk_id=chunk_id_for(REGULATION, "21", "1", CORPUS_VERSION),
                quoted_span="...",
                source_version=CORPUS_VERSION,
            )

    def test_reference_renders_article_and_point(self, citation: Citation) -> None:
        assert citation.reference == f"{REGULATION} Art. 21(1)(b)"

    def test_reference_renders_an_annex(self) -> None:
        annex = Citation.for_clause(
            regulation=REGULATION,
            article="Annex XIV",
            quoted_span="fat 37 kJ/g",
            source_version=CORPUS_VERSION,
        )
        assert annex.reference == f"{REGULATION} Annex XIV"


class TestRuleResult:
    def test_kind_is_derived_from_the_rule_id(self, citation: Citation) -> None:
        result = RuleResult(
            rule_id=RuleId.ALLERGEN_EMPHASIS,
            verdict=Verdict.PASS,
            citations=[citation],
            rationale="MILK is capitalised in the ingredient list.",
            confidence=0.97,
        )
        assert result.kind is RuleKind.DETERMINISTIC

    def test_verdict_without_a_citation_is_rejected(self) -> None:
        # Non-negotiable #1: a rule that cannot cite must return NEEDS_REVIEW.
        with pytest.raises(ValidationError, match="without a citation"):
            RuleResult(
                rule_id=RuleId.MANDATORY_FIELDS,
                verdict=Verdict.PASS,
                rationale="Everything looks present.",
                confidence=0.9,
            )

    def test_fail_without_a_suggested_fix_is_rejected(self, citation: Citation) -> None:
        with pytest.raises(ValidationError, match="without a suggested_fix"):
            RuleResult(
                rule_id=RuleId.MANDATORY_FIELDS,
                verdict=Verdict.FAIL,
                citations=[citation],
                rationale="Net quantity is missing.",
                confidence=0.9,
            )

    def test_abstention_must_say_why(self) -> None:
        with pytest.raises(ValidationError, match="without an abstention_reason"):
            RuleResult(
                rule_id=RuleId.ORIGIN_DECLARATION,
                verdict=Verdict.NEEDS_REVIEW,
                rationale="Not sure.",
                confidence=0.2,
            )

    def test_a_deterministic_rule_cannot_carry_an_llm_call(self, citation: Citation) -> None:
        # Non-negotiable #2, enforced in the type rather than left to code review.
        with pytest.raises(ValidationError, match="must not call a model"):
            RuleResult(
                rule_id=RuleId.NUTRITION_ARITHMETIC,
                verdict=Verdict.PASS,
                citations=[citation],
                rationale="Energy matches the Annex XIV computation.",
                confidence=0.99,
                llm_usage=[
                    LlmUsage(
                        provider="anthropic",
                        model="claude-sonnet-5",
                        prompt_version="judge@v1",
                        input_tokens=10,
                        output_tokens=5,
                        cost_usd=0.001,
                        latency_ms=120,
                    )
                ],
            )

    def test_abstention_needs_no_citation(self) -> None:
        result = RuleResult(
            rule_id=RuleId.ORIGIN_DECLARATION,
            verdict=Verdict.NEEDS_REVIEW,
            rationale="No clause was retrieved above the score threshold.",
            confidence=0.2,
            abstention_reason=AbstentionReason.NO_RELEVANT_CLAUSE_RETRIEVED,
        )
        assert result.citations == []


class TestCheckReport:
    @staticmethod
    def _result(rule_id: RuleId, verdict: Verdict, citation: Citation) -> RuleResult:
        return RuleResult(
            rule_id=rule_id,
            verdict=verdict,
            citations=[] if verdict is Verdict.NEEDS_REVIEW else [citation],
            rationale="because",
            suggested_fix="do the thing" if verdict is Verdict.FAIL else None,
            confidence=0.8,
            abstention_reason=(
                AbstentionReason.CITATION_UNVERIFIED if verdict is Verdict.NEEDS_REVIEW else None
            ),
        )

    def _report(self, spec: ProductSpec, results: list[RuleResult]) -> CheckReport:
        return CheckReport(
            spec=spec,
            results=results,
            corpus_version=CORPUS_VERSION,
            graph_version="graph@v1",
        )

    @pytest.mark.parametrize(
        ("verdicts", "expected"),
        [
            ([Verdict.PASS, Verdict.PASS], Verdict.PASS),
            ([Verdict.PASS, Verdict.NEEDS_REVIEW], Verdict.NEEDS_REVIEW),
            ([Verdict.NEEDS_REVIEW, Verdict.FAIL], Verdict.FAIL),
        ],
    )
    def test_overall_verdict_takes_the_worst(
        self,
        spec: ProductSpec,
        citation: Citation,
        verdicts: list[Verdict],
        expected: Verdict,
    ) -> None:
        rule_ids = [RuleId.MANDATORY_FIELDS, RuleId.ORIGIN_DECLARATION]
        results = [self._result(r, v, citation) for r, v in zip(rule_ids, verdicts, strict=True)]
        assert self._report(spec, results).overall_verdict is expected

    def test_counts_always_carry_every_verdict_key(
        self, spec: ProductSpec, citation: Citation
    ) -> None:
        report = self._report(spec, [self._result(RuleId.MANDATORY_FIELDS, Verdict.PASS, citation)])
        assert report.counts == {Verdict.PASS: 1, Verdict.FAIL: 0, Verdict.NEEDS_REVIEW: 0}

    def test_duplicate_rule_results_are_rejected(
        self, spec: ProductSpec, citation: Citation
    ) -> None:
        result = self._result(RuleId.MANDATORY_FIELDS, Verdict.PASS, citation)
        with pytest.raises(ValidationError, match="duplicate rule results"):
            self._report(spec, [result, result])

    def test_survives_a_json_round_trip(self, spec: ProductSpec, citation: Citation) -> None:
        # A stored report is read back out of Postgres; computed fields must not
        # make the model reject its own output.
        report = self._report(spec, [self._result(RuleId.MANDATORY_FIELDS, Verdict.PASS, citation)])
        restored = CheckReport.model_validate(json.loads(report.model_dump_json()))
        assert restored.overall_verdict is report.overall_verdict
        assert restored.spec.legal_name is not None
        assert restored.spec.legal_name.value == "Strawberry yogurt"

    def test_unknown_fields_are_still_rejected(self, spec: ProductSpec) -> None:
        with pytest.raises(ValidationError):
            CheckReport.model_validate(
                {
                    "spec": spec.model_dump(),
                    "results": [],
                    "corpus_version": CORPUS_VERSION,
                    "graph_version": "graph@v1",
                    "surprise": True,
                }
            )


class TestProductSpec:
    def test_extraction_schema_is_generatable_for_structured_output(self) -> None:
        # Non-negotiable #7: every model call is schema-constrained, so ProductSpec
        # must be expressible as a JSON schema despite the recursive Ingredient.
        schema = ProductSpec.model_json_schema()
        assert "Ingredient" in schema["$defs"]

    def test_a_field_carries_its_own_confidence_and_provenance(self, spec: ProductSpec) -> None:
        assert spec.legal_name is not None
        assert spec.legal_name.confidence == pytest.approx(0.94)
        assert spec.legal_name.page == 1

    def test_records_are_immutable(self, spec: ProductSpec) -> None:
        with pytest.raises(ValidationError):
            spec.extractor_model = "something-else"  # type: ignore[misc]
