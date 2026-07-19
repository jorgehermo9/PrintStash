"""add oidc runtime configuration

Revision ID: b5d9f3a7c2e4
Revises: a4c8e2f6b1d3
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5d9f3a7c2e4"
down_revision: str | None = "a4c8e2f6b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.add_column(sa.Column("oidc_enabled", sa.Boolean(), nullable=True))
        batch_op.add_column(
            sa.Column("oidc_issuer_url", sa.String(length=512), nullable=True)
        )
        batch_op.add_column(
            sa.Column("oidc_client_id", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(sa.Column("oidc_client_secret", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("oidc_scopes", sa.String(length=512), nullable=True)
        )
        batch_op.add_column(
            sa.Column("oidc_username_claim", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(
            sa.Column("oidc_groups_claim", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(
            sa.Column("oidc_admin_groups", sa.String(length=1024), nullable=True)
        )
        batch_op.add_column(
            sa.Column("oidc_display_name", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(
            sa.Column("oidc_redirect_uri", sa.String(length=1024), nullable=True)
        )
        batch_op.add_column(
            sa.Column("oidc_allow_insecure_http", sa.Boolean(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.drop_column("oidc_allow_insecure_http")
        batch_op.drop_column("oidc_redirect_uri")
        batch_op.drop_column("oidc_display_name")
        batch_op.drop_column("oidc_admin_groups")
        batch_op.drop_column("oidc_groups_claim")
        batch_op.drop_column("oidc_username_claim")
        batch_op.drop_column("oidc_scopes")
        batch_op.drop_column("oidc_client_secret")
        batch_op.drop_column("oidc_client_id")
        batch_op.drop_column("oidc_issuer_url")
        batch_op.drop_column("oidc_enabled")
