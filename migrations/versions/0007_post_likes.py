"""post likes

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-13 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # One row per (student, post); the unique constraint is what keeps a
    # double click from producing two likes (see app.models.like).
    op.create_table(
        'post_likes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id']),
        sa.ForeignKeyConstraint(['student_id'], ['students.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'student_id', 'post_id', name='uq_post_likes_student_post'
        ),
    )
    op.create_index(
        op.f('ix_post_likes_student_id'), 'post_likes', ['student_id'], unique=False
    )
    op.create_index(
        op.f('ix_post_likes_post_id'), 'post_likes', ['post_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_post_likes_post_id'), table_name='post_likes')
    op.drop_index(op.f('ix_post_likes_student_id'), table_name='post_likes')
    op.drop_table('post_likes')
