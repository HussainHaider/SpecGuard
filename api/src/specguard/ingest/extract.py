"""Turn an ingested document into a ProductSpec with one schema-constrained call."""

from __future__ import annotations

from pydantic import Field

from specguard.llm.protocol import LLMClient
from specguard.models.common import ExtractedField, Language, Quantity, SpecGuardModel
from specguard.models.document import IngestedDocument
from specguard.models.rule import LlmUsage
from specguard.models.spec import (
    BusinessOperator,
    Claim,
    DurabilityDate,
    EmphasisStyle,
    Ingredient,
    IngredientList,
    NutritionDeclaration,
    OriginDeclaration,
    ProductSpec,
)
from specguard.prompts.loader import load_prompt

PROMPT_NAME = "extract"


class SpecExtraction(SpecGuardModel):
    """What the model is asked to return.

    Deliberately not ProductSpec itself: identity, provenance and the model/prompt
    versions are facts we hold, not things a model should be asked to supply. Narrowing
    the schema to what the document can actually support also removes fields the model
    might otherwise feel obliged to fill.
    """

    product_name: ExtractedField[str] | None = None
    legal_name: ExtractedField[str] | None = None
    ingredients: ExtractedField[IngredientList] | None = None
    allergen_statement: ExtractedField[str] | None = None
    may_contain_statement: ExtractedField[str] | None = None
    net_quantity: ExtractedField[Quantity] | None = None
    drained_net_weight: ExtractedField[Quantity] | None = None
    durability: ExtractedField[DurabilityDate] | None = None
    storage_conditions: ExtractedField[str] | None = None
    instructions_for_use: ExtractedField[str] | None = None
    business_operator: ExtractedField[BusinessOperator] | None = None
    alcohol_strength_abv: ExtractedField[float] | None = None
    nutrition: ExtractedField[NutritionDeclaration] | None = None
    origins: list[ExtractedField[OriginDeclaration]] = Field(default_factory=list)
    claims: list[ExtractedField[Claim]] = Field(default_factory=list)
    unmapped_notes: list[str] = Field(default_factory=list)


def _enrich_emphasis(
    ingredients: ExtractedField[IngredientList], document: IngestedDocument
) -> ExtractedField[IngredientList]:
    """Set each ingredient's emphasis from the PDF's own font data.

    Typography is measured, not asked about. A model reading plain text cannot see font
    weight, and asking it to infer emphasis from capitalisation would make
    ALLERGEN_EMPHASIS — a deterministic rule — depend on a model's guess about the very
    thing the article is about. The spans say what the typeset actually was.
    """
    raw = ingredients.value.raw_text
    emphasised = document.emphasised_words_in(raw)

    def mark(ingredient: Ingredient) -> Ingredient:
        words = {
            "".join(c for c in word if c.isalnum()).upper()
            for word in ingredient.name.replace("-", " ").split()
        }
        hit = bool(words & emphasised)
        return ingredient.model_copy(
            update={
                "emphasised": hit,
                "emphasis_style": EmphasisStyle.UPPERCASE if hit else EmphasisStyle.NONE,
                "sub_ingredients": [mark(child) for child in ingredient.sub_ingredients],
            }
        )

    updated = ingredients.value.model_copy(
        update={
            "items": [
                item.model_copy(update={"value": mark(item.value)})
                for item in ingredients.value.items
            ]
        }
    )
    return ingredients.model_copy(update={"value": updated})


def extract_spec(
    document: IngestedDocument,
    client: LLMClient,
    *,
    language: Language = Language.EN,
) -> tuple[ProductSpec, LlmUsage]:
    """Extract a ProductSpec from a supplier document."""
    prompt = load_prompt(PROMPT_NAME)
    result = client.generate(
        prompt=prompt,
        schema=SpecExtraction,
        document=document.text,
        # Keyed by content hash, so a replayed fixture is tied to the exact document
        # that produced it rather than to a filename someone may rename.
        cache_key=document.source.sha256[:16],
    )

    extraction = result.value
    ingredients = (
        _enrich_emphasis(extraction.ingredients, document)
        if extraction.ingredients is not None
        else None
    )

    spec = ProductSpec(
        source=document.source,
        language=language,
        extractor_model=result.usage.model,
        extractor_prompt_version=result.usage.prompt_version,
        **(extraction.model_dump() | {"ingredients": None}),
    )
    return spec.model_copy(update={"ingredients": ingredients}), result.usage
