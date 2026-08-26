"""trace ids on results and feedback

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-26

A verdict and the run that produced it are stored together so a correction made months
later still has something to attach to. Nullable on both sides: rows written before
tracing existed have no run, and a correction recorded while LangSmith is unreachable is
still a correction worth keeping.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("results", sa.Column("langsmith_run_id", sa.String(64), nullable=True))
    op.add_column("feedback", sa.Column("langsmith_run_id", sa.String(64), nullable=True))
    op.add_column("feedback", sa.Column("langsmith_feedback_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("feedback", "langsmith_feedback_id")
    op.drop_column("feedback", "langsmith_run_id")
    op.drop_column("results", "langsmith_run_id")
