"""Graph assembly: parse → extract → plan → check → verify → aggregate.

Nodes are plain functions over state, bound to their dependencies here. Keeping the
wiring in one place means the sequence is readable at a glance, and each node stays
testable on its own without constructing a graph at all.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise
from typing import Any

from langgraph.graph import END, START, StateGraph

from specguard.graph.nodes import (
    Dependencies,
    aggregate,
    check,
    extract,
    parse,
    plan,
    verify,
)
from specguard.graph.state import CheckState
from specguard.tracing import span

#: In order. The names are the trace span names, so they match the milestone's language
#: rather than the function names they happen to be implemented by.
NODES: tuple[tuple[str, Callable[[CheckState, Dependencies], CheckState]], ...] = (
    ("parse", parse),
    ("extract", extract),
    ("plan", plan),
    ("check", check),
    ("verify", verify),
    ("aggregate", aggregate),
)


def _bind(
    node: Callable[[CheckState, Dependencies], CheckState], deps: Dependencies
) -> Callable[[CheckState], dict[str, Any]]:
    """Bind a node to its dependencies, giving the graph the single-argument shape it wants.

    The return is widened to a plain dict: LangGraph merges whatever mapping a node
    returns into the state, and typing it as the full CheckState would claim each node
    returns every key when in fact each writes only its own.
    """

    def run(state: CheckState) -> dict[str, Any]:
        with span(
            f"node:{node.__name__}",
            metadata={
                "node": node.__name__,
                "job_id": state.get("job_id"),
                "correlation_id": state.get("correlation_id"),
                "graph_version": deps.settings.graph_version,
            },
            tags=[f"node:{node.__name__}"],
        ) as traced:
            written = dict(node(state, deps))
            # The keys a node wrote, not their values: the values are a whole document
            # and eight rule results, and a span is not the place to store either.
            traced.finish(wrote=sorted(written))
        return written

    run.__name__ = node.__name__
    return run


def build_graph(deps: Dependencies) -> Any:
    """Compile the check graph with its dependencies bound."""
    builder: StateGraph[CheckState, None, CheckState, CheckState] = StateGraph(CheckState)

    for name, node in NODES:
        # LangGraph's add_node overloads do not accept a Callable whose parameter is a
        # total=False TypedDict, though that is exactly what a node takes at runtime.
        # An SDK typing limitation rather than a defect here.
        builder.add_node(name, _bind(node, deps))  # type: ignore[call-overload]

    builder.add_edge(START, NODES[0][0])
    for (name, _), (next_name, _) in pairwise(NODES):
        builder.add_edge(name, next_name)
    builder.add_edge(NODES[-1][0], END)

    return builder.compile()


def run_check(deps: Dependencies, initial: CheckState) -> CheckState:
    """Run one document through the graph."""
    result: CheckState = build_graph(deps).invoke(initial)
    return result
