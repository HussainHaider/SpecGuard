"""The state that flows through the check graph."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from specguard.models.document import IngestedDocument
from specguard.models.report import CheckReport, GuardrailFlags
from specguard.models.rule import RuleId, RuleResult
from specguard.models.spec import ProductSpec


class CheckState(TypedDict, total=False):
    """What each node reads and writes.

    ``results`` is annotated with ``operator.add`` because the check node fans out and
    several branches append concurrently; without it the last branch to finish would
    overwrite the others rather than joining them.
    """

    job_id: str
    correlation_id: str
    started_at: float
    pdf_path: str
    language: str

    document: IngestedDocument
    scrubbed_text: str
    spec: ProductSpec
    selected_rules: list[RuleId]
    skipped_rules: dict[str, str]

    #: Appended to by the fan-out, so it needs a reducer: without one the last branch
    #: to finish would overwrite the others instead of joining them.
    results: Annotated[list[RuleResult], operator.add]

    #: The gated results. A separate key rather than a rewrite of ``results``, because
    #: ``results`` accumulates — writing to it from verify would append the gated copies
    #: to the ungated ones and double every report.
    gated_results: list[RuleResult]

    guardrails: GuardrailFlags
    report: CheckReport
    error: str
