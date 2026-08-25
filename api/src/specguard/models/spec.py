"""ProductSpec: the structured record extracted from a supplier spec sheet."""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum

from pydantic import Field

from specguard.models.common import ExtractedField, Language, Quantity, SpecGuardModel


class SourceDocument(SpecGuardModel):
    """Provenance of the PDF a spec was extracted from.

    ``sha256`` is what makes a report reproducible: the same bytes, corpus version and
    prompt version must yield the same report.
    """

    filename: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)
    byte_size: int = Field(ge=1)


class EmphasisStyle(StrEnum):
    """How allergen emphasis is carried in the ingredient text.

    Art. 21(1)(b) requires emphasis "by a typeset that clearly distinguishes it";
    the PDF may achieve that with bold, capitals or underline, so the extractor
    records which convention this document uses and ALLERGEN_EMPHASIS checks against it.
    """

    BOLD = "bold"
    UPPERCASE = "uppercase"
    UNDERLINE = "underline"
    ITALIC = "italic"
    NONE = "none"


class Ingredient(SpecGuardModel):
    """One entry in the ingredient list.

    ``percentage`` is the QUID figure where the supplier declared one. ``emphasised``
    is a fact about the source markup, not a compliance judgement — deciding whether
    that emphasis was *required* is ALLERGEN_EMPHASIS's job.
    """

    name: str = Field(min_length=1)
    percentage: float | None = Field(default=None, ge=0.0, le=100.0)
    emphasised: bool = False
    emphasis_style: EmphasisStyle = EmphasisStyle.NONE
    sub_ingredients: list[Ingredient] = Field(
        default_factory=list,
        description="Components of a compound ingredient, which carry allergens of their own.",
    )


class IngredientList(SpecGuardModel):
    """The ingredient list, kept both parsed and raw.

    The raw text is retained with its markup because emphasis is only observable
    there; the parsed items are what QUID and ordering checks run against. Losing
    either representation would make one of the two rules unimplementable.
    """

    raw_text: str = Field(
        min_length=1,
        description="Verbatim ingredient declaration with emphasis markup preserved.",
    )
    items: list[ExtractedField[Ingredient]] = Field(default_factory=list)
    dominant_emphasis_style: EmphasisStyle = EmphasisStyle.NONE


class NutrientBasis(StrEnum):
    """Basis a nutrition declaration is expressed on (Art. 32)."""

    PER_100G = "per_100g"
    PER_100ML = "per_100ml"
    PER_PORTION = "per_portion"
    PER_PACK = "per_pack"


class MicroNutrient(SpecGuardModel):
    """A vitamin or mineral entry, with its NRV percentage where declared."""

    name: str = Field(min_length=1)
    amount: float = Field(ge=0.0)
    unit: str = Field(min_length=1, description='Declared unit, e.g. "mg", "µg".')
    nrv_percent: float | None = Field(default=None, ge=0.0)


class NutritionDeclaration(SpecGuardModel):
    """The nutrition table.

    Macronutrient amounts are normalised to **grams** at ingestion so that
    NUTRITION_ARITHMETIC can apply the Annex XIV conversion factors without
    re-deriving units. Energy is kept in both kJ and kcal because the regulation
    requires both and the arithmetic check tests them independently.
    """

    basis: ExtractedField[NutrientBasis]
    portion_size: ExtractedField[Quantity] | None = None
    portions_per_pack: ExtractedField[float] | None = None

    energy_kj: ExtractedField[float] | None = None
    energy_kcal: ExtractedField[float] | None = None
    fat_g: ExtractedField[float] | None = None
    saturates_g: ExtractedField[float] | None = None
    mono_unsaturates_g: ExtractedField[float] | None = None
    polyunsaturates_g: ExtractedField[float] | None = None
    carbohydrate_g: ExtractedField[float] | None = None
    sugars_g: ExtractedField[float] | None = None
    polyols_g: ExtractedField[float] | None = None
    starch_g: ExtractedField[float] | None = None
    fibre_g: ExtractedField[float] | None = None
    protein_g: ExtractedField[float] | None = None
    salt_g: ExtractedField[float] | None = None
    alcohol_g: ExtractedField[float] | None = None
    organic_acids_g: ExtractedField[float] | None = None
    erythritol_g: ExtractedField[float] | None = None

    micronutrients: list[ExtractedField[MicroNutrient]] = Field(default_factory=list)


class ClaimKind(StrEnum):
    """Which regime a marketing claim falls under (Reg. 1924/2006)."""

    NUTRITION = "nutrition"
    HEALTH = "health"
    OTHER = "other"


class Claim(SpecGuardModel):
    """A claim made on pack, e.g. "source of fibre" or "supports normal immune function"."""

    text: str = Field(min_length=1)
    kind: ClaimKind
    nutrient: str | None = Field(
        default=None,
        description='Nutrient or substance the claim is about, e.g. "fibre". Used to build '
        "the retrieval query for the conditions of use.",
    )


class DurabilityKind(StrEnum):
    """Which durability form the label uses (Art. 24)."""

    BEST_BEFORE = "best_before"
    BEST_BEFORE_END = "best_before_end"
    USE_BY = "use_by"


class DurabilityDate(SpecGuardModel):
    """Date of minimum durability or use-by date, kept raw and parsed."""

    kind: DurabilityKind
    raw_text: str = Field(min_length=1)
    date: dt.date | None = None


class OriginScope(StrEnum):
    """What an origin statement refers to (Art. 26)."""

    PRODUCT = "product"
    PRIMARY_INGREDIENT = "primary_ingredient"


class OriginDeclaration(SpecGuardModel):
    """A country-of-origin or place-of-provenance statement."""

    scope: OriginScope
    country: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)


class BusinessOperator(SpecGuardModel):
    """The food business operator under whose name the food is marketed (Art. 8(1))."""

    name: str = Field(min_length=1)
    address: str = Field(min_length=1)
    country: str | None = None


class ProductSpec(SpecGuardModel):
    """The structured record extracted from one supplier spec sheet.

    Every field a rule reads is an ``ExtractedField`` so that a missing value and a
    low-confidence value are distinguishable: ``None`` means the extractor found
    nothing, while a present field with low confidence is a NEEDS_REVIEW signal
    rather than a FAIL. Rules must never treat a low-confidence read as fact.
    """

    spec_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source: SourceDocument
    language: Language = Language.EN
    extracted_at: dt.datetime = Field(
        default_factory=lambda: dt.datetime.now(dt.UTC),
    )
    extractor_model: str = Field(
        min_length=1, description="Model id that produced this extraction, for the audit trail."
    )
    extractor_prompt_version: str = Field(
        min_length=1, description="Version string from the extraction prompt's frontmatter."
    )

    # --- Art. 9 mandatory particulars ---------------------------------------
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

    # --- Art. 26 origin, Reg. 1924/2006 claims -------------------------------
    origins: list[ExtractedField[OriginDeclaration]] = Field(default_factory=list)
    claims: list[ExtractedField[Claim]] = Field(default_factory=list)

    # --- Anything the extractor read but could not map -----------------------
    unmapped_notes: list[str] = Field(
        default_factory=list,
        description="Text the extractor judged relevant but could not place. Never fed back "
        "into a prompt as instruction; surfaced to the reviewer as data.",
    )
