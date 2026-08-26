"""Tier 1 metrics: deterministic ground-truth scoring over the golden set. Gates CI.

Every number here is computed by comparing an output to a label a human or a generator
wrote down in advance. No model judges anything in this file, and nothing in it needs a
network — which is precisely what makes it fit to fail a build (non-negotiable #6).

Definitions are spelled out rather than assumed, because most of these have more than
one reasonable reading and a metric whose definition is implicit is a metric nobody can
argue with:

* **accuracy** — the share of golden records whose verdict matches the label exactly.
  An abstention is counted as wrong. It is a designed outcome, not a correct answer.
* **abstention rate** — the share answered NEEDS_REVIEW. Reported beside accuracy so the
  two are read together: a system can reach high accuracy by abstaining on everything
  hard, and these two numbers together show whether it did.
* **allergen false-negative rate** — of the allergen-sensitive records whose label is
  FAIL, the share not reported as FAIL. Strict: an abstention counts as a miss, because
  a reviewer who is told "needs review" has not been told there is an undeclared
  allergen. Measured over the same rule set the runtime gate escalates on, so the metric
  and the safety behaviour cannot disagree about which rules are life-critical.
* **citation resolution rate** — of the decided verdicts, the share whose every citation
  resolves to a chunk in the indexed corpus. This is non-negotiable #1 as a number.
* **recall@5** — mean over queries of the share of that query's relevant clauses in the
  top 5. ``hit_rate@5`` is the looser companion: at least one relevant clause in the top
  5, which is the threshold that actually decides whether a rule can be answered.
* **latency** — p50/p95 over per-rule wall time. A replayed run reports what the calls
  cost in time when they were *recorded*, and reports nothing where that was never
  captured. It never reports the replay's own microseconds as though they were real.
* **cost per spec** — model spend for one complete eight-rule check, averaged over the
  specifications in the split. Measured over the whole check rather than over the rules
  the golden set happens to sample, because the question it answers is "what does it
  cost to check one supplier document", and half a check does not answer it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from evals.golden import GoldenRetrieval, GoldenRule, Split
from specguard.guardrails.verdicts import ALLERGEN_SENSITIVE
from specguard.models.rule import RuleId, RuleResult, Verdict


@dataclass(frozen=True)
class Scored:
    """One golden record and what the pipeline actually produced for it."""

    golden: GoldenRule
    result: RuleResult

    @property
    def expected(self) -> Verdict:
        return self.golden.expected_verdict

    @property
    def actual(self) -> Verdict:
        return self.result.verdict

    @property
    def correct(self) -> bool:
        return self.actual is self.expected

    @property
    def abstained(self) -> bool:
        return self.actual is Verdict.NEEDS_REVIEW

    @property
    def wrong_verdict(self) -> bool:
        """A confident answer that is wrong — a different failure from declining to answer."""
        return not self.correct and not self.abstained

    @property
    def false_pass(self) -> bool:
        """The worst outcome: a non-compliant specification reported as compliant."""
        return self.actual is Verdict.PASS and self.expected is Verdict.FAIL

    @property
    def cost_usd(self) -> float:
        return sum(usage.cost_usd for usage in self.result.llm_usage)

    @property
    def latency_ms(self) -> int | None:
        """Time spent in model calls for this rule, or None where nothing measured it.

        Model-backed rules only, and only where a real latency was captured. Two
        deliberate exclusions. A deterministic rule is microseconds of arithmetic, so
        including it would drag the percentiles to zero and hide the latency that
        actually matters. And a replayed call has no latency of its own — reporting the
        replay's own microseconds would say this system answers instantly, which is a
        lie a fixture makes very easy to tell.
        """
        if not self.result.llm_usage:
            return None
        return sum(usage.latency_ms for usage in self.result.llm_usage) or None


@dataclass(frozen=True)
class RetrievalScored:
    """One golden query and the chunk ids that came back for it."""

    golden: GoldenRetrieval
    retrieved: Sequence[str]

    @property
    def recall(self) -> float:
        relevant = set(self.golden.relevant_chunk_ids)
        top = set(self.retrieved[:5])
        return len(relevant & top) / len(relevant)

    @property
    def hit(self) -> bool:
        return bool(set(self.golden.relevant_chunk_ids) & set(self.retrieved[:5]))


def _percentile(values: Sequence[int], q: float) -> float:
    """Nearest-rank percentile. Deliberately simple: these samples are tens, not millions."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(q * len(ordered) + 0.5)))
    return float(ordered[rank - 1])


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


@dataclass(frozen=True)
class Metrics:
    """Every tier 1 number, for one split.

    Optionals are genuinely absent rather than zero. A split with no allergen failure in
    it has no false-negative rate, and printing 0.0 there would read as "nothing was
    missed" when the truth is "nothing was asked".
    """

    split: str
    records: int = 0
    specs: int = 0

    accuracy: float = 0.0
    per_rule: dict[RuleId, tuple[int, int]] = field(default_factory=dict)
    abstention_rate: float = 0.0
    wrong_verdicts: int = 0
    false_passes: int = 0

    allergen_cases: int = 0
    allergen_fnr: float | None = None
    allergen_false_passes: int = 0

    decided: int = 0
    citation_resolution_rate: float | None = None

    retrieval_queries: int = 0
    recall_at_5: float | None = None
    hit_rate_at_5: float | None = None

    latency_samples: int = 0
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None

    cost_usd: float = 0.0
    cost_per_spec_usd: float = 0.0


