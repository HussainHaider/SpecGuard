"""Structured JSON logging with a correlation id threaded through every line.

The id is held in a ContextVar rather than passed as an argument. Passing it would mean
every function between the request handler and the model call takes a parameter it does
not use, and the one place someone forgets to thread it is the place the trace breaks.
"""

from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def new_correlation_id() -> str:
    """A fresh id for one request or one job."""
    return uuid.uuid4().hex[:16]


def bind_correlation_id(value: str | None = None) -> str:
    """Set the id for this context, returning it."""
    resolved = value or new_correlation_id()
    _correlation_id.set(resolved)
    return resolved


def current_correlation_id() -> str | None:
    """The id for this context, if one is bound."""
    return _correlation_id.get()


def _add_correlation_id(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    correlation_id = _correlation_id.get()
    if correlation_id is not None:
        event_dict["correlation_id"] = correlation_id
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure structlog once, at process start."""
    renderer: Any = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_correlation_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """A bound logger."""
    return structlog.get_logger(name)
