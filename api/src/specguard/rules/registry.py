"""Rule registry: the one place that knows which rules exist.

Deterministic and RAG rules are registered separately, and the separation is load-
bearing rather than cosmetic. The graph runs the deterministic set in a single
in-process node with no client of any kind in scope, so there is nothing for a model
call to be made *with* — non-negotiable #2 is enforced by what the node is handed, not
by a comment asking future maintainers to be careful.
"""

from __future__ import annotations

from specguard.models.rule import RULE_KINDS, RuleId, RuleKind
from specguard.rules.base import Rule
from specguard.rules.deterministic.allergen_emphasis import AllergenEmphasisRule
from specguard.rules.deterministic.mandatory_fields import MandatoryFieldsRule
from specguard.rules.deterministic.nutrition_arithmetic import NutritionArithmeticRule
from specguard.rules.deterministic.nutrition_per_100 import NutritionPer100Rule
from specguard.rules.rag.base import RagRule
from specguard.rules.rag.rules import (
    HealthClaimAuthorisedRule,
    LegalNameAndQuidRule,
    NutritionClaimConditionsRule,
    OriginDeclarationRule,
)

DETERMINISTIC_RULES: tuple[Rule, ...] = (
    MandatoryFieldsRule(),
    NutritionArithmeticRule(),
    NutritionPer100Rule(),
    AllergenEmphasisRule(),
)


RAG_RULES: tuple[RagRule, ...] = (
    NutritionClaimConditionsRule(),
    HealthClaimAuthorisedRule(),
    OriginDeclarationRule(),
    LegalNameAndQuidRule(),
)


def rag_rules() -> dict[RuleId, RagRule]:
    """The retrieval-backed rules, by id."""
    return {rule.rule_id: rule for rule in RAG_RULES}


def deterministic_rules() -> dict[RuleId, Rule]:
    """The Python-only rules, by id."""
    return {rule.rule_id: rule for rule in DETERMINISTIC_RULES}


def registered_ids() -> set[RuleId]:
    """Which rule ids currently have an implementation."""
    return set(deterministic_rules()) | set(rag_rules())


def missing_ids() -> set[RuleId]:
    """Rule ids declared in the model but with no implementation. Empty once M3 lands."""
    return {rule_id for rule_id in RuleId if rule_id not in registered_ids()}


def _self_check() -> None:
    """Each rule must be registered under the kind the model declares for it."""
    for rule_id in deterministic_rules():
        if RULE_KINDS[rule_id] is not RuleKind.DETERMINISTIC:
            raise ValueError(f"{rule_id} is registered as deterministic but declared RAG")
    for rule_id in rag_rules():
        if RULE_KINDS[rule_id] is not RuleKind.RAG:
            raise ValueError(f"{rule_id} is registered as RAG but declared deterministic")


_self_check()
