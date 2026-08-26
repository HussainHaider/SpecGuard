"""MANDATORY_FIELDS — Art. 9(1): are all mandatory particulars present?"""

from __future__ import annotations

from specguard.models.rule import AbstentionReason, RuleId, RuleResult, Verdict
from specguard.models.spec import ProductSpec
from specguard.rules.base import RuleContext, abstain, confident

REGULATION = "Regulation (EU) No 1169/2011"
QUOTE = (
    "In accordance with Articles 10 to 35 and subject to the exceptions contained in this "
    "Chapter, indication of the following particulars shall be mandatory"
)

#: Art. 9(1)(a)-(l). Point (i) origin and (j) instructions are conditional and are left
#: to ORIGIN_DECLARATION and to the trigger below rather than demanded unconditionally.
UNCONDITIONAL: tuple[tuple[str, str], ...] = (
    ("legal_name", "name of the food, Art. 9(1)(a)"),
    ("ingredients", "list of ingredients, Art. 9(1)(b)"),
    ("net_quantity", "net quantity, Art. 9(1)(e)"),
    ("durability", "date of minimum durability or use-by date, Art. 9(1)(f)"),
    ("storage_conditions", "storage conditions, Art. 9(1)(g)"),
    ("business_operator", "operator name and address, Art. 9(1)(h)"),
    ("nutrition", "nutrition declaration, Art. 9(1)(l)"),
)


class MandatoryFieldsRule:
    """Presence of the Art. 9 particulars. Presence only — other rules judge content."""

    rule_id = RuleId.MANDATORY_FIELDS

    def evaluate(self, spec: ProductSpec, context: RuleContext) -> RuleResult:
        missing: list[str] = []
        unreadable: list[str] = []

        for attribute, description in UNCONDITIONAL:
            field = getattr(spec, attribute)
            if field is None:
                missing.append(description)
            elif not confident(field, context):
                # Present but badly read. Calling that a failure would blame the
                # supplier for our own extraction, so it abstains instead.
                unreadable.append(description)

        # Art. 9(1)(k) applies only above 1.2% vol, so its absence is not a failure on a
        # yogurt: the field is its own trigger and needs no separate check here.

        # An operator with no address is a partial particular, not a present one:
        # Art. 9(1)(h) requires the name *and* the address.
        operator = spec.business_operator
        if operator is not None and confident(operator, context) and not operator.value.address:
            missing.append("operator address, Art. 9(1)(h)")

        citation = context.cite(REGULATION, "9", QUOTE, paragraph="1")

        if unreadable:
            return abstain(
                self.rule_id,
                "Could not read "
                + ", ".join(unreadable)
                + " with enough confidence to judge presence.",
                AbstentionReason.LOW_EXTRACTION_CONFIDENCE,
            )

        if missing:
            return RuleResult(
                rule_id=self.rule_id,
                verdict=Verdict.FAIL,
                citations=[citation],
                rationale=f"Missing mandatory particulars: {', '.join(missing)}.",
                suggested_fix=(
                    "Add the missing particulars to the specification: " + "; ".join(missing) + "."
                ),
                confidence=0.95,
                metrics={"missing_count": float(len(missing))},
            )

        return RuleResult(
            rule_id=self.rule_id,
            verdict=Verdict.PASS,
            citations=[citation],
            rationale="All mandatory particulars required by Art. 9(1) are present.",
            confidence=0.95,
            metrics={"missing_count": 0.0},
        )
