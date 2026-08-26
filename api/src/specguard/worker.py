"""The arq worker: runs the check graph for a queued job.

Executed out of process because a check takes tens of seconds and several model calls.
Everything it needs — the store, the client, the corpus chunk ids — is built once at
worker startup rather than per job: loading 734 clauses and an ONNX model on every
document would dominate the runtime.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from arq.connections import RedisSettings
from qdrant_client import QdrantClient

from specguard.api.deps import get_sessionmaker
from specguard.api.metrics import (
    CHECK_DURATION,
    CHECKS_TOTAL,
    GUARDRAIL_TRIPS,
    LLM_COST_USD,
    VERDICTS_TOTAL,
)
from specguard.api.routers import UPLOAD_DIR
from specguard.config import get_settings
from specguard.corpus.seed import load_clauses
from specguard.db.models import Job, JobStatus, Result
from specguard.embedding.encoder import Encoder
from specguard.graph.graph import run_check
from specguard.graph.nodes import Dependencies
from specguard.guardrails.verdicts import needs_human_review
from specguard.llm.factory import build_client
from specguard.logging import bind_correlation_id, configure_logging, get_logger
from specguard.models.report import CheckReport
from specguard.vectorstore.qdrant import QdrantVectorStore

log = get_logger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    """Build the expensive dependencies once for the life of the worker."""
    settings = get_settings()
    configure_logging(settings.log_level)

    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=60)
    store = QdrantVectorStore(
        client,
        Encoder(settings.dense_embedding_model, settings.sparse_embedding_model),
        collection=settings.qdrant_collection,
    )
    ctx["deps"] = Dependencies(
        settings=settings,
        client=build_client(settings),
        store=store,
        corpus_chunk_ids={clause.chunk_id for clause in load_clauses(settings.corpus_dir)},
    )
    log.info("worker.ready", corpus_clauses=len(ctx["deps"].corpus_chunk_ids))


def _persist(job: Job, report: CheckReport) -> list[Result]:
    """Flatten the report into queryable rows, keeping the report itself alongside."""
    rows: list[Result] = []
    for result in report.results:
        VERDICTS_TOTAL.labels(rule_id=result.rule_id.value, verdict=result.verdict.value).inc()
        for usage in result.llm_usage:
            LLM_COST_USD.labels(provider=usage.provider, model=usage.model).inc(usage.cost_usd)
        rows.append(
            Result(
                job_id=job.id,
                rule_id=result.rule_id.value,
                verdict=result.verdict.value,
                confidence=result.confidence,
                rationale=result.rationale,
                suggested_fix=result.suggested_fix,
                abstention_reason=(
                    result.abstention_reason.value if result.abstention_reason else None
                ),
                requires_human_review=needs_human_review(result),
                citations={"items": [c.model_dump(mode="json") for c in result.citations]},
                metrics=dict(result.metrics),
                cost_usd=sum(u.cost_usd for u in result.llm_usage),
                duration_ms=result.duration_ms,
            )
        )
    return rows


async def run_check_job(ctx: dict[str, Any], job_id: str) -> str:
    """Run one check end to end and record the outcome."""
    deps: Dependencies = ctx["deps"]
    sessionmaker = get_sessionmaker()

    async with sessionmaker() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        if job is None:
            log.error("job.missing", job_id=job_id)
            return "missing"

        bind_correlation_id(job.correlation_id)
        job.status = JobStatus.RUNNING
        job.started_at = dt.datetime.now(dt.UTC)
        await session.commit()

    pdf_path = UPLOAD_DIR / f"{job_id}.pdf"
    try:
        with CHECK_DURATION.time():
            state = run_check(
                deps,
                {
                    "job_id": job_id,
                    "correlation_id": bind_correlation_id(None),
                    "pdf_path": str(pdf_path),
                    "language": job.language,
                },
            )
        report: CheckReport = state["report"]
    except Exception as error:
        log.exception("check.failed", job_id=job_id, error=str(error))
        async with sessionmaker() as session:
            failed = await session.get(Job, uuid.UUID(job_id))
            if failed is not None:
                failed.status = JobStatus.FAILED
                failed.error = f"{type(error).__name__}: {error}"
                failed.finished_at = dt.datetime.now(dt.UTC)
                await session.commit()
        CHECKS_TOTAL.labels(status="failed").inc()
        return "failed"

    if report.guardrails.injection_suspected:
        GUARDRAIL_TRIPS.labels(guardrail="injection").inc()

    async with sessionmaker() as session:
        done = await session.get(Job, uuid.UUID(job_id))
        if done is not None:
            done.status = JobStatus.SUCCEEDED
            done.finished_at = dt.datetime.now(dt.UTC)
            done.report = report.model_dump(mode="json")
            done.graph_version = report.graph_version
            done.corpus_version = report.corpus_version
            session.add_all(_persist(done, report))
            await session.commit()

    CHECKS_TOTAL.labels(status="succeeded").inc()
    log.info(
        "check.succeeded",
        job_id=job_id,
        verdict=report.overall_verdict.value,
        cost_usd=report.total_cost_usd,
    )
    # The upload is scratch: the report and its hash are the durable record, and keeping
    # supplier documents around after the check is a liability rather than an asset.
    pdf_path.unlink(missing_ok=True)
    return report.overall_verdict.value


class WorkerSettings:
    """arq entry point: ``arq specguard.worker.WorkerSettings``."""

    functions = [run_check_job]  # noqa: RUF012 - arq reads this as a plain list
    on_startup = startup
    max_jobs = 4
    job_timeout = 600

    @staticmethod
    def redis_settings() -> RedisSettings:
        return RedisSettings.from_dsn(get_settings().redis_url)
