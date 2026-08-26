"""Decide which rules apply to a given specification.

Running every rule on every document wastes model calls on questions the document cannot
answer, and worse, it fills the report with abstentions that look like uncertainty when
they are really just irrelevance. A yogurt that makes no health claim should not produce
a health-claim finding of any kind.

The skip reasons are kept and reported. "Not checked because the product makes no health
claim" is information a reviewer wants; a rule silently missing from a report is not.
"""

from __future__ import annotations

from specguard.models.rule import RuleId
from specguard.models.spec import ClaimKind, ProductSpec
from specguard.rules.registry import registered_ids


def _has_claim(spec: ProductSpec, kind: ClaimKind) -> bool:
    return any(claim.value.kind is kind for claim in spec.claims)


def plan_rules(spec: ProductSpec) -> tuple[list[RuleId], dict[str, str]]:
    """Return the rules worth running, and why the others were skipped.

    Only claim rules are ever skipped. The rest are unconditional: a missing nutrition
    declaration is exactly what MANDATORY_FIELDS is for, so a spec without one still
    needs checking rather than excusing.
    """
    skipped: dict[str, str] = {}
    selected: list[RuleId] = []

    for rule_id in sorted(registered_ids(), key=lambda r: r.value):
        if rule_id is RuleId.NUTRITION_CLAIM_CONDITIONS and not _has_claim(
            spec, ClaimKind.NUTRITION
        ):
            skipped[rule_id.value] = "the specification makes no nutrition claim"
            continue
        if rule_id is RuleId.HEALTH_CLAIM_AUTHORISED and not _has_claim(spec, ClaimKind.HEALTH):
            skipped[rule_id.value] = "the specification makes no health claim"
            continue
        selected.append(rule_id)

    return selected, skipped
