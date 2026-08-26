"""NUTRITION_ARITHMETIC — is the declared energy consistent with the macronutrients?"""

from __future__ import annotations

from specguard.models.rule import AbstentionReason, RuleId, RuleResult, Verdict
from specguard.models.spec import NutritionDeclaration, ProductSpec
from specguard.rules.base import RuleContext, abstain, confident

REGULATION = "Regulation (EU) No 1169/2011"
QUOTE = "The energy value to be declared shall be calculated using the following conversion factors"

#: Annex XIV, kJ and kcal per gram.
FACTORS_KJ: dict[str, float] = {
    "carbohydrate_g": 17.0,
    "polyols_g": 10.0,
    "protein_g": 17.0,
    "fat_g": 37.0,
    "fibre_g": 8.0,
    "alcohol_g": 29.0,
    "organic_acids_g": 13.0,
    "erythritol_g": 0.0,
}
FACTORS_KCAL: dict[str, float] = {
    "carbohydrate_g": 4.0,
    "polyols_g": 2.4,
    "protein_g": 4.0,
    "fat_g": 9.0,
    "fibre_g": 2.0,
    "alcohol_g": 7.0,
    "organic_acids_g": 3.0,
    "erythritol_g": 0.0,
}

#: 1 kcal = 4.184 kJ. Used to cross-check the two declared figures against each other.
KJ_PER_KCAL = 4.184


def _computed(nutrition: NutritionDeclaration, factors: dict[str, float]) -> float:
    """Energy implied by the declared macros.

    Carbohydrate is taken as-is: the declaration is "carbohydrate" excluding fibre under
    Annex I, so subtracting fibre here would double-count it.
    """
    total = 0.0
    for attribute, factor in factors.items():
        field = getattr(nutrition, attribute, None)
        if field is not None:
            total += field.value * factor
    return total


class NutritionArithmeticRule:
    """Recomputes energy from the macros with the Annex XIV factors."""

    rule_id = RuleId.NUTRITION_ARITHMETIC

    def evaluate(self, spec: ProductSpec, context: RuleContext) -> RuleResult:
        if spec.nutrition is None:
            return abstain(
                self.rule_id,
                "No nutrition declaration to check. Its absence is MANDATORY_FIELDS' finding.",
                AbstentionReason.FIELD_MISSING,
            )
        if not confident(spec.nutrition, context):
            return abstain(
                self.rule_id,
                "The nutrition declaration was not read with enough confidence to recompute.",
                AbstentionReason.LOW_EXTRACTION_CONFIDENCE,
            )

        nutrition = spec.nutrition.value
        if nutrition.energy_kj is None and nutrition.energy_kcal is None:
            return abstain(
                self.rule_id,
                "No energy value declared, so there is nothing to reconcile.",
                AbstentionReason.FIELD_MISSING,
            )

        citation = context.cite(REGULATION, "Annex XIV", QUOTE)
        tolerance = context.energy_tolerance_pct
        metrics: dict[str, float] = {"tolerance_pct": tolerance}
        problems: list[str] = []

        for label, field, factors in (
            ("kJ", nutrition.energy_kj, FACTORS_KJ),
            ("kcal", nutrition.energy_kcal, FACTORS_KCAL),
        ):
            if field is None:
                continue
            computed = _computed(nutrition, factors)
            metrics[f"declared_{label}"] = round(field.value, 1)
            metrics[f"computed_{label}"] = round(computed, 1)
            if computed <= 0:
                continue
            deviation = abs(field.value - computed) / computed * 100
            metrics[f"deviation_{label}_pct"] = round(deviation, 1)
            if deviation > tolerance:
                problems.append(
                    f"declared {field.value:.0f} {label} against {computed:.0f} {label} "
                    f"computed from the macronutrients ({deviation:.1f}% out)"
                )

        # The two declared figures must also agree with each other.
        if nutrition.energy_kj is not None and nutrition.energy_kcal is not None:
            implied = nutrition.energy_kcal.value * KJ_PER_KCAL
            if implied > 0:
                drift = abs(nutrition.energy_kj.value - implied) / implied * 100
                metrics["kj_kcal_drift_pct"] = round(drift, 1)
                if drift > tolerance:
                    problems.append(
                        f"declared {nutrition.energy_kj.value:.0f} kJ and "
                        f"{nutrition.energy_kcal.value:.0f} kcal, which are not the same "
                        f"quantity ({drift:.1f}% apart)"
                    )

        if problems:
            return RuleResult(
                rule_id=self.rule_id,
                verdict=Verdict.FAIL,
                citations=[citation],
                rationale="Energy declaration is inconsistent: " + "; ".join(problems) + ".",
                suggested_fix=(
                    "Recalculate the energy value from the declared macronutrients using the "
                    "Annex XIV conversion factors, or correct the macronutrient values."
                ),
                confidence=0.9,
                metrics=metrics,
            )

        return RuleResult(
            rule_id=self.rule_id,
            verdict=Verdict.PASS,
            citations=[citation],
            rationale=(
                "Declared energy agrees with the value computed from the macronutrients "
                f"using the Annex XIV factors, within {tolerance:.0f}%."
            ),
            confidence=0.9,
            metrics=metrics,
        )
