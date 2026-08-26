"""Build the ProductSpec a perfect extraction would produce from a generated sheet.

This is ground truth, not a shortcut around extraction. It lets the deterministic rules
be exercised against all thirty fixtures with no model in the loop, so a rule failure is
unambiguously the rule's fault rather than the extractor's. Extraction is tested
separately, against its own recorded fixtures.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from specguard.fixtures.generate import _ingredient_text, _Sheet
from specguard.models.common import ExtractedField, NetQuantityUnit, Quantity
from specguard.models.spec import (
    BusinessOperator,
    Claim,
    ClaimKind,
    DurabilityDate,
    DurabilityKind,
    EmphasisStyle,
    Ingredient,
    IngredientList,
    NutrientBasis,
    NutritionDeclaration,
    OriginDeclaration,
    OriginScope,
    ProductSpec,
    SourceDocument,
)

CONFIDENCE = 1.0

_UNITS = {
    "g": NetQuantityUnit.G,
    "kg": NetQuantityUnit.KG,
    "ml": NetQuantityUnit.ML,
    "cl": NetQuantityUnit.CL,
    "l": NetQuantityUnit.L,
}
_USE_BY_WORDS = ("use by", "verbrauchen bis")


def _field[T](value: T) -> ExtractedField[T]:
    """Wrap a known-true value. Confidence is 1.0 because this *is* the ground truth."""
    return ExtractedField(value=value, confidence=CONFIDENCE)


def _is_emphasised(name: str) -> bool:
    """Whether the rendered ingredient name carries capitalised emphasis."""
    return any(len(word) > 1 and word.isupper() for word in name.replace("-", " ").split())


def _quantity(text: str) -> Quantity | None:
    parts = text.strip().split()
    if len(parts) != 2 or parts[1].lower() not in _UNITS:
        return None
    return Quantity(value=float(parts[0]), unit=_UNITS[parts[1].lower()])


def _durability(text: str) -> DurabilityDate:
    lowered = text.lower()
    kind = (
        DurabilityKind.USE_BY
        if any(word in lowered for word in _USE_BY_WORDS)
        else DurabilityKind.BEST_BEFORE
    )
    return DurabilityDate(kind=kind, raw_text=text)


def _nutrition(sheet: _Sheet) -> NutritionDeclaration:
    nutrients = sheet.template.nutrients
    fat = sheet.fat_override if sheet.fat_override is not None else nutrients.fat
    fibre = sheet.fibre_override if sheet.fibre_override is not None else nutrients.fibre
    basis = {
        "per 100 g": NutrientBasis.PER_100G,
        "per 100 ml": NutrientBasis.PER_100ML,
        "per 30 g portion": NutrientBasis.PER_PORTION,
        "per pack": NutrientBasis.PER_PACK,
    }[sheet.basis]
    return NutritionDeclaration(
        basis=_field(basis),
        energy_kj=_field(sheet.energy_kj),
        energy_kcal=_field(sheet.energy_kcal),
        fat_g=_field(fat),
        saturates_g=_field(nutrients.saturates),
        carbohydrate_g=_field(nutrients.carbohydrate),
        sugars_g=_field(nutrients.sugars),
        fibre_g=_field(fibre),
        protein_g=_field(nutrients.protein),
        salt_g=_field(nutrients.salt),
    )


def spec_for_sheet(sheet: _Sheet, pdf_path: Path | None = None) -> ProductSpec:
    """The ProductSpec a flawless extraction of this sheet would produce."""
    template = sheet.template

    if pdf_path is not None and pdf_path.exists():
        payload = pdf_path.read_bytes()
        source = SourceDocument(
            filename=pdf_path.name,
            sha256=hashlib.sha256(payload).hexdigest(),
            page_count=1,
            byte_size=len(payload),
        )
    else:
        source = SourceDocument(
            filename=f"{template.slug}.pdf", sha256="0" * 64, page_count=1, byte_size=1
        )

    ingredients = IngredientList(
        raw_text=_ingredient_text(sheet),
        items=[
            _field(
                Ingredient(
                    name=item.name,
                    percentage=None if sheet.quid_suppressed else item.percentage,
                    emphasised=_is_emphasised(item.name),
                    emphasis_style=(
                        EmphasisStyle.UPPERCASE if _is_emphasised(item.name) else EmphasisStyle.NONE
                    ),
                )
            )
            for item in sheet.ingredients
        ],
        dominant_emphasis_style=EmphasisStyle.UPPERCASE,
    )

    origins = []
    if sheet.origin:
        origins.append(
            _field(
                OriginDeclaration(
                    scope=OriginScope.PRODUCT, country=sheet.origin, raw_text=sheet.origin
                )
            )
        )
    if sheet.primary_ingredient_origin:
        origins.append(
            _field(
                OriginDeclaration(
                    scope=OriginScope.PRIMARY_INGREDIENT,
                    country=sheet.primary_ingredient_origin,
                    raw_text=sheet.primary_ingredient_origin,
                )
            )
        )

    claims = []
    if sheet.nutrition_claim:
        claims.append(_field(Claim(text=sheet.nutrition_claim, kind=ClaimKind.NUTRITION)))
    if sheet.health_claim:
        claims.append(_field(Claim(text=sheet.health_claim, kind=ClaimKind.HEALTH)))

    quantity = _quantity(sheet.net_quantity) if sheet.net_quantity else None
    # Art. 9(1)(h) wants a name *and* an address, so a suppressed address means the
    # particular is absent, not merely partial.
    operator = (
        BusinessOperator(name=template.supplier, address=sheet.supplier_address)
        if sheet.supplier_address
        else None
    )

    return ProductSpec(
        source=source,
        language=template.language,
        extractor_model="ground-truth",
        extractor_prompt_version="fixtures@v1",
        product_name=_field(template.product_name),
        legal_name=_field(sheet.legal_name),
        ingredients=_field(ingredients),
        net_quantity=_field(quantity) if quantity else None,
        durability=_field(_durability(sheet.durability)) if sheet.durability else None,
        storage_conditions=_field(template.storage),
        instructions_for_use=_field(template.instructions) if template.instructions else None,
        business_operator=_field(operator) if operator else None,
        nutrition=_field(_nutrition(sheet)),
        origins=origins,
        claims=claims,
    )
