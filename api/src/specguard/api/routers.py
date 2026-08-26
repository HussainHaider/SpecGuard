"""The five endpoints."""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from functools import partial
from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from specguard.api.deps import get_session
from specguard.api.metrics import GUARDRAIL_TRIPS, REGISTRY
from specguard.api.schemas import (
    CheckAccepted,
    CheckStatus,
    ClauseText,
    FeedbackIn,
    FeedbackOut,
    Health,
)
from specguard.config import get_settings
from specguard.db.models import Feedback, Job, JobStatus, Result
from specguard.guardrails.upload import UploadRejectedError, check_upload
from specguard.logging import bind_correlation_id, get_logger
from specguard.models.report import CheckReport
from specguard.models.rule import RuleId
from specguard.queue import WORKER_FUNCTION
from specguard.tracing import record_feedback

log = get_logger(__name__)
router = APIRouter()

UPLOAD_DIR = Path("/tmp/specguard-uploads")  # noqa: S108 - container-local scratch space


def _persist_upload(job_id: uuid.UUID, payload: bytes) -> None:
    """Write the upload where the worker will find it."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / f"{job_id}.pdf").write_bytes(payload)


@router.post("/checks", response_model=CheckAccepted, status_code=status.HTTP_202_ACCEPTED)
async def submit_check(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File(description="A PDF specification sheet.")],
) -> CheckAccepted:
    """Accept a specification sheet and queue it for checking.

    Returns immediately with a job id. Checking involves several model calls and takes
    tens of seconds, which is far too long to hold a request open.
    """
    correlation_id = bind_correlation_id(request.headers.get("x-correlation-id"))
    payload = await file.read()
    filename = file.filename or "upload.pdf"

    try:
        # Rejected before anything is written to disk or queued: the cheapest possible
        # place to say no.
        check_upload(payload, filename)
    except UploadRejectedError as error:
        GUARDRAIL_TRIPS.labels(guardrail="upload").inc()
        log.warning("upload.rejected", filename=filename, reason=str(error))
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error

    job = Job(
        correlation_id=correlation_id,
        status=JobStatus.QUEUED,
        filename=filename,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        language=request.query_params.get("language", "en"),
    )
    session.add(job)
    await session.flush()

    # Off the event loop: a 10 MB write is long enough to stall every other request
    # on this worker while it happens.
    await anyio.to_thread.run_sync(_persist_upload, job.id, payload)

    queue = request.app.state.queue
    if queue is not None:
        # Must match the name arq registers, which is the function's own __name__.
        # WorkerSettings.functions is the one place that mapping is defined.
        await queue.enqueue_job(WORKER_FUNCTION, str(job.id), _job_id=str(job.id))
    log.info("check.queued", job_id=str(job.id), filename=filename, bytes=len(payload))

    return CheckAccepted(job_id=job.id, status=job.status, correlation_id=correlation_id)


@router.get("/checks/{job_id}", response_model=CheckStatus)
async def get_check(
    job_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]
) -> CheckStatus:
    """The status of one check, and its report once it has finished."""
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No check with id {job_id}.")

    bind_correlation_id(job.correlation_id)
    return CheckStatus(
        job_id=job.id,
        status=job.status,
        correlation_id=job.correlation_id,
        filename=job.filename,
        created_at=job.created_at,
        finished_at=job.finished_at,
        error=job.error,
        report=CheckReport.model_validate(job.report) if job.report else None,
    )


@router.post(
    "/checks/{job_id}/feedback",
    response_model=FeedbackOut,
    status_code=status.HTTP_201_CREATED,
)
async def submit_feedback(
    job_id: uuid.UUID,
    body: FeedbackIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FeedbackOut:
    """Record a reviewer's correction of a verdict.

    A stored disagreement is the most informative record this system holds: it is a
    labelled example of the tool being wrong, which is exactly what an eval set needs.
    """
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No check with id {job_id}.")

    if body.rule_id not in {rule.value for rule in RuleId}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown rule {body.rule_id!r}.")

    # The run that produced the verdict being corrected. Without it the correction is
    # still a usable eval label, it just is not attached to anything a person can open.
    stored = (
        await session.execute(
            select(Result).where(Result.job_id == job.id, Result.rule_id == body.rule_id)
        )
    ).scalar_one_or_none()

    entry = Feedback(
        job_id=job.id,
        rule_id=body.rule_id,
        corrected_verdict=body.corrected_verdict.value,
        comment=body.comment,
        reviewer=body.reviewer,
        langsmith_run_id=stored.langsmith_run_id if stored else None,
    )
    session.add(entry)
    await session.flush()

    if entry.langsmith_run_id:
        # Off the event loop: this is an outbound HTTP call to a third party, and the
        # correction is already committed whether or not it lands.
        entry.langsmith_feedback_id = await anyio.to_thread.run_sync(
            partial(
                record_feedback,
                entry.langsmith_run_id,
                corrected_verdict=body.corrected_verdict.value,
                original_verdict=stored.verdict if stored else None,
                comment=body.comment,
                reviewer=body.reviewer,
            )
        )

    log.info(
        "feedback.recorded",
        job_id=str(job.id),
        rule_id=body.rule_id,
        corrected=body.corrected_verdict.value,
        run_id=entry.langsmith_run_id,
        attached=bool(entry.langsmith_feedback_id),
    )
    return FeedbackOut(
        id=entry.id,
        job_id=job.id,
        rule_id=entry.rule_id,
        corrected_verdict=body.corrected_verdict,
        created_at=entry.created_at or dt.datetime.now(dt.UTC),
    )


@router.get("/clauses/{chunk_id}", response_model=ClauseText)
async def get_clause(chunk_id: str, request: Request) -> ClauseText:
    """The full text of a cited clause, so a verdict can be read in context.

    Served from the corpus already loaded in this process rather than from the vector
    store. Retrieval by id is not a retrieval concern, and adding a fetch method to
    VectorStore would widen an abstraction that exists for one comparison and is
    deliberately kept to three methods.
    """
    clauses = getattr(request.app.state, "clauses", None)
    if not clauses:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The regulation corpus is not loaded; run `python -m specguard.corpus.fetch`.",
        )

    clause = clauses.get(chunk_id)
    if clause is None:
        # A citation that does not resolve is exactly the failure non-negotiable #1
        # exists to prevent, so it is a 404 rather than an empty body.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No indexed clause with chunk_id {chunk_id}."
        )

    return ClauseText(
        chunk_id=clause.chunk_id,
        regulation=clause.regulation,
        article=clause.article,
        paragraph=clause.paragraph,
        heading=clause.heading,
        language=clause.language.value,
        source_version=clause.source_version,
        text=clause.text,
        reference=clause.to_citation("x").reference,
    )


@router.get("/healthz", response_model=Health)
async def healthz(session: Annotated[AsyncSession, Depends(get_session)]) -> Health:
    """Liveness and dependency check.

    Reports which dependencies answered rather than a bare "ok": a health endpoint that
    says nothing useful when half the system is down is worse than none.
    """
    checks: dict[str, bool] = {}
    try:
        await session.execute(select(1))
        checks["postgres"] = True
    except Exception:
        checks["postgres"] = False

    healthy = all(checks.values())
    return Health(
        status="ok" if healthy else "degraded",
        version=get_settings().graph_version,
        checks=checks,
    )


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus exposition."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
