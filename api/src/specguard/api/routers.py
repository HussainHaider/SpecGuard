"""The five endpoints."""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
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
    FeedbackIn,
    FeedbackOut,
    Health,
)
from specguard.config import get_settings
from specguard.db.models import Feedback, Job, JobStatus
from specguard.guardrails.upload import UploadRejectedError, check_upload
from specguard.logging import bind_correlation_id, get_logger
from specguard.models.report import CheckReport
from specguard.models.rule import RuleId

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
        await queue.enqueue_job("run_check", str(job.id), _job_id=str(job.id))
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

    entry = Feedback(
        job_id=job.id,
        rule_id=body.rule_id,
        corrected_verdict=body.corrected_verdict.value,
        comment=body.comment,
        reviewer=body.reviewer,
    )
    session.add(entry)
    await session.flush()
    log.info(
        "feedback.recorded",
        job_id=str(job.id),
        rule_id=body.rule_id,
        corrected=body.corrected_verdict.value,
    )
    return FeedbackOut(
        id=entry.id,
        job_id=job.id,
        rule_id=entry.rule_id,
        corrected_verdict=body.corrected_verdict,
        created_at=entry.created_at or dt.datetime.now(dt.UTC),
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
