"""pii blocked attempts

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The fact of a refusal only: who, when, what they were publishing and which
    # hard rule fired. No fragment of the message is ever stored here.
    op.create_table(
        'pii_blocked_attempts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('target_type', sa.Text(), nullable=False),
        sa.Column('rule_codes', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['student_id'], ['students.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_pii_blocked_attempts_created_at',
        'pii_blocked_attempts',
        ['created_at'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_pii_blocked_attempts_created_at', table_name='pii_blocked_attempts')
    op.drop_table('pii_blocked_attempts')
