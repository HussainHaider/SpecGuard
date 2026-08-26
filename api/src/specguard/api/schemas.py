"""Request and response models for the API boundary."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, Field

from specguard.db.models import JobStatus
from specguard.models.report import CheckReport
from specguard.models.rule import Verdict


class CheckAccepted(BaseModel):
    """Returned by POST /checks. The work has been queued, not done."""

    job_id: uuid.UUID
    status: JobStatus
    correlation_id: str


class CheckStatus(BaseModel):
    """Returned by GET /checks/{id}.

    ``report`` is present only once the job has succeeded. A caller polling this should
    branch on ``status`` rather than on the presence of the report.
    """

    job_id: uuid.UUID
    status: JobStatus
    correlation_id: str
    filename: str
    created_at: dt.datetime
    finished_at: dt.datetime | None = None
    error: str | None = None
    report: CheckReport | None = None


class FeedbackIn(BaseModel):
    """A reviewer disagreeing with a verdict.

    Deliberately requires the corrected verdict rather than a thumbs-down: "this is
    wrong" is not usable as an eval label, while "this should have been PASS" is.
    """

    rule_id: str = Field(min_length=1)
    corrected_verdict: Verdict
    comment: str | None = Field(default=None, max_length=4000)
    reviewer: str | None = Field(default=None, max_length=128)


class FeedbackOut(BaseModel):
    """Stored feedback."""

    id: uuid.UUID
    job_id: uuid.UUID
    rule_id: str
    corrected_verdict: Verdict
    created_at: dt.datetime


class ClauseText(BaseModel):
    """Returned by GET /clauses/{chunk_id}.

    A citation carries the span a verdict relied on but not the clause it came from, so
    on its own it cannot be shown in context. This is what makes "no verdict without a
    resolvable citation" resolvable for a reader rather than only for the system: the
    reviewer sees the article, with the relied-on words inside it.
    """

    chunk_id: str
    regulation: str
    article: str
    paragraph: str | None = None
    heading: str | None = None
    language: str
    source_version: str
    text: str
    reference: str = Field(description="Human-readable locator, e.g. 'Art. 21(1)'.")


class Health(BaseModel):
    """Returned by GET /healthz."""

    status: str
    version: str
    checks: dict[str, bool]
