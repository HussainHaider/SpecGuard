"""Tier 1 eval: deterministic ground-truth scoring over the fixture set. Gates CI.

Runs entirely offline. Retrieval and every model call are replayed from recorded
fixtures, so this produces the same numbers on any machine, costs nothing, and reaches
no network — which is what lets it gate a build.

    uv run python -m evals.run_eval
    uv run python -m evals.run_eval --fail-under-config
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import evals.pipeline as pipeline
from specguard.llm.factory import FIXTURE_DIR
from specguard.llm.fake import FakeClient
from specguard.vectorstore.fixtures import FixtureStore

SEARCH_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "searches"

#: Gates. Accuracy is a floor rather than a target, but the two verdict counts are hard
#: zeros: a compliance tool that reports a non-compliant product as compliant is worse
#: than one that declines to answer, so a single false pass fails the build.
MIN_ACCURACY = 0.85
MAX_FALSE_PASSES = 0
MAX_WRONG_VERDICTS = 1


def build_report() -> pipeline.Report:
    """Score every rule against every fixture spec, fully offline."""
    return pipeline.run(FixtureStore(SEARCH_FIXTURES), FakeClient(FIXTURE_DIR, model="recorded"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fail-under-config",
        action="store_true",
        help="Exit non-zero if the report is below the configured gates.",
    )
    args = parser.parse_args()

    report = build_report()
    print(pipeline.render(report))

    if not args.fail_under_config:
        return

    failures: list[str] = []
    if report.accuracy < MIN_ACCURACY:
        failures.append(f"accuracy {report.accuracy:.1%} is below {MIN_ACCURACY:.0%}")
    if len(report.false_passes) > MAX_FALSE_PASSES:
        failures.append(f"{len(report.false_passes)} non-compliant spec(s) reported as compliant")
    if len(report.wrong_verdicts) > MAX_WRONG_VERDICTS:
        failures.append(
            f"{len(report.wrong_verdicts)} wrong verdicts, above the {MAX_WRONG_VERDICTS} allowed"
        )

    if failures:
        print("\nFAILED: " + "; ".join(failures), file=sys.stderr)
        raise SystemExit(1)
    print("\ngates passed")


if __name__ == "__main__":
    main()
