"""add_user_id_index_to_investigations

Revision ID: f1b22144d805
Revises: f1b22144d804
Create Date: 2026-08-28 01:27:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1b22144d805'
down_revision: Union[str, Sequence[str], None] = 'f1b22144d804'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index('ix_investigations_user_id', 'investigations', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_investigations_user_id', table_name='investigations')
