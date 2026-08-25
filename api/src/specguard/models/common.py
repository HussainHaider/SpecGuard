"""Shared model primitives: the base config and the per-field extraction envelope."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SpecGuardModel(BaseModel):
    """Base for every SpecGuard record.

    Frozen because these models are an audit trail: a ProductSpec or a RuleResult
    describes what was observed at a point in time and must not be edited in place.
    ``extra="forbid"`` matters most for the models used as LLM output schemas —
    a model that invents a field fails validation rather than smuggling it through.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_round_tripped_computed_fields(cls, data: Any) -> Any:
        """Let a serialised record validate back into its model.

        ``extra="forbid"`` and ``@computed_field`` collide on the return trip: dumping
        a CheckReport emits ``overall_verdict`` and ``counts``, and re-validating that
        JSON — reading a stored report out of Postgres, or a client POSTing one back —
        would then fail on its own output. Computed keys are dropped and recomputed;
        genuinely unknown keys are still rejected.
        """
        if cls.model_computed_fields and isinstance(data, dict):
            return {k: v for k, v in data.items() if k not in cls.model_computed_fields}
        return data


class ExtractedField[T](SpecGuardModel):
    """A value pulled out of a supplier PDF, carrying its own confidence and provenance.

    Every extracted datum is wrapped in this envelope so that a caller cannot read a
    value without also being able to see how sure the extractor was and where it came
    from. ``quoted_span`` is the verbatim source text and is what the UI highlights.
    """

    value: T
    confidence: float = Field(ge=0.0, le=1.0)
    page: int | None = Field(default=None, ge=1)
    quoted_span: str | None = None


class NetQuantityUnit(StrEnum):
    """Units permitted for a net quantity declaration."""

    G = "g"
    KG = "kg"
    ML = "ml"
    CL = "cl"
    L = "l"
    PIECES = "pieces"


class Quantity(SpecGuardModel):
    """A magnitude with its unit, e.g. net quantity or drained net weight."""

    value: float = Field(ge=0.0)
    unit: NetQuantityUnit


class Language(StrEnum):
    """Languages the corpus and the specs are authored in."""

    EN = "en"
    DE = "de"
    FR = "fr"
    ES = "es"
    NL = "nl"
