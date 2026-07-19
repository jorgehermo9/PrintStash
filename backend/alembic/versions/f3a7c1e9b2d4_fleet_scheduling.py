"""add fleet scheduling and persistent background jobs

Revision ID: f3a7c1e9b2d4
Revises: e2d4f6a8b0c1
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a7c1e9b2d4"
down_revision: str | None = "e2d4f6a8b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("printers") as batch_op:
        batch_op.add_column(
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("drain_mode", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("drain_reason", sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column("drain_updated_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_printers_is_default", ["is_default"])
        batch_op.create_index("ix_printers_drain_mode", ["drain_mode"])

    with op.batch_alter_table("print_jobs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "routing_strategy",
                sa.String(length=16),
                nullable=False,
                server_default="MANUAL",
            )
        )
        batch_op.add_column(
            sa.Column("queue_position", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("provider_job_id", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("blocked_reason", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("dispatch_claimed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("requested_by", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_print_jobs_requested_by_users", "users", ["requested_by"], ["id"]
        )
        batch_op.create_index("ix_print_jobs_routing_strategy", ["routing_strategy"])
        batch_op.create_index("ix_print_jobs_queue_position", ["queue_position"])
        batch_op.create_index("ix_print_jobs_provider_job_id", ["provider_job_id"])
        batch_op.create_index("ix_print_jobs_dispatch_claimed_at", ["dispatch_claimed_at"])
        batch_op.create_index("ix_print_jobs_requested_by", ["requested_by"])
        batch_op.create_index("ix_print_jobs_retryable", ["retryable"])
        batch_op.create_index(
            "ix_print_jobs_state_queue_position", ["state", "queue_position"]
        )
        batch_op.create_index(
            "ix_print_jobs_printer_state", ["printer_id", "state"]
        )
        batch_op.create_unique_constraint(
            "uq_print_jobs_printer_provider_job", ["printer_id", "provider_job_id"]
        )

    op.execute(
        "UPDATE print_jobs SET queue_position = id WHERE queue_position = 0"
    )

    op.create_table(
        "printer_maintenance_windows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("printer_id", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["printer_id"], ["printers.id"]),
        sa.ForeignKeyConstraint(["deleted_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_printer_maintenance_windows_printer_id", "printer_maintenance_windows", ["printer_id"])
    op.create_index("ix_printer_maintenance_windows_starts_at", "printer_maintenance_windows", ["starts_at"])
    op.create_index("ix_printer_maintenance_windows_ends_at", "printer_maintenance_windows", ["ends_at"])
    op.create_index("ix_printer_maintenance_windows_deleted_at", "printer_maintenance_windows", ["deleted_at"])

    op.create_table(
        "printer_maintenance_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("printer_id", sa.Integer(), nullable=False),
        sa.Column("performed_at", sa.DateTime(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=4096), nullable=False),
        sa.Column("counter_value", sa.Float(), nullable=True),
        sa.Column("counter_unit", sa.String(length=32), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["printer_id"], ["printers.id"]),
        sa.ForeignKeyConstraint(["deleted_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_printer_maintenance_logs_printer_id", "printer_maintenance_logs", ["printer_id"])
    op.create_index("ix_printer_maintenance_logs_performed_at", "printer_maintenance_logs", ["performed_at"])
    op.create_index("ix_printer_maintenance_logs_category", "printer_maintenance_logs", ["category"])
    op.create_index("ix_printer_maintenance_logs_deleted_at", "printer_maintenance_logs", ["deleted_at"])

    op.create_table(
        "background_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("visible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("kind", sa.String(length=64), nullable=False, server_default="generic"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("status_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("replay_safe", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "owner_user_id",
        "visible",
        "kind",
        "state",
        "replay_safe",
        "created_at",
        "updated_at",
        "finished_at",
    ):
        op.create_index(f"ix_background_jobs_{column}", "background_jobs", [column])


def downgrade() -> None:
    op.drop_table("background_jobs")
    op.drop_table("printer_maintenance_logs")
    op.drop_table("printer_maintenance_windows")
    with op.batch_alter_table("print_jobs") as batch_op:
        batch_op.drop_constraint("uq_print_jobs_printer_provider_job", type_="unique")
        batch_op.drop_index("ix_print_jobs_printer_state")
        batch_op.drop_index("ix_print_jobs_state_queue_position")
        batch_op.drop_index("ix_print_jobs_requested_by")
        batch_op.drop_index("ix_print_jobs_retryable")
        batch_op.drop_index("ix_print_jobs_dispatch_claimed_at")
        batch_op.drop_index("ix_print_jobs_provider_job_id")
        batch_op.drop_index("ix_print_jobs_queue_position")
        batch_op.drop_index("ix_print_jobs_routing_strategy")
        batch_op.drop_constraint("fk_print_jobs_requested_by_users", type_="foreignkey")
        batch_op.drop_column("requested_by")
        batch_op.drop_column("retryable")
        batch_op.drop_column("dispatch_attempts")
        batch_op.drop_column("dispatch_claimed_at")
        batch_op.drop_column("blocked_reason")
        batch_op.drop_column("provider_job_id")
        batch_op.drop_column("queue_position")
        batch_op.drop_column("routing_strategy")
    with op.batch_alter_table("printers") as batch_op:
        batch_op.drop_index("ix_printers_drain_mode")
        batch_op.drop_index("ix_printers_is_default")
        batch_op.drop_column("drain_updated_at")
        batch_op.drop_column("drain_reason")
        batch_op.drop_column("drain_mode")
        batch_op.drop_column("is_default")
