"""Demo mode: what the public deployment serves.

Two properties carry the whole feature. It must never call a model or an embedding —
a public endpoint anyone can post to, wired to a paid API, is a bill waiting to happen.
And a replayed report must be visibly replayed, because a demo that looks like a live
result misleads exactly the person the demo exists to inform.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from specguard import demo
from specguard.api.app import create_app
from specguard.api.deps import get_session
from specguard.config import Settings, get_settings
from specguard.db.models import Base, JobStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "fixtures" / "specs" / "generated"
REPORT_DIR = REPO_ROOT / "fixtures" / "reports"


@pytest.fixture(autouse=True)
def _demo_settings(monkeypatch):
    """Run the app with DEMO_MODE on, without touching the developer's environment."""
    get_settings.cache_clear()
    monkeypatch.setenv("DEMO_MODE", "true")
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app()
    app.state.queue = None

    async def _session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_session] = _session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await engine.dispose()


def _a_demo_spec() -> tuple[str, bytes]:
    """A fixture PDF that has a pre-computed report."""
    if not REPORT_DIR.exists():
        pytest.skip("no pre-computed reports")
    demo._index.cache_clear()
    for path in sorted(REPORT_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        digest = payload["spec"]["source"]["sha256"]
        for pdf in SPEC_DIR.glob("*.pdf"):
            if hashlib.sha256(pdf.read_bytes()).hexdigest() == digest:
                return pdf.name, pdf.read_bytes()
    pytest.skip("no report matches a generated spec")


class TestReplay:
    def test_reports_are_indexed_by_the_document_that_produced_them(self):
        assert demo.available() > 0

    def test_it_returns_the_report_for_that_exact_document(self):
        _, payload = _a_demo_spec()
        report = demo.report_for(payload)
        assert report.spec.source.sha256 == hashlib.sha256(payload).hexdigest()

    def test_an_unknown_document_gets_no_report_at_all(self):
        # Never somebody else's verdicts. Returning a report computed from a different
        # document is the worst failure a compliance tool can have, demo or not.
        with pytest.raises(demo.NoDemoReportError):
            demo.report_for(b"%PDF-1.7 not a spec we know")

    def test_a_replayed_report_says_so(self):
        _, payload = _a_demo_spec()
        report = demo.report_for(payload)
        assert report.demo is True
        assert report.demo_note


class TestEndpoint:
    async def test_a_known_spec_comes_back_finished_immediately(self, client) -> None:
        name, payload = _a_demo_spec()
        submitted = await client.post("/checks", files={"file": (name, payload, "application/pdf")})
        assert submitted.status_code == 202
        assert submitted.json()["status"] == JobStatus.SUCCEEDED.value

        body = (await client.get(f"/checks/{submitted.json()['job_id']}")).json()
        assert body["report"]["demo"] is True
        assert body["report"]["results"]

    async def test_an_unknown_spec_is_refused_rather_than_substituted(self, client) -> None:
        response = await client.post(
            "/checks", files={"file": ("x.pdf", b"%PDF-1.7 unknown", "application/pdf")}
        )
        assert response.status_code == 422
        assert "demo mode" in response.json()["detail"]

    async def test_nothing_is_queued(self, client) -> None:
        # The demo path must not enqueue: there is no worker in a demo deployment, and a
        # job nobody will ever run is a report that never arrives.
        name, payload = _a_demo_spec()
        await client.post("/checks", files={"file": (name, payload, "application/pdf")})
        assert client._transport.app.state.queue is None

    async def test_healthz_admits_it_is_a_demo(self, client) -> None:
        body = (await client.get("/healthz")).json()
        assert body["checks"]["demo_mode"] is True
        # Demo mode is not a degraded state; it is the intended one for this deployment.
        assert body["status"] == "ok"


class TestNoModelCalls:
    def test_the_demo_module_builds_no_client_and_no_store(self):
        """The guarantee is structural, not a promise.

        `specguard.demo` imports neither the LLM factory nor a vector store, so there is
        no configuration under which a demo deployment starts spending money.
        """
        source = Path(demo.__file__).read_text(encoding="utf-8")
        for forbidden in ("build_client", "QdrantVectorStore", "Encoder", "llm."):
            assert forbidden not in source, forbidden

    def test_demo_settings_do_not_require_a_provider(self):
        settings = Settings(demo_mode=True, llm_provider="fake")
        assert settings.demo_mode is True
