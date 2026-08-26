"""add_evidences_table

Revision ID: 5f02467da51f
Revises: 6d597ddcf68c
Create Date: 2026-08-26 22:49:31.530444

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5f02467da51f'
down_revision: Union[str, Sequence[str], None] = '6d597ddcf68c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('evidences',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('investigation_id', sa.Uuid(), nullable=False),
    sa.Column('research_result_id', sa.String(length=100), nullable=False),
    sa.Column('task_id', sa.String(length=100), nullable=False),
    sa.Column('field_name', sa.String(length=100), nullable=False),
    sa.Column('field_value', sa.Text(), nullable=False),
    sa.Column('source_name', sa.String(length=200), nullable=False),
    sa.Column('source_url', sa.Text(), nullable=True),
    sa.Column('retrieved_timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('created_timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['investigation_id'], ['investigations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('evidences')
