"""merge the job engine and family removal histories

Revision ID: c46bd16d3080
Revises: 0b36c56fb17d, 0fee449176a0
Create Date: 2026-09-25 11:56:36.660071

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c46bd16d3080'
down_revision: Union[str, Sequence[str], None] = ('0b36c56fb17d', '0fee449176a0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
