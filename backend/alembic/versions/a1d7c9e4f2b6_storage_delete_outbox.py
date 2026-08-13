"""add storage deletion outbox and purge claims

Revision ID: a1d7c9e4f2b6
Revises: e4b7c2d9a6f1
Create Date: 2026-08-13 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a1d7c9e4f2b6"
down_revision: str | None = "e4b7c2d9a6f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("owned_storage_objects") as batch:
        batch.add_column(sa.Column("version_id", sa.String(length=1024), nullable=True))
    op.create_table(
        "storage_delete_intents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("backend", sa.String(length=32), nullable=False),
        sa.Column("namespace", sa.String(length=1024), nullable=False),
        sa.Column("key", sa.String(length=2048), nullable=False),
        sa.Column("object_kind", sa.String(length=64), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("etag", sa.String(length=255), nullable=True),
        sa.Column("version_id", sa.String(length=1024), nullable=True),
        sa.Column("device", sa.BigInteger(), nullable=True),
        sa.Column("inode", sa.BigInteger(), nullable=True),
        sa.Column("ctime_ns", sa.BigInteger(), nullable=True),
        sa.Column("resource_kind", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "backend",
            "namespace",
            "key",
            "token",
            name="uq_storage_delete_intent_receipt",
        ),
    )
    for column in (
        "backend",
        "object_kind",
        "resource_kind",
        "resource_id",
        "status",
        "next_attempt_at",
        "created_at",
    ):
        op.create_index(
            op.f(f"ix_storage_delete_intents_{column}"),
            "storage_delete_intents",
            [column],
            unique=False,
        )
    for table in ("models", "files", "documents", "collections"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("purge_token", sa.String(length=64), nullable=True)
            )
            batch.create_index(
                op.f(f"ix_{table}_purge_token"), ["purge_token"], unique=False
            )
    with op.batch_alter_table("files") as batch:
        batch.add_column(
            sa.Column("ingestion_key", sa.String(length=64), nullable=True)
        )
        batch.add_column(
            sa.Column("thumbnail_path", sa.String(length=2048), nullable=True)
        )
        batch.create_index("ix_files_ingestion_key", ["ingestion_key"], unique=True)
    with op.batch_alter_table("background_jobs") as batch:
        batch.add_column(sa.Column("claim_token", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
        batch.add_column(
            sa.Column("attempts", sa.Integer(), server_default="0", nullable=False)
        )
        batch.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
        batch.create_index(
            "ix_background_jobs_claim_token", ["claim_token"], unique=False
        )
        batch.create_index(
            "ix_background_jobs_lease_expires_at", ["lease_expires_at"], unique=False
        )
        batch.create_index(
            "ix_background_jobs_next_attempt_at", ["next_attempt_at"], unique=False
        )
    op.create_table(
        "staging_leases",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("background_job_id", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("device", sa.BigInteger(), nullable=True),
        sa.Column("inode", sa.BigInteger(), nullable=True),
        sa.Column("ctime_ns", sa.BigInteger(), nullable=True),
        sa.Column("destination_key", sa.String(length=2048), nullable=True),
        sa.Column("receipt_json", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["background_job_id"], ["background_jobs.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("background_job_id"),
        sa.UniqueConstraint("path"),
    )
    for column in (
        "owner_user_id",
        "background_job_id",
        "sha256",
        "expires_at",
        "created_at",
    ):
        op.create_index(
            op.f(f"ix_staging_leases_{column}"),
            "staging_leases",
            [column],
            unique=False,
        )
    with op.batch_alter_table("external_libraries") as batch:
        batch.add_column(
            sa.Column("scan_claim_token", sa.String(length=64), nullable=True)
        )
        batch.add_column(
            sa.Column("scan_claim_expires_at", sa.DateTime(), nullable=True)
        )
        batch.add_column(sa.Column("scan_job_id", sa.String(length=64), nullable=True))
        batch.create_index(
            "ix_external_libraries_scan_claim_token", ["scan_claim_token"], unique=False
        )
        batch.create_index(
            "ix_external_libraries_scan_claim_expires_at",
            ["scan_claim_expires_at"],
            unique=False,
        )
        batch.create_index(
            "ix_external_libraries_scan_job_id", ["scan_job_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("external_libraries") as batch:
        batch.drop_index("ix_external_libraries_scan_job_id")
        batch.drop_index("ix_external_libraries_scan_claim_expires_at")
        batch.drop_index("ix_external_libraries_scan_claim_token")
        batch.drop_column("scan_job_id")
        batch.drop_column("scan_claim_expires_at")
        batch.drop_column("scan_claim_token")
    op.drop_table("staging_leases")
    with op.batch_alter_table("background_jobs") as batch:
        batch.drop_index("ix_background_jobs_next_attempt_at")
        batch.drop_index("ix_background_jobs_lease_expires_at")
        batch.drop_index("ix_background_jobs_claim_token")
        batch.drop_column("next_attempt_at")
        batch.drop_column("attempts")
        batch.drop_column("lease_expires_at")
        batch.drop_column("claim_token")
    with op.batch_alter_table("files") as batch:
        batch.drop_index("ix_files_ingestion_key")
        batch.drop_column("thumbnail_path")
        batch.drop_column("ingestion_key")
    for table in ("collections", "documents", "files", "models"):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(op.f(f"ix_{table}_purge_token"))
            batch.drop_column("purge_token")
    op.drop_table("storage_delete_intents")
    with op.batch_alter_table("owned_storage_objects") as batch:
        batch.drop_column("version_id")