def compute(
    scored: Sequence[Scored],
    retrieval: Sequence[RetrievalScored] = (),
    *,
    known_chunk_ids: set[str] | None = None,
    spec_cost: dict[str, float] | None = None,
    split: str = "all",
) -> Metrics:
    """Every tier 1 metric over one set of scored records.

    ``spec_cost`` is what a full eight-rule check cost for each specification, measured
    separately from the sampled records being scored here. Without it, cost falls back
    to the spend on the sampled rules alone, which understates a check by however much
    of it the golden set did not sample.
    """
    if not scored and not retrieval:
        return Metrics(split=split)

    per_rule: dict[RuleId, tuple[int, int]] = {}
    for item in scored:
        correct, total = per_rule.get(item.golden.rule_id, (0, 0))
        per_rule[item.golden.rule_id] = (correct + item.correct, total + 1)

    allergen = [
        item
        for item in scored
        if item.golden.rule_id in ALLERGEN_SENSITIVE and item.expected is Verdict.FAIL
    ]
    decided = [item for item in scored if not item.abstained]

    resolution: float | None = None
    if known_chunk_ids is not None and decided:
        resolvable = sum(
            all(citation.chunk_id in known_chunk_ids for citation in item.result.citations)
            and bool(item.result.citations)
            for item in decided
        )
        resolution = _rate(resolvable, len(decided))

    latencies = [item.latency_ms for item in scored if item.latency_ms is not None]

    specs = {item.golden.spec_id for item in scored}
    cost = (
        sum(value for spec_id, value in spec_cost.items() if spec_id in specs)
        if spec_cost is not None
        else sum(item.cost_usd for item in scored)
    )

    return Metrics(
        split=split,
        records=len(scored),
        specs=len(specs),
        accuracy=_rate(sum(item.correct for item in scored), len(scored)),
        per_rule=per_rule,
        abstention_rate=_rate(sum(item.abstained for item in scored), len(scored)),
        wrong_verdicts=sum(item.wrong_verdict for item in scored),
        false_passes=sum(item.false_pass for item in scored),
        allergen_cases=len(allergen),
        allergen_fnr=(
            _rate(sum(item.actual is not Verdict.FAIL for item in allergen), len(allergen))
            if allergen
            else None
        ),
        allergen_false_passes=sum(item.false_pass for item in allergen),
        decided=len(decided),
        citation_resolution_rate=resolution,
        retrieval_queries=len(retrieval),
        recall_at_5=(
            sum(item.recall for item in retrieval) / len(retrieval) if retrieval else None
        ),
        hit_rate_at_5=(
            _rate(sum(item.hit for item in retrieval), len(retrieval)) if retrieval else None
        ),
        latency_samples=len(latencies),
        p50_latency_ms=_percentile(latencies, 0.50) if latencies else None,
        p95_latency_ms=_percentile(latencies, 0.95) if latencies else None,
        cost_usd=cost,
        cost_per_spec_usd=_rate_float(cost, len(specs)),
    )


def _rate_float(numerator: float, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def by_split(
    scored: Sequence[Scored],
    retrieval: Sequence[RetrievalScored] = (),
    *,
    known_chunk_ids: set[str] | None = None,
    spec_cost: dict[str, float] | None = None,
) -> dict[str, Metrics]:
    """Metrics for the whole set and for each split, reported separately.

    Separately because a held-out number that is quietly averaged into a dev number
    stops being held out. The combined figure is kept as well: it is the one that is
    comparable across milestones, since the split itself can be rebuilt.
    """
    out = {
        "all": compute(
            scored,
            retrieval,
            known_chunk_ids=known_chunk_ids,
            spec_cost=spec_cost,
            split="all",
        ),
    }
    for split in Split:
        out[split.value] = compute(
            [item for item in scored if item.golden.split is split],
            [item for item in retrieval if item.golden.split is split],
            known_chunk_ids=known_chunk_ids,
            spec_cost=spec_cost,
            split=split.value,
        )
    return out


def render(metrics: Metrics) -> str:
    """One split, as a short block a person can read in a terminal."""

    def pct(value: float | None) -> str:
        return "     —" if value is None else f"{value:6.1%}"

    def ms(value: float | None) -> str:
        return "    —" if value is None else f"{value:5.0f}"

    lines = [
        f"[{metrics.split}] {metrics.records} records over {metrics.specs} specs",
        f"  accuracy              {pct(metrics.accuracy)}   "
        f"({sum(c for c, _ in metrics.per_rule.values())}/{metrics.records})",
        f"  abstention rate       {pct(metrics.abstention_rate)}",
        f"  wrong verdicts        {metrics.wrong_verdicts:6d}   "
        f"(false passes {metrics.false_passes})",
        f"  allergen FNR          {pct(metrics.allergen_fnr)}   "
        f"(n={metrics.allergen_cases}, false passes {metrics.allergen_false_passes})",
        f"  citation resolution   {pct(metrics.citation_resolution_rate)}   "
        f"(of {metrics.decided} decided)",
        f"  recall@5              {pct(metrics.recall_at_5)}   "
        f"(hit rate {pct(metrics.hit_rate_at_5).strip()}, n={metrics.retrieval_queries})",
        f"  latency p50 / p95     {ms(metrics.p50_latency_ms)} / {ms(metrics.p95_latency_ms)} ms"
        f"   (n={metrics.latency_samples})",
        f"  cost per spec         ${metrics.cost_per_spec_usd:.4f}   "
        f"(${metrics.cost_usd:.4f} total)",
        "",
        "  per rule:",
    ]
    for rule_id, (correct, total) in sorted(metrics.per_rule.items(), key=lambda kv: kv[0].value):
        lines.append(f"    {rule_id.value:28s} {correct:>3}/{total}")
    return "\n".join(lines)
