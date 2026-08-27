"""add_investigation_persistence_fields

Revision ID: f1b22144d803
Revises: f1b22144d802
Create Date: 2026-08-28 00:23:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1b22144d803'
down_revision: Union[str, Sequence[str], None] = 'f1b22144d802'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('investigations', sa.Column('user_id', sa.String(length=100), nullable=True))
    op.add_column('investigations', sa.Column('raw_input', sa.Text(), nullable=True))
    op.add_column('investigations', sa.Column('normalized_input', sa.Text(), nullable=True))
    op.add_column('investigations', sa.Column('current_graph_node', sa.String(length=100), nullable=True))
    op.add_column('investigations', sa.Column('completed_timestamp', sa.DateTime(timezone=True), nullable=True))
    op.add_column('investigations', sa.Column('persistent_graph_state', sa.Text(), nullable=True))
    op.drop_column('investigations', 'completed_at')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('investigations', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    op.drop_column('investigations', 'persistent_graph_state')
    op.drop_column('investigations', 'completed_timestamp')
    op.drop_column('investigations', 'current_graph_node')
    op.drop_column('investigations', 'normalized_input')
    op.drop_column('investigations', 'raw_input')
    op.drop_column('investigations', 'user_id')
