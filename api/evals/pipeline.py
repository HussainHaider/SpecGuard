"""Run every rule over every fixture spec and score the result.

Shared by the recording script and by the tier 1 eval, so the number in the README is
produced by the same code path a test asserts on — not by a script that happened to be
run once and then lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from specguard.fixtures.generate import SpecFixture, build_sheets, load_manifest
from specguard.fixtures.to_spec import spec_for_sheet
from specguard.llm.protocol import LLMClient
from specguard.models.rule import RuleId, Verdict
from specguard.rules.base import RagContext, RuleContext
from specguard.rules.registry import deterministic_rules, rag_rules
from specguard.vectorstore.protocol import VectorStore

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "fixtures" / "specs"


@dataclass
class Outcome:
    """One rule's result on one spec, against what the manifest expects."""

    spec_id: str
    rule_id: RuleId
    expected: Verdict
    actual: Verdict
    rationale: str

    @property
    def correct(self) -> bool:
        return self.actual is self.expected

    @property
    def is_abstention(self) -> bool:
        return self.actual is Verdict.NEEDS_REVIEW and not self.correct

    @property
    def is_wrong_verdict(self) -> bool:
        """A confident answer that is wrong — far worse than declining to answer."""
        return not self.correct and self.actual is not Verdict.NEEDS_REVIEW

    @property
    def is_false_pass(self) -> bool:
        """The worst outcome: a non-compliant spec reported as compliant."""
        return self.actual is Verdict.PASS and self.expected is Verdict.FAIL


@dataclass
class Report:
    """Scores across the whole fixture set."""

    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def correct(self) -> int:
        return sum(o.correct for o in self.outcomes)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def wrong_verdicts(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.is_wrong_verdict]

    @property
    def false_passes(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.is_false_pass]

    @property
    def abstentions(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.is_abstention]

    def by_rule(self) -> dict[RuleId, tuple[int, int]]:
        """Correct and total, per rule."""
        scores: dict[RuleId, tuple[int, int]] = {}
        for outcome in self.outcomes:
            correct, total = scores.get(outcome.rule_id, (0, 0))
            scores[outcome.rule_id] = (correct + outcome.correct, total + 1)
        return scores


def load_specs() -> list[tuple[SpecFixture, object]]:
    """Every fixture spec paired with its ground-truth ProductSpec."""
    manifest = {entry.spec_id: entry for entry in load_manifest(SPEC_DIR / "manifest.jsonl")}
    return [
        (
            manifest[spec_id],
            spec_for_sheet(sheet, SPEC_DIR / "generated" / manifest[spec_id].filename),
        )
        for spec_id, sheet in build_sheets()
        if spec_id in manifest
    ]


def run(store: VectorStore, client: LLMClient, *, retrieval_limit: int = 5) -> Report:
    """Evaluate all eight rules against every fixture spec."""
    report = Report()
    for entry, spec in load_specs():
        source_version = f"02011R1169-20180101-{entry.language.value}"
        plain = RuleContext(source_version=source_version, language=entry.language)
        rag = RagContext(
            source_version=source_version,
            language=entry.language,
            store=store,
            client=client,
            retrieval_limit=retrieval_limit,
        )
        for rule_id, rule in deterministic_rules().items():
            result = rule.evaluate(spec, plain)  # type: ignore[arg-type]
            report.outcomes.append(
                Outcome(
                    entry.spec_id,
                    rule_id,
                    entry.expected_verdicts[rule_id],
                    result.verdict,
                    result.rationale,
                )
            )
        for rule_id, rag_rule in rag_rules().items():
            result = rag_rule.evaluate(spec, rag)  # type: ignore[arg-type]
            report.outcomes.append(
                Outcome(
                    entry.spec_id,
                    rule_id,
                    entry.expected_verdicts[rule_id],
                    result.verdict,
                    result.rationale,
                )
            )
    return report


def render(report: Report) -> str:
    """A short human-readable summary."""
    lines = [
        f"accuracy {report.correct}/{report.total} ({report.accuracy:.1%})",
        f"wrong verdicts {len(report.wrong_verdicts)}  (false passes {len(report.false_passes)})",
        f"abstentions {len(report.abstentions)}",
        "",
        "per rule:",
    ]
    for rule_id, (correct, total) in sorted(report.by_rule().items(), key=lambda kv: kv[0].value):
        lines.append(f"   {rule_id.value:28s} {correct:>3}/{total}")
    if report.wrong_verdicts:
        lines.append("")
        lines.append("wrong verdicts:")
        for outcome in report.wrong_verdicts:
            lines.append(
                f"   {outcome.spec_id} {outcome.rule_id.value}: expected "
                f"{outcome.expected.value}, got {outcome.actual.value}"
            )
    return "\n".join(lines)
