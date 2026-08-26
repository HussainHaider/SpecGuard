"""The Annex II list of substances causing allergies or intolerances.

Kept as data rather than retrieved, because the list is closed, short, and part of the
regulation's text rather than something requiring interpretation. ALLERGEN_EMPHASIS
still cites Annex II — it just does not need a model to read it.
"""

from __future__ import annotations

import re
from functools import lru_cache

from specguard.models.common import Language

#: Annex II, entries 1-14. Each entry maps to the surface forms that appear in an
#: ingredient list, in the languages the corpus is indexed in.
ANNEX_II: dict[str, dict[Language, tuple[str, ...]]] = {
    "cereals containing gluten": {
        Language.EN: ("wheat", "rye", "barley", "oats", "spelt", "khorasan", "gluten"),
        Language.DE: ("weizen", "roggen", "gerste", "hafer", "dinkel", "gluten"),
    },
    "crustaceans": {
        Language.EN: ("crustacean", "prawn", "shrimp", "crab", "lobster"),
        Language.DE: ("krebstier", "garnele", "krabbe", "hummer"),
    },
    "eggs": {Language.EN: ("egg",), Language.DE: ("ei", "eier")},
    "fish": {
        Language.EN: ("fish", "salmon", "cod", "tuna", "anchovy"),
        Language.DE: ("fisch", "lachs", "kabeljau", "thunfisch", "sardelle"),
    },
    "peanuts": {Language.EN: ("peanut",), Language.DE: ("erdnuss", "erdnuesse")},
    "soybeans": {Language.EN: ("soya", "soy", "soybean"), Language.DE: ("soja",)},
    "milk": {
        Language.EN: ("milk", "cream", "butter", "cheese", "yogurt", "whey", "lactose"),
        Language.DE: ("milch", "sahne", "butter", "kaese", "joghurt", "molke", "laktose"),
    },
    "nuts": {
        Language.EN: ("almond", "hazelnut", "walnut", "cashew", "pecan", "pistachio", "macadamia"),
        Language.DE: ("mandel", "haselnuss", "walnuss", "cashew", "pekan", "pistazie", "macadamia"),
    },
    "celery": {Language.EN: ("celery", "celeriac"), Language.DE: ("sellerie",)},
    "mustard": {Language.EN: ("mustard",), Language.DE: ("senf",)},
    "sesame seeds": {Language.EN: ("sesame",), Language.DE: ("sesam",)},
    "sulphur dioxide and sulphites": {
        Language.EN: ("sulphite", "sulfite", "sulphur dioxide"),
        Language.DE: ("sulfit", "schwefeldioxid"),
    },
    "lupin": {Language.EN: ("lupin",), Language.DE: ("lupine",)},
    "molluscs": {
        Language.EN: ("mollusc", "mussel", "oyster", "squid", "clam"),
        Language.DE: ("weichtier", "muschel", "auster", "tintenfisch"),
    },
}


#: Compounds that contain an allergen word without being that allergen. Cocoa butter is
#: not dairy; peanut butter is a nut, not milk. Stripped before matching.
_FALSE_FRIENDS: tuple[str, ...] = (
    "cocoa butter",
    "shea butter",
    "peanut butter",
    "kakaobutter",
    "sheabutter",
    "erdnussbutter",
    "buttermilk substitute",
)

#: Terms this short are only credible as whole words. Plain substring matching on "ei"
#: (German for egg) flags Speisesalz, Basilikum and half the German language.
_SHORT_TERM = 3


def _pattern(term: str) -> re.Pattern[str]:
    """Word-start matching, or whole-word matching for very short terms.

    Start-of-word rather than exact word is deliberate: German compounds its nouns, so
    "Weizenvollkornmehl" declares wheat and an exact-word match would miss it. Matching
    anywhere in the word is what produced the Speisesalz false positive.
    """
    suffix = "" if len(term) > _SHORT_TERM else r"\b"
    return re.compile(r"\b" + re.escape(term) + suffix)


@lru_cache(maxsize=256)
def _patterns(language: Language) -> tuple[tuple[str, tuple[re.Pattern[str], ...]], ...]:
    compiled = []
    for entry, by_language in ANNEX_II.items():
        terms = by_language.get(language, by_language[Language.EN])
        compiled.append((entry, tuple(_pattern(term) for term in terms)))
    return tuple(compiled)


def allergens_in(text: str, language: Language) -> set[str]:
    """Which Annex II entries appear in a piece of ingredient text."""
    lowered = text.casefold().replace("-", " ")
    for compound in _FALSE_FRIENDS:
        lowered = lowered.replace(compound, " ")
    return {
        entry
        for entry, patterns in _patterns(language)
        if any(pattern.search(lowered) for pattern in patterns)
    }
