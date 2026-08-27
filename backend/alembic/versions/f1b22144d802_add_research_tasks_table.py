"""add_research_tasks_table

Revision ID: f1b22144d802
Revises: e007196eb7e9
Create Date: 2026-08-27T19:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1b22144d802'
down_revision: Union[str, Sequence[str], None] = 'e007196eb7e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Create research_tasks table
    op.create_table('research_tasks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('task_id', sa.String(length=100), nullable=False),
    sa.Column('investigation_id', sa.Uuid(), nullable=False),
    sa.Column('task_type', sa.String(length=100), nullable=False),
    sa.Column('target', sa.String(length=500), nullable=False),
    sa.Column('objective', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=50), nullable=False, server_default='PENDING'),
    sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('result_info', sa.Text(), nullable=True),
    sa.Column('error_info', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['investigation_id'], ['investigations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_research_tasks_investigation_id'), 'research_tasks', ['investigation_id'], unique=False)

    # 2. Add columns to evidences table
    op.add_column('evidences', sa.Column('verification_status', sa.String(length=50), nullable=True, server_default='UNVERIFIED'))
    op.add_column('evidences', sa.Column('research_task_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_evidences_research_task_id_research_tasks', 'evidences', 'research_tasks', ['research_task_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_evidences_research_task_id_research_tasks', 'evidences', type_='foreignkey')
    op.drop_column('evidences', 'research_task_id')
    op.drop_column('evidences', 'verification_status')
    op.drop_index(op.f('ix_research_tasks_investigation_id'), table_name='research_tasks')
    op.drop_table('research_tasks')
