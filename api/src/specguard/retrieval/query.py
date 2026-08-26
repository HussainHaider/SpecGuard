"""Build the retrieval query for each RAG rule.

A rule's query is drawn from the spec fields that rule actually reasons about. That
matters more than it sounds: retrieving on the whole document would return whatever the
document talks about most, not the clause the rule needs. "source of fibre, 1.2 g fibre
per 100 g, conditions of use" finds the conditions of use; the spec sheet as a whole
finds the ingredients list.

The ``query:`` prefix e5 expects is applied by the encoder, which knows whether the
configured model was trained with it — never here.
"""

from __future__ import annotations

from specguard.models.rule import RuleId
from specguard.models.spec import ClaimKind, ProductSpec

MAX_QUERY_CHARS = 400


def _claims(spec: ProductSpec, kind: ClaimKind) -> list[str]:
    return [claim.value.text for claim in spec.claims if claim.value.kind is kind]


def _named_ingredients(spec: ProductSpec, limit: int = 4) -> list[str]:
    if spec.ingredients is None:
        return []
    return [item.value.name for item in spec.ingredients.value.items[:limit]]


def _nutrition_terms(spec: ProductSpec) -> list[str]:
    """The declared figures a conditions-of-use clause is judged against."""
    if spec.nutrition is None:
        return []
    nutrition = spec.nutrition.value
    terms = []
    for label, field in (
        ("fibre", nutrition.fibre_g),
        ("fat", nutrition.fat_g),
        ("saturates", nutrition.saturates_g),
        ("sugars", nutrition.sugars_g),
        ("salt", nutrition.salt_g),
        ("protein", nutrition.protein_g),
    ):
        if field is not None:
            terms.append(f"{label} {field.value:g} g per 100")
    return terms


def build_query(rule_id: RuleId, spec: ProductSpec) -> str:
    """The search string for one rule against one spec.

    Returns an empty string when the rule has nothing to look up — a product making no
    claims needs no conditions-of-use clause — and the caller treats that as "the rule
    does not apply here" rather than searching for nothing.
    """
    parts: list[str] = []

    if rule_id is RuleId.NUTRITION_CLAIM_CONDITIONS:
        claims = _claims(spec, ClaimKind.NUTRITION)
        if not claims:
            return ""
        parts = [*claims, "conditions of use for nutrition claims", *_nutrition_terms(spec)]

    elif rule_id is RuleId.HEALTH_CLAIM_AUTHORISED:
        claims = _claims(spec, ClaimKind.HEALTH)
        if not claims:
            return ""
        parts = [
            *claims,
            "authorised health claims, general principles, permitted wording",
        ]

    elif rule_id is RuleId.ORIGIN_DECLARATION:
        origins = [origin.value.country for origin in spec.origins]
        primary = _named_ingredients(spec, limit=1)
        parts = [
            "country of origin or place of provenance",
            "primary ingredient origin indication",
            *origins,
            *primary,
        ]

    elif rule_id is RuleId.LEGAL_NAME_AND_QUID:
        legal_name = spec.legal_name.value if spec.legal_name else ""
        quantified = [
            f"{item.value.name} {item.value.percentage:g}%"
            for item in (spec.ingredients.value.items if spec.ingredients else [])
            if item.value.percentage is not None
        ][:3]
        parts = [
            "name of the food, legal name",
            "quantitative indication of ingredients QUID",
            legal_name,
            *quantified,
        ]

    query = ", ".join(part for part in parts if part).strip()
    return query[:MAX_QUERY_CHARS]
