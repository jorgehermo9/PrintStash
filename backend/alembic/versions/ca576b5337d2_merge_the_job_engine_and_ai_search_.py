"""merge the job engine and AI search histories

Revision ID: ca576b5337d2
Revises: 594452a753fe, 6f27f2e6090a
Create Date: 2026-09-24 17:31:57.984926

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ca576b5337d2'
down_revision: Union[str, Sequence[str], None] = ('594452a753fe', '6f27f2e6090a')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
