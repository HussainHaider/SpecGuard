"""The API surface, and the migration that backs it.

Everything here runs against an in-process SQLite database, so the suite needs no
Postgres, no Redis and no network. That is not just convenience: a test that only runs
where someone happened to have the stack up is a test nobody runs.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from specguard.api.app import create_app
from specguard.api.deps import get_session
from specguard.db.models import Base, Feedback, Job, JobStatus, Result
from specguard.models.rule import RuleId, Verdict

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(sessionmaker) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.queue = None  # No Redis in tests; submission still records the job.

    async def _session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_session] = _session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


class TestSubmit:
    async def test_accepts_a_pdf_and_returns_a_job_id(self, client) -> None:
        response = await client.post(
            "/checks", files={"file": ("spec.pdf", PDF, "application/pdf")}
        )
        assert response.status_code == 202
        body = response.json()
        assert uuid.UUID(body["job_id"])
        assert body["status"] == JobStatus.QUEUED.value
        assert body["correlation_id"]

    async def test_rejects_a_non_pdf_before_queueing_anything(self, client) -> None:
        response = await client.post(
            "/checks", files={"file": ("spec.pdf", b"PK\x03\x04not a pdf", "application/pdf")}
        )
        assert response.status_code == 422
        assert "not a PDF" in response.json()["detail"]

    async def test_rejects_an_empty_file(self, client) -> None:
        response = await client.post(
            "/checks", files={"file": ("spec.pdf", b"", "application/pdf")}
        )
        assert response.status_code == 422

    async def test_echoes_a_supplied_correlation_id(self, client) -> None:
        response = await client.post(
            "/checks",
            files={"file": ("spec.pdf", PDF, "application/pdf")},
            headers={"x-correlation-id": "trace-me-1234"},
        )
        # A caller that brings its own id can find these logs in its own system.
        assert response.headers["x-correlation-id"] == "trace-me-1234"
        assert response.json()["correlation_id"] == "trace-me-1234"


class TestRetrieve:
    async def test_unknown_job_is_404(self, client) -> None:
        response = await client.get(f"/checks/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_a_queued_job_has_no_report_yet(self, client) -> None:
        submitted = await client.post("/checks", files={"file": ("s.pdf", PDF, "application/pdf")})
        job_id = submitted.json()["job_id"]
        body = (await client.get(f"/checks/{job_id}")).json()
        assert body["status"] == JobStatus.QUEUED.value
        assert body["report"] is None


class TestFeedback:
    async def test_records_a_correction(self, client) -> None:
        submitted = await client.post("/checks", files={"file": ("s.pdf", PDF, "application/pdf")})
        job_id = submitted.json()["job_id"]
        response = await client.post(
            f"/checks/{job_id}/feedback",
            json={
                "rule_id": RuleId.ALLERGEN_EMPHASIS.value,
                "corrected_verdict": Verdict.PASS.value,
                "comment": "MILK is emphasised in the printed artwork.",
                "reviewer": "qa",
            },
        )
        assert response.status_code == 201
        assert response.json()["corrected_verdict"] == Verdict.PASS.value

    async def test_a_correction_attaches_to_the_run_that_produced_the_verdict(
        self, client, sessionmaker, monkeypatch
    ) -> None:
        """The point of storing the run id: a human override lands on the trace it disputes."""
        submitted = await client.post("/checks", files={"file": ("s.pdf", PDF, "application/pdf")})
        job_id = uuid.UUID(submitted.json()["job_id"])

        async with sessionmaker() as session:
            session.add(
                Result(
                    job_id=job_id,
                    rule_id=RuleId.ORIGIN_DECLARATION.value,
                    verdict=Verdict.FAIL.value,
                    confidence=0.9,
                    rationale="origin is not declared",
                    langsmith_run_id="run-xyz",
                )
            )
            await session.commit()

        pushed: dict[str, object] = {}

        def _capture(run_id: str, **kwargs: object) -> str:
            pushed.update({"run_id": run_id, **kwargs})
            return "feedback-1"

        monkeypatch.setattr("specguard.api.routers.record_feedback", _capture)

        response = await client.post(
            f"/checks/{job_id}/feedback",
            json={
                "rule_id": RuleId.ORIGIN_DECLARATION.value,
                "corrected_verdict": Verdict.PASS.value,
                "reviewer": "qa",
            },
        )
        assert response.status_code == 201
        assert pushed["run_id"] == "run-xyz"
        assert pushed["corrected_verdict"] == Verdict.PASS.value
        assert pushed["original_verdict"] == Verdict.FAIL.value

        async with sessionmaker() as session:
            stored = (await session.execute(select(Feedback))).scalars().all()
        assert [entry.langsmith_feedback_id for entry in stored] == ["feedback-1"]

    async def test_a_correction_is_kept_even_when_there_is_no_run_to_attach_it_to(
        self, client
    ) -> None:
        """A verdict recorded before tracing existed is still correctable."""
        submitted = await client.post("/checks", files={"file": ("s.pdf", PDF, "application/pdf")})
        response = await client.post(
            f"/checks/{submitted.json()['job_id']}/feedback",
            json={"rule_id": RuleId.MANDATORY_FIELDS.value, "corrected_verdict": "PASS"},
        )
        assert response.status_code == 201

    async def test_rejects_an_unknown_rule(self, client) -> None:
        submitted = await client.post("/checks", files={"file": ("s.pdf", PDF, "application/pdf")})
        job_id = submitted.json()["job_id"]
        response = await client.post(
            f"/checks/{job_id}/feedback",
            json={"rule_id": "NOT_A_RULE", "corrected_verdict": "PASS"},
        )
        assert response.status_code == 422

    async def test_rejects_feedback_on_an_unknown_job(self, client) -> None:
        response = await client.post(
            f"/checks/{uuid.uuid4()}/feedback",
            json={"rule_id": RuleId.ORIGIN_DECLARATION.value, "corrected_verdict": "FAIL"},
        )
        assert response.status_code == 404


class TestOperational:
    async def test_healthz_reports_each_dependency(self, client) -> None:
        body = (await client.get("/healthz")).json()
        assert body["status"] in {"ok", "degraded"}
        assert "postgres" in body["checks"]

    async def test_metrics_are_prometheus_formatted(self, client) -> None:
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "specguard_checks_total" in response.text


class TestSchema:
    async def test_the_models_persist_a_full_check(self, sessionmaker) -> None:
        # Exercises the shape the worker writes: a job, its per-rule rows, and a
        # reviewer's later disagreement with one of them.
        async with sessionmaker() as session:
            job = Job(
                correlation_id="abc123",
                filename="spec.pdf",
                sha256="0" * 64,
                byte_size=1024,
            )
            session.add(job)
            await session.flush()
            session.add(
                Result(
                    job_id=job.id,
                    rule_id=RuleId.ALLERGEN_EMPHASIS.value,
                    verdict=Verdict.FAIL.value,
                    confidence=0.92,
                    rationale="MILK is not distinguished.",
                    suggested_fix="Emphasise MILK.",
                    requires_human_review=True,
                    citations={"items": []},
                    metrics={"allergens_found": 1.0},
                )
            )
            session.add(
                Feedback(
                    job_id=job.id,
                    rule_id=RuleId.ALLERGEN_EMPHASIS.value,
                    corrected_verdict=Verdict.PASS.value,
                    comment="Emphasised in artwork.",
                )
            )
            await session.commit()

            stored = await session.get(Job, job.id)
            assert stored is not None
            await session.refresh(stored, ["results", "feedback"])
            assert stored.results[0].requires_human_review
            assert stored.feedback[0].corrected_verdict == Verdict.PASS.value
