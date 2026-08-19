"""add positive-proof storage ownership ledger

Revision ID: e4b7c2d9a6f1
Revises: d8f5b2c9a1e7
Create Date: 2026-08-12 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e4b7c2d9a6f1"
down_revision: str | None = "d8f5b2c9a1e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owned_storage_objects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("backend", sa.String(length=32), nullable=False),
        sa.Column("namespace", sa.String(length=1024), nullable=False),
        sa.Column("key", sa.String(length=2048), nullable=False),
        sa.Column("object_kind", sa.String(length=64), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("etag", sa.String(length=255), nullable=True),
        sa.Column("device", sa.BigInteger(), nullable=True),
        sa.Column("inode", sa.BigInteger(), nullable=True),
        sa.Column("ctime_ns", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "backend", "namespace", "key", name="uq_owned_storage_locator"
        ),
    )
    op.create_index(
        op.f("ix_owned_storage_objects_backend"),
        "owned_storage_objects",
        ["backend"],
        unique=False,
    )
    op.create_index(
        op.f("ix_owned_storage_objects_namespace"),
        "owned_storage_objects",
        ["namespace"],
        unique=False,
    )
    op.create_index(
        op.f("ix_owned_storage_objects_object_kind"),
        "owned_storage_objects",
        ["object_kind"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_owned_storage_objects_object_kind"),
        table_name="owned_storage_objects",
    )
    op.drop_index(
        op.f("ix_owned_storage_objects_namespace"),
        table_name="owned_storage_objects",
    )
    op.drop_index(
        op.f("ix_owned_storage_objects_backend"),
        table_name="owned_storage_objects",
    )
    op.drop_table("owned_storage_objects")
