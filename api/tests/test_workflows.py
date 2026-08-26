"""The committed n8n workflows.

Workflows are JSON in a repository, which means they can rot silently: a URL that only
works on the author's laptop, a credential accidentally exported, a node id that collides
after an import. None of that shows up until someone runs the workflow, and by then it is
running on a schedule at four in the morning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

WORKFLOW_DIR = Path(__file__).resolve().parents[2] / "n8n"


def workflows() -> list[tuple[str, dict]]:
    if not WORKFLOW_DIR.exists():
        pytest.skip("no workflows directory")
    found = [
        (path.name, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(WORKFLOW_DIR.glob("*.json"))
    ]
    if not found:
        pytest.skip("no workflows exported")
    return found


@pytest.fixture(scope="module")
def exported() -> list[tuple[str, dict]]:
    return workflows()


def test_both_workflows_are_committed(exported) -> None:
    names = {name for name, _ in exported}
    assert names == {"intake.json", "regulation-watcher.json"}


def test_http_nodes_address_the_compose_service_not_localhost(exported) -> None:
    """localhost inside n8n is the n8n container.

    A node pointed at localhost:8000 fails with a connection error that looks like the API
    is down, which is the single most confusing way a self-hosted n8n setup breaks.
    """
    for name, workflow in exported:
        for node in workflow["nodes"]:
            # n8n expressions are stored with a leading '='. The host is what matters
            # here, so the marker is stripped before the URL is read.
            url = str(node.get("parameters", {}).get("url", "")).lstrip("=")
            if not url or "$env" in url:
                continue
            assert "localhost" not in url, f"{name}: {node['name']} points at localhost"
            assert "127.0.0.1" not in url, f"{name}: {node['name']} points at 127.0.0.1"
            if "/checks" in url or "/clauses" in url:
                assert url.startswith("http://api:8000"), f"{name}: {node['name']} -> {url}"


def test_no_credentials_were_exported(exported) -> None:
    """`n8n export:workflow` does not include credentials, and this keeps it that way.

    Credentials are encrypted with N8N_ENCRYPTION_KEY and belong to a machine, not to a
    repository. A workflow file carrying one is a secret in git history.
    """
    for name, workflow in exported:
        blob = json.dumps(workflow)
        assert '"credentials"' not in blob, f"{name} carries a credentials block"
        for marker in ("api_key", "apiKey", "password", "Bearer ", "sk-"):
            assert marker not in blob, f"{name} looks like it contains a secret: {marker!r}"


def test_node_ids_are_unique_within_a_workflow(exported) -> None:
    for name, workflow in exported:
        ids = [node["id"] for node in workflow["nodes"]]
        assert len(ids) == len(set(ids)), f"{name} has duplicate node ids"


def test_every_connection_names_a_node_that_exists(exported) -> None:
    """A dangling connection imports without complaint and silently does nothing."""
    for name, workflow in exported:
        known = {node["name"] for node in workflow["nodes"]}
        for source, outputs in workflow["connections"].items():
            assert source in known, f"{name}: connection from unknown node {source!r}"
            for branch in outputs.get("main", []):
                for link in branch:
                    assert link["node"] in known, f"{name}: connection to unknown {link['node']!r}"


def test_workflows_are_inactive_as_committed(exported) -> None:
    """Importing must not start a schedule.

    A clean clone that immediately begins polling an inbox and re-indexing a corpus is a
    surprise; activation is a deliberate act in the editor.
    """
    for name, workflow in exported:
        assert workflow.get("active") is False, f"{name} is committed as active"


class TestRegulationWatcher:
    def test_it_re_indexes_without_resetting_the_collection(self, exported) -> None:
        """--reset would drop the collection and every stored citation with it.

        A re-index overwrites by deterministic chunk id, which is the whole reason the
        weekly run is safe. Dropping first turns it into an outage.
        """
        watcher = dict(exported)["regulation-watcher.json"]
        commands = [str(node.get("parameters", {}).get("command", "")) for node in watcher["nodes"]]
        seed = [c for c in commands if "corpus.seed" in c]
        assert seed, "the watcher never re-indexes"
        assert all("--reset" not in c for c in seed)

    def test_it_decides_what_to_re_run_rather_than_re_running_everything(self, exported) -> None:
        watcher = dict(exported)["regulation-watcher.json"]
        commands = " ".join(
            str(node.get("parameters", {}).get("command", "")) for node in watcher["nodes"]
        )
        assert "specguard.ops.affected" in commands

    def test_it_short_circuits_when_nothing_changed(self, exported) -> None:
        """A week with no consolidation must not re-index or re-run anything."""
        watcher = dict(exported)["regulation-watcher.json"]
        names = {node["name"] for node in watcher["nodes"]}
        assert "Changed?" in names
        false_branch = watcher["connections"]["Changed?"]["main"][1]
        assert false_branch, "the unchanged path goes nowhere"
        assert false_branch[0]["node"] == "Nothing changed"
