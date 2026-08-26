"""Non-negotiable #2, enforced mechanically.

Arithmetic, field presence and keyword matching are Python. This is a deliberate design
position, and a comment asking future maintainers to respect it is not enforcement — so
these tests fail loudly if a model call is ever introduced into the deterministic rules,
whether directly, through a helper, or at runtime.
"""

from __future__ import annotations

import ast
import socket
from pathlib import Path

import pytest

from specguard.fixtures.generate import build_sheets, load_manifest
from specguard.fixtures.to_spec import spec_for_sheet
from specguard.models.common import Language
from specguard.rules.base import RuleContext
from specguard.rules.registry import deterministic_rules

RULES_DIR = Path(__file__).resolve().parents[1] / "src" / "specguard" / "rules" / "deterministic"
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "specs"

#: Importing any of these from a deterministic rule means it can reach a model.
FORBIDDEN_ROOTS = {"anthropic", "openai", "httpx", "requests", "urllib", "aiohttp", "socket"}
FORBIDDEN_MODULES = {"specguard.llm"}


def _imported_modules(path: Path) -> set[str]:
    """Every module name a file imports, however it imports it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestStaticImports:
    """Catches a model call added anywhere in the deterministic package, including helpers."""

    @pytest.mark.parametrize("path", sorted(RULES_DIR.glob("*.py")), ids=lambda p: p.name)
    def test_rule_module_cannot_reach_a_model(self, path: Path) -> None:
        imported = _imported_modules(path)
        offending = {
            name
            for name in imported
            if name.split(".")[0] in FORBIDDEN_ROOTS
            or any(name.startswith(prefix) for prefix in FORBIDDEN_MODULES)
        }
        assert not offending, (
            f"{path.name} imports {sorted(offending)}. Deterministic rules are pure Python "
            "by design — arithmetic, field presence and keyword matching must never be "
            "routed through a model (CLAUDE.md non-negotiable #2)."
        )

    def test_the_guard_would_actually_catch_a_violation(self, tmp_path: Path) -> None:
        # A guard nobody has seen fail is a guard nobody knows works.
        offender = tmp_path / "sneaky_rule.py"
        offender.write_text("from specguard.llm.factory import build_client\n")
        imported = _imported_modules(offender)
        assert any(name.startswith("specguard.llm") for name in imported)


class TestRuntimeBehaviour:
    """Even a dynamically imported client would need a socket. Deny it and re-run."""

    def test_rules_evaluate_with_networking_disabled(self, monkeypatch) -> None:
        if not (FIXTURE_DIR / "manifest.jsonl").exists():
            pytest.skip("fixtures not generated")

        def deny(*args: object, **kwargs: object) -> None:
            raise AssertionError(
                "a deterministic rule opened a network connection; non-negotiable #2 says "
                "these rules never call a model"
            )

        manifest = {entry.spec_id: entry for entry in load_manifest(FIXTURE_DIR / "manifest.jsonl")}
        cases = [(manifest[spec_id], spec_for_sheet(sheet)) for spec_id, sheet in build_sheets()]

        monkeypatch.setattr(socket, "socket", deny)
        monkeypatch.setattr(socket, "create_connection", deny)

        for entry, spec in cases:
            context = RuleContext(
                source_version=f"02011R1169-20180101-{entry.language.value}",
                language=Language(entry.language),
            )
            for rule in deterministic_rules().values():
                rule.evaluate(spec, context)

    def test_no_rule_result_carries_llm_usage(self) -> None:
        # The model layer refuses to build such a result at all, but asserting it here
        # means the guarantee is covered even if that validator is ever relaxed.
        if not (FIXTURE_DIR / "manifest.jsonl").exists():
            pytest.skip("fixtures not generated")
        context = RuleContext(source_version="02011R1169-20180101-en")
        for _, sheet in build_sheets():
            spec = spec_for_sheet(sheet)
            for rule in deterministic_rules().values():
                assert rule.evaluate(spec, context).llm_usage == []
