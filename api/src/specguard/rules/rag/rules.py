"""The four retrieval-backed rules.

Each is only an identity and a prompt. Everything that decides whether a verdict is
allowed to stand lives in RagRule, so no rule can implement a weaker version of the
verification contract than the others.
"""

from __future__ import annotations

from specguard.models.rule import RuleId
from specguard.rules.rag.base import RagRule


class NutritionClaimConditionsRule(RagRule):
    """Do the nutrition claims meet the conditions of use in the 1924/2006 Annex?"""

    rule_id = RuleId.NUTRITION_CLAIM_CONDITIONS
    judge_prompt = "judge_nutrition_claim_conditions"
    governing_regulation = "Regulation (EC) No 1924/2006"
    governing_article = "8"
    governing_paragraph = "1"
    governing_quote = (
        "Nutrition claims shall only be permitted if they are listed in the Annex and are "
        "in conformity with the conditions set out in this Regulation."
    )


class HealthClaimAuthorisedRule(RagRule):
    """Are the health claims authorised, and worded within that authorisation?"""

    rule_id = RuleId.HEALTH_CLAIM_AUTHORISED
    judge_prompt = "judge_health_claim_authorised"
    governing_regulation = "Regulation (EC) No 1924/2006"
    governing_article = "10"
    governing_paragraph = "1"
    governing_quote = (
        "Health claims shall be prohibited unless they comply with the general "
        "requirements in Chapter II"
    )


class OriginDeclarationRule(RagRule):
    """Is origin declared where Art. 26 requires it, primary ingredient included?"""

    rule_id = RuleId.ORIGIN_DECLARATION
    judge_prompt = "judge_origin_declaration"
    governing_regulation = "Regulation (EU) No 1169/2011"
    governing_article = "26"
    governing_paragraph = "2"
    governing_quote = (
        "Indication of the country of origin or place of provenance shall be mandatory"
    )


class LegalNameAndQuidRule(RagRule):
    """Is the legal name correct, and is QUID given where Art. 22 requires it?"""

    rule_id = RuleId.LEGAL_NAME_AND_QUID
    judge_prompt = "judge_legal_name_and_quid"
    governing_regulation = "Regulation (EU) No 1169/2011"
    governing_article = "22"
    governing_paragraph = "1"
    governing_quote = (
        "The indication of the quantity of an ingredient or category of ingredients used "
        "in the manufacture or preparation of a food shall be required"
    )
