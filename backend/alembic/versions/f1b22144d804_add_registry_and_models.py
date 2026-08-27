"""add_registry_and_models

Revision ID: f1b22144d804
Revises: f1b22144d803
Create Date: 2026-08-28 00:32:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1b22144d804'
down_revision: Union[str, Sequence[str], None] = 'f1b22144d803'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Create entities table
    op.create_table(
        'entities',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('canonical_name', sa.String(length=255), nullable=False),
        sa.Column('trade_name', sa.String(length=255), nullable=True),
        sa.Column('gstin', sa.String(length=15), nullable=True),
        sa.Column('cin', sa.String(length=21), nullable=True),
        sa.Column('epfo_code', sa.String(length=100), nullable=True),
        sa.Column('website', sa.String(length=500), nullable=True),
        sa.Column('registered_address', sa.Text(), nullable=True),
        sa.Column('state', sa.String(length=100), nullable=True),
        sa.Column('business_activity', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # 2. Add foreign key link from investigations.resolved_entity_id to entities.id
    with op.batch_alter_table('investigations', schema=None) as batch_op:
        batch_op.create_foreign_key('fk_investigations_resolved_entity_id', 'entities', ['resolved_entity_id'], ['id'], ondelete='SET NULL')

    # 3. Create candidate_entities table
    op.create_table(
        'candidate_entities',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('investigation_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('gstin', sa.String(length=15), nullable=True),
        sa.Column('cin', sa.String(length=21), nullable=True),
        sa.Column('website', sa.String(length=500), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(['investigation_id'], ['investigations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # 4. Create browser_sessions table
    op.create_table(
        'browser_sessions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('investigation_id', sa.Uuid(), nullable=False),
        sa.Column('task_id', sa.String(length=100), nullable=False),
        sa.Column('domain', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('action_count', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['investigation_id'], ['investigations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # 5. Create browser_artifacts table
    op.create_table(
        'browser_artifacts',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('investigation_id', sa.Uuid(), nullable=False),
        sa.Column('task_id', sa.String(length=100), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('storage_location', sa.Text(), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['investigation_id'], ['investigations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # 6. Create source_registry table
    op.create_table(
        'source_registry',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('type', sa.String(length=50), nullable=False),
        sa.Column('domain', sa.String(length=255), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('config_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', 'type', name='uq_source_name_type')
    )
    with op.batch_alter_table('source_registry', schema=None) as batch_op:
        batch_op.create_index('ix_source_registry_name', ['name'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('source_registry', schema=None) as batch_op:
        batch_op.drop_index('ix_source_registry_name')

    op.drop_table('source_registry')
    op.drop_table('browser_artifacts')
    op.drop_table('browser_sessions')
    op.drop_table('candidate_entities')

    with op.batch_alter_table('investigations', schema=None) as batch_op:
        batch_op.drop_constraint('fk_investigations_resolved_entity_id', type_='foreignkey')

    op.drop_table('entities')
