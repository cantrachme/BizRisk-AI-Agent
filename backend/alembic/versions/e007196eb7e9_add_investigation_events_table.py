"""add_investigation_events_table

Revision ID: e007196eb7e9
Revises: ca2686a6eeed
Create Date: 2026-08-27 01:31:55.577395

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e007196eb7e9'
down_revision: Union[str, Sequence[str], None] = 'c4f22144d801'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('investigation_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('investigation_id', sa.Uuid(), nullable=False),
    sa.Column('event_type', sa.String(length=100), nullable=False),
    sa.Column('node', sa.String(length=100), nullable=False),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('metadata_json', sa.Text(), nullable=False, server_default='{}'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['investigation_id'], ['investigations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('investigation_events')
