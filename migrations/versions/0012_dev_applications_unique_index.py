"""dev applications: one unique index on student_id

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Migration 0009 wrote the "one application per student" rule twice: as a
    # UNIQUE constraint and as a plain index on the same column. The model asks
    # for ``unique=True, index=True``, which is one *unique index* -- hence the
    # drift ``alembic check`` reported on a database built from scratch.
    #
    # SQLite cannot drop a constraint in place, so the constraint goes through
    # batch mode, which rebuilds the table. The indexes stay outside the batch:
    # there SQLite handles them directly, and the rebuild does not have to
    # reflect them back (a reflected index would be dropped again with the old
    # table).
    op.drop_index(
        op.f('ix_dev_applications_student_id'), table_name='dev_applications'
    )
    with op.batch_alter_table('dev_applications', schema=None) as batch_op:
        batch_op.drop_constraint('uq_dev_applications_student', type_='unique')
    op.create_index(
        op.f('ix_dev_applications_student_id'),
        'dev_applications',
        ['student_id'],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Back to what 0009 declared, constraint and plain index both.
    op.drop_index(
        op.f('ix_dev_applications_student_id'), table_name='dev_applications'
    )
    with op.batch_alter_table('dev_applications', schema=None) as batch_op:
        batch_op.create_unique_constraint(
            'uq_dev_applications_student', ['student_id']
        )
    op.create_index(
        op.f('ix_dev_applications_student_id'),
        'dev_applications',
        ['student_id'],
        unique=False,
    )
