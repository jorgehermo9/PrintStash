"""add oidc identities

Revision ID: a4c8e2f6b1d3
Revises: f3a7c1e9b2d4
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4c8e2f6b1d3"
down_revision: str | None = "f3a7c1e9b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("oidc_issuer", sa.String(length=512), nullable=True)
        )
        batch_op.add_column(
            sa.Column("oidc_subject", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "oidc_managed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_index("ix_users_oidc_issuer", ["oidc_issuer"])
        batch_op.create_index("ix_users_oidc_subject", ["oidc_subject"])
        batch_op.create_index("ix_users_oidc_managed", ["oidc_managed"])
        batch_op.create_unique_constraint(
            "uq_users_oidc_identity", ["oidc_issuer", "oidc_subject"]
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_oidc_identity", type_="unique")
        batch_op.drop_index("ix_users_oidc_managed")
        batch_op.drop_index("ix_users_oidc_subject")
        batch_op.drop_index("ix_users_oidc_issuer")
        batch_op.drop_column("oidc_managed")
        batch_op.drop_column("oidc_subject")
        batch_op.drop_column("oidc_issuer")
