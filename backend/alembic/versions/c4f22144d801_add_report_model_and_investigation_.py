"""add_report_model_and_investigation_fields

Revision ID: c4f22144d801
Revises: ca2686a6eeed
Create Date: 2026-08-27 01:22:36.345353

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f22144d801'
down_revision: Union[str, Sequence[str], None] = 'ca2686a6eeed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add new columns to investigations table
    op.add_column('investigations', sa.Column('current_node', sa.String(length=100), nullable=True))
    op.add_column('investigations', sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('investigations', sa.Column('risk_score', sa.Integer(), nullable=True))
    op.add_column('investigations', sa.Column('risk_level', sa.String(length=50), nullable=True))
    op.add_column('investigations', sa.Column('resolved_entity_id', sa.Uuid(), nullable=True))
    op.add_column('investigations', sa.Column('entity_confidence', sa.Float(), nullable=True))
    op.add_column('investigations', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))

    # Create reports table
    op.create_table(
        'reports',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('investigation_id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('report_json', sa.Text(), nullable=False),
        sa.Column('qa_status', sa.String(length=50), server_default='PENDING', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['investigation_id'], ['investigations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop reports table
    op.drop_table('reports')

    # Drop columns from investigations table
    op.drop_column('investigations', 'completed_at')
    op.drop_column('investigations', 'entity_confidence')
    op.drop_column('investigations', 'resolved_entity_id')
    op.drop_column('investigations', 'risk_level')
    op.drop_column('investigations', 'risk_score')
    op.drop_column('investigations', 'retry_count')
    op.drop_column('investigations', 'current_node')
