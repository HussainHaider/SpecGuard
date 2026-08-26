"""Prometheus metrics.

Deliberately few. A metric nobody reads is a maintenance cost, so these are the four
questions someone actually asks about this system in production: is it working, how long
does it take, what is it spending, and how often does it decline to answer.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry()

CHECKS_TOTAL = Counter(
    "specguard_checks_total",
    "Check jobs by terminal status.",
    labelnames=("status",),
    registry=REGISTRY,
)

CHECK_DURATION = Histogram(
    "specguard_check_duration_seconds",
    "Wall time for one complete check.",
    buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300),
    registry=REGISTRY,
)

VERDICTS_TOTAL = Counter(
    "specguard_verdicts_total",
    "Rule verdicts by rule and outcome. Abstention rate is the ratio worth alerting on.",
    labelnames=("rule_id", "verdict"),
    registry=REGISTRY,
)

LLM_COST_USD = Counter(
    "specguard_llm_cost_usd_total",
    "Cumulative model spend, by provider and model.",
    labelnames=("provider", "model"),
    registry=REGISTRY,
)

GUARDRAIL_TRIPS = Counter(
    "specguard_guardrail_trips_total",
    "Guardrails that fired, by name. Injection attempts show up here.",
    labelnames=("guardrail",),
    registry=REGISTRY,
)
