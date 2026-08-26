"""ALLERGEN_EMPHASIS — Art. 21(1)(b): are Annex II allergens emphasised in the list?"""

from __future__ import annotations

from specguard.models.rule import AbstentionReason, RuleId, RuleResult, Verdict
from specguard.models.spec import EmphasisStyle, Ingredient, ProductSpec
from specguard.rules.base import RuleContext, abstain, confident
from specguard.rules.deterministic.allergens import allergens_in

REGULATION = "Regulation (EU) No 1169/2011"
QUOTE = (
    "emphasised through a typeset that clearly distinguishes it from the rest of the list of "
    "ingredients, for instance by means of the font, style or background colour"
)


def _walk(ingredients: list[Ingredient]) -> list[Ingredient]:
    """Every ingredient including those inside compound ingredients.

    Art. 21 applies to any Annex II substance "used in the manufacture", so an allergen
    inside a compound ingredient is squarely in scope. Checking only the top level would
    miss exactly the cases that matter.
    """
    flat: list[Ingredient] = []
    for ingredient in ingredients:
        flat.append(ingredient)
        flat.extend(_walk(ingredient.sub_ingredients))
    return flat


class AllergenEmphasisRule:
    """Every Annex II allergen in the list must be typographically distinguished."""

    rule_id = RuleId.ALLERGEN_EMPHASIS

    def evaluate(self, spec: ProductSpec, context: RuleContext) -> RuleResult:
        if spec.ingredients is None:
            return abstain(
                self.rule_id,
                "No ingredient list to check for allergen emphasis.",
                AbstentionReason.FIELD_MISSING,
            )
        if not confident(spec.ingredients, context):
            return abstain(
                self.rule_id,
                "The ingredient list was not read with enough confidence to judge emphasis.",
                AbstentionReason.LOW_EXTRACTION_CONFIDENCE,
            )

        ingredient_list = spec.ingredients.value
        citation = context.cite(REGULATION, "21", QUOTE, paragraph="1")

        allergens = allergens_in(ingredient_list.raw_text, spec.language)
        if not allergens:
            return RuleResult(
                rule_id=self.rule_id,
                verdict=Verdict.PASS,
                citations=[citation],
                rationale=(
                    "No Annex II substance appears in the ingredient list, so no emphasis "
                    "is required."
                ),
                confidence=0.9,
                metrics={"allergens_found": 0.0},
            )

        unemphasised: list[str] = []
        for ingredient in _walk([item.value for item in ingredient_list.items]):
            matched = allergens_in(ingredient.name, spec.language)
            if matched and not ingredient.emphasised:
                unemphasised.append(ingredient.name)

        metrics = {
            "allergens_found": float(len(allergens)),
            "unemphasised_count": float(len(unemphasised)),
        }

        if unemphasised:
            return RuleResult(
                rule_id=self.rule_id,
                verdict=Verdict.FAIL,
                citations=[citation],
                rationale=(
                    "These ingredients are Annex II allergens but are not distinguished from "
                    f"the rest of the ingredient list: {', '.join(unemphasised)}."
                ),
                suggested_fix=(
                    "Emphasise "
                    + ", ".join(unemphasised)
                    + " in the ingredient list, for example in bold or capitals, so each "
                    "stands out from the surrounding text."
                ),
                confidence=0.92,
                metrics=metrics,
            )

        styles = {
            item.value.emphasis_style
            for item in ingredient_list.items
            if item.value.emphasised and item.value.emphasis_style is not EmphasisStyle.NONE
        }
        style_note = f" ({', '.join(sorted(s.value for s in styles))})" if styles else ""
        return RuleResult(
            rule_id=self.rule_id,
            verdict=Verdict.PASS,
            citations=[citation],
            rationale=(
                f"All {len(allergens)} Annex II substance(s) in the ingredient list are "
                f"emphasised{style_note}."
            ),
            confidence=0.92,
            metrics=metrics,
        )
