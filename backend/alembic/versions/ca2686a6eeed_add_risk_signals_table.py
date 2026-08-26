"""add_risk_signals_table

Revision ID: ca2686a6eeed
Revises: 5f02467da51f
Create Date: 2026-08-26 23:01:59.310615

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ca2686a6eeed'
down_revision: Union[str, Sequence[str], None] = '5f02467da51f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('risk_signals',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('investigation_id', sa.Uuid(), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=False),
    sa.Column('code', sa.String(length=100), nullable=False),
    sa.Column('severity', sa.String(length=50), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('risk_weight', sa.Integer(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('evidence_ids', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['investigation_id'], ['investigations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('risk_signals')
