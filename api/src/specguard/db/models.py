"""SQLAlchemy models: jobs, results, feedback, and the audit trail.

Postgres holds what happened; Qdrant holds the corpus. No vectors here — a citation is
stored as its ``chunk_id``, which resolves against a re-indexed Qdrant precisely because
the id is derived from the clause rather than assigned at insert time.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base. JSON payloads use JSONB on Postgres and JSON elsewhere."""

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSON().with_variant(JSONB(), "postgresql")
    }


class JobStatus(enum.StrEnum):
    """Lifecycle of one check."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Job(Base):
    """One submitted document and the run over it."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: Threaded through every log line and every span for this job, so a report can be
    #: traced back to the exact request that produced it.
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", native_enum=False), default=JobStatus.QUEUED
    )

    filename: Mapped[str] = mapped_column(String(255))
    #: Content hash, so the same document submitted twice is recognisable as the same
    #: document regardless of what it was named.
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(8), default="en")

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The whole CheckReport as returned to the caller. Kept alongside the normalised
    #: rows because the report is the artefact a person actually read, and rebuilding it
    #: from parts years later would not reproduce what they saw.
    report: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    graph_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    corpus_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    results: Mapped[list[Result]] = relationship(back_populates="job", cascade="all, delete-orphan")
    feedback: Mapped[list[Feedback]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)


class Result(Base):
    """One rule's verdict, normalised so verdicts can be queried without parsing JSON."""

    __tablename__ = "results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)

    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    verdict: Mapped[str] = mapped_column(String(16), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(Text)
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    abstention_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Set when a gate routed this to a person — an allergen failure, for instance.
    requires_human_review: Mapped[bool] = mapped_column(default=False)

    citations: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    #: The LangSmith run that produced this verdict. Stored so a reviewer's correction
    #: can be attached to the trace it disagrees with, months after the run.
    langsmith_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    job: Mapped[Job] = relationship(back_populates="results")


class Feedback(Base):
    """A reviewer's correction of a verdict.

    The point of collecting this is that a compliance tool is only as good as its
    disagreements: a stored FAIL that a human overturned is the most informative record
    in the system, and the eval set in M5 should be fed by it.
    """

    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[str] = mapped_column(String(64), index=True)

    #: What the reviewer says the verdict should have been.
    corrected_verdict: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    #: The run this correction was attached to, and the id LangSmith gave the feedback.
    #: A null feedback id against a non-null run id means the push did not land — worth
    #: being able to see, rather than pretending every correction reached the trace.
    langsmith_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    langsmith_feedback_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    job: Mapped[Job] = relationship(back_populates="feedback")
