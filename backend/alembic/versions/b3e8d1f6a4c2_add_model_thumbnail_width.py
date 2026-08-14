"""add configurable Model thumbnail width

Revision ID: b3e8d1f6a4c2
Revises: a1d7c9e4f2b6
Create Date: 2026-08-14 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b3e8d1f6a4c2"
down_revision: str | None = "a1d7c9e4f2b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("system_config") as batch:
        batch.add_column(
            sa.Column("model_thumbnail_width", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("system_config") as batch:
        batch.drop_column("model_thumbnail_width")
