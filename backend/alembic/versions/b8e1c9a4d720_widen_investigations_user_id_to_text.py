"""widen investigations.user_id to Text

Bearer-token-derived user identifiers are opaque and can exceed 100 characters.
The original VARCHAR(100) column caused StringDataRightTruncation on
investigation creation. Widen it to an unbounded text type.

Revision ID: b8e1c9a4d720
Revises: 11a09897a203
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8e1c9a4d720'
down_revision: Union[str, Sequence[str], None] = '11a09897a203'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Widen investigations.user_id from VARCHAR(100) to TEXT.

    On PostgreSQL this is an in-place widening cast with no data loss; the
    existing ix_investigations_user_id index is rebuilt automatically.
    """
    op.alter_column(
        "investigations",
        "user_id",
        existing_type=sa.String(length=100),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Narrow back to VARCHAR(100). Fails if any stored value exceeds 100 chars."""
    op.alter_column(
        "investigations",
        "user_id",
        existing_type=sa.Text(),
        type_=sa.String(length=100),
        existing_nullable=True,
        postgresql_using="user_id::varchar(100)",
    )
