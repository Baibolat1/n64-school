"""comment likes

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-13 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A second table rather than a nullable ``post_id``/``comment_id`` pair on
    # ``post_likes``: two real foreign keys let the database refuse a like that
    # points at a comment nobody wrote, which a pair of nullable columns cannot
    # (see app.models.like).
    op.create_table(
        'comment_likes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('comment_id', sa.Integer(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['comment_id'], ['comments.id']),
        sa.ForeignKeyConstraint(['student_id'], ['students.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'student_id', 'comment_id', name='uq_comment_likes_student_comment'
        ),
    )
    op.create_index(
        op.f('ix_comment_likes_student_id'), 'comment_likes', ['student_id'], unique=False
    )
    op.create_index(
        op.f('ix_comment_likes_comment_id'), 'comment_likes', ['comment_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_comment_likes_comment_id'), table_name='comment_likes')
    op.drop_index(op.f('ix_comment_likes_student_id'), table_name='comment_likes')
    op.drop_table('comment_likes')
