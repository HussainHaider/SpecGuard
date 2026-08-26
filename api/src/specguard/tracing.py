"""LangSmith tracing: one span per graph node, one per rule, one per model call.

Non-negotiable #5 says every LLM call is traced with prompt version, rule id, tokens,
cost and latency. That is enforced here rather than at each call site: ``TracedClient``
wraps whatever provider the factory built, so a new caller cannot forget to trace and a
new provider cannot implement tracing differently.

Three things are deliberate:

* **Tracing is off unless configured.** ``LANGSMITH_TRACING`` gates it, and when it is
  off nothing in this module touches the network or the langsmith client at all. The
  default test run must reach no API, and a tracing layer that half-runs would be a way
  to break that quietly.
* **The rule id comes from context, not from the signature.** A rule's judge and verify
  calls happen several frames below the graph node that knows which rule is running.
  Threading a parameter through would mean every function in between takes an argument
  it does not use, and the one place someone forgets is the place the trace breaks.
* **The run id is returned, not just recorded.** A reviewer's correction has to attach to
  the run that produced the verdict, so the id travels back out on ``LlmUsage`` and on
  ``RuleResult`` and is stored in Postgres alongside the verdict.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from specguard.config import Settings
from specguard.llm.protocol import LLMClient, LLMResult
from specguard.models.rule import RuleId
from specguard.prompts.loader import Prompt

#: The rule whose evaluation is currently on the stack, if any. Set by ``rule_span``.
_rule_id: ContextVar[str | None] = ContextVar("specguard_rule_id", default=None)

#: Truthy values for LANGSMITH_TRACING, matching what langsmith itself accepts.
_TRUE = frozenset({"1", "true", "yes", "on"})

#: The span kinds LangSmith renders differently. Narrowed to the two this project opens.
type RunType = Literal["chain", "llm"]


def tracing_enabled() -> bool:
    """Whether spans should be sent.

    Read from the environment rather than held in a module variable because the API and
    the worker are different processes, and a flag set in one would say nothing about
    the other.
    """
    return os.environ.get("LANGSMITH_TRACING", "").strip().lower() in _TRUE


def configure_tracing(settings: Settings) -> bool:
    """Publish the LangSmith environment from settings. Returns whether tracing is on.

    Called once at process start. An API key is required: tracing that is switched on
    without one fails on every span instead of once, here.
    """
    if not settings.langsmith_tracing:
        os.environ["LANGSMITH_TRACING"] = "false"
        return False
    if not settings.langsmith_api_key:
        os.environ["LANGSMITH_TRACING"] = "false"
        return False

    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    return True


def current_rule_id() -> str | None:
    """The rule currently being evaluated, for anything that wants to tag itself."""
    return _rule_id.get()


@dataclass
class Span:
    """A handle on one span. Every method is a no-op when tracing is off."""

    run_id: str | None
    _run: Any = None
    #: Whether closing this handle should close the run. False when the run belongs to
    #: someone else — LangGraph's own node span, which we annotate but must not end.
    _owned: bool = True

    def tag(self, **values: Any) -> None:
        """Attach metadata to this span."""
        if self._run is not None:
            self._run.add_metadata(dict(values))

    def finish(self, **outputs: Any) -> None:
        """Close the span with its outputs. Safe to call more than once."""
        if self._run is not None and self._owned:
            self._run.end(outputs=outputs)
            self._run = None


@contextmanager
def span(
    name: str,
    *,
    run_type: RunType = "chain",
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Iterator[Span]:
    """Open one span, or nothing at all when tracing is off.

    The import of langsmith lives inside the branch so that a run with tracing disabled
    neither imports the client nor constructs one.
    """
    if not tracing_enabled():
        yield Span(run_id=None)
        return

    from langsmith.run_helpers import trace

    with trace(
        name=name, run_type=run_type, inputs=inputs or {}, metadata=metadata, tags=tags
    ) as run:
        handle = Span(run_id=str(run.id), _run=run)
        try:
            yield handle
        finally:
            handle.finish()


@contextmanager
def node_span(name: str, metadata: dict[str, Any]) -> Iterator[Span]:
    """Trace one graph node, without duplicating the span LangGraph already opened.

    LangGraph traces each node itself when tracing is on, but with none of the context
    that makes a span worth opening — no job id, no correlation id, no graph version.
    Wrapping it in a second span of our own would put every node in the tree twice, so
    the existing run is annotated instead. Outside a graph — a node called directly, in
    a test — there is nothing to annotate and a span is opened as normal.
    """
    if not tracing_enabled():
        yield Span(run_id=None)
        return

    from langsmith.run_helpers import get_current_run_tree

    current = get_current_run_tree()
    if current is None:
        with span(name, metadata=metadata, tags=[name]) as handle:
            yield handle
        return

    current.add_metadata(metadata)
    yield Span(run_id=str(current.id), _run=current, _owned=False)


@contextmanager
def rule_span(rule_id: RuleId) -> Iterator[Span]:
    """Span covering one rule's whole evaluation, and the scope its model calls inherit.

    The run id of this span is what a reviewer's correction attaches to: it is the run
    that produced the verdict, whether that verdict came from a model or from arithmetic.
    """
    token = _rule_id.set(rule_id.value)
    try:
        with span(
            f"rule:{rule_id.value}",
            run_type="chain",
            metadata={"rule_id": rule_id.value},
            tags=[f"rule:{rule_id.value}"],
        ) as handle:
            yield handle
    finally:
        _rule_id.reset(token)


class TracedClient:
    """An ``LLMClient`` that traces every call it forwards.

    Wraps rather than subclasses, so it works for every provider including the fake one,
    and so the tracing concern stays out of each provider implementation.
    """

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.provider = inner.provider
        self.model = inner.model

    def generate[T: BaseModel](
        self,
        *,
        prompt: Prompt,
        schema: type[T],
        document: str,
        cache_key: str,
    ) -> LLMResult[T]:
        """Forward the call inside a span carrying everything #5 requires."""
        rule_id = current_rule_id()
        started = time.perf_counter()

        with span(
            f"llm:{prompt.name}",
            run_type="llm",
            # The document is untrusted supplier text and is already labelled as data by
            # the provider. It is not put in the trace: a span is a place a whole PDF
            # would be retained indefinitely, and the cache key identifies it exactly.
            inputs={"prompt": prompt.name, "cache_key": cache_key},
            metadata={
                "rule_id": rule_id,
                "prompt_version": prompt.version,
                "provider": self.provider,
                "model": self.model,
                "schema": schema.__name__,
            },
            tags=[t for t in (f"prompt:{prompt.version}", rule_id and f"rule:{rule_id}") if t],
        ) as handle:
            result = self._inner.generate(
                prompt=prompt, schema=schema, document=document, cache_key=cache_key
            )
            latency_ms = result.usage.latency_ms or int((time.perf_counter() - started) * 1000)
            handle.tag(
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cost_usd=result.usage.cost_usd,
                latency_ms=latency_ms,
            )

        usage = result.usage.model_copy(
            update={"langsmith_run_id": handle.run_id, "latency_ms": latency_ms}
        )
        return LLMResult(value=result.value, usage=usage)


def record_feedback(
    run_id: str,
    *,
    corrected_verdict: str,
    original_verdict: str | None = None,
    comment: str | None = None,
    reviewer: str | None = None,
) -> str | None:
    """Attach a reviewer's correction to the run that produced the verdict.

    Returns the LangSmith feedback id, or None if tracing is off or the push failed.
    Failure is deliberately not raised: the correction is already committed to Postgres
    by the time this is called, and losing a reviewer's answer because an observability
    vendor was unreachable would be the wrong trade every time.

    The score is the agreement, not the verdict — 1.0 where the reviewer confirmed what
    the system said, 0.0 where they overturned it — so a LangSmith chart of this key
    reads as accuracy against human judgement.
    """
    if not tracing_enabled():
        return None

    try:
        from langsmith import Client

        feedback = Client().create_feedback(
            run_id=run_id,
            key="human_verdict",
            score=None
            if original_verdict is None
            else float(corrected_verdict == original_verdict),
            value=corrected_verdict,
            comment=comment,
            correction={"verdict": corrected_verdict},
            source_info={"reviewer": reviewer} if reviewer else None,
        )
    except Exception:
        return None
    return str(feedback.id)
