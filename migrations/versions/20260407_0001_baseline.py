"""baseline schema checkpoint

Revision ID: 20260407_0001
Revises:
Create Date: 2026-04-07 00:00:00
"""

from __future__ import annotations

from alembic import op

from app.models import Base

revision = "20260407_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Bootstrap the current application schema under Alembic management.
    # Future revisions should use explicit Alembic operations for incremental changes.
    bind = op.get_bind()
    Base.metadata.create_all(bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind)
