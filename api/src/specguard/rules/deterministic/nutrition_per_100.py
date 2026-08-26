"""NUTRITION_PER_100 — Art. 32(2): is the declaration expressed per 100 g or 100 ml?"""

from __future__ import annotations

from specguard.models.rule import AbstentionReason, RuleId, RuleResult, Verdict
from specguard.models.spec import NutrientBasis, ProductSpec
from specguard.rules.base import RuleContext, abstain, confident

REGULATION = "Regulation (EU) No 1169/2011"
QUOTE = (
    "The energy value and the amount of nutrients referred to in Article 30(1) to (5) shall be "
    "expressed per 100 g or per 100 ml"
)

REQUIRED_BASES = {NutrientBasis.PER_100G, NutrientBasis.PER_100ML}


class NutritionPer100Rule:
    """The per-100 basis is mandatory; a per-portion figure is only ever additional."""

    rule_id = RuleId.NUTRITION_PER_100

    def evaluate(self, spec: ProductSpec, context: RuleContext) -> RuleResult:
        if spec.nutrition is None:
            return abstain(
                self.rule_id,
                "No nutrition declaration, so there is no basis to check.",
                AbstentionReason.FIELD_MISSING,
            )

        # Both the declaration and the basis within it have to have been read well: a
        # nutrition table we could barely read gives no grounds to judge its basis.
        basis_field = spec.nutrition.value.basis
        if not confident(spec.nutrition, context) or not confident(basis_field, context):
            return abstain(
                self.rule_id,
                "The basis of the nutrition declaration was not read with enough confidence.",
                AbstentionReason.LOW_EXTRACTION_CONFIDENCE,
            )

        citation = context.cite(REGULATION, "32", QUOTE, paragraph="2")
        basis = basis_field.value

        if basis in REQUIRED_BASES:
            return RuleResult(
                rule_id=self.rule_id,
                verdict=Verdict.PASS,
                citations=[citation],
                rationale=(
                    f"The nutrition declaration is expressed {basis.value.replace('_', ' ')}."
                ),
                confidence=0.97,
            )

        return RuleResult(
            rule_id=self.rule_id,
            verdict=Verdict.FAIL,
            citations=[citation],
            rationale=(
                f"The nutrition declaration is expressed {basis.value.replace('_', ' ')} only. "
                "Art. 32(2) requires a per 100 g or per 100 ml declaration; a per-portion or "
                "per-pack figure may only be given in addition to it."
            ),
            suggested_fix=(
                "Restate the nutrition declaration per 100 g or per 100 ml. Keep the "
                "per-portion figures alongside it if they are wanted."
            ),
            confidence=0.97,
        )
