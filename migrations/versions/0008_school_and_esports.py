"""school schedule, homework and esports

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-13 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Which class the student is in -- picks their schedule row and their
    # homework. NULL for students who never set one (invite-only accounts).
    with op.batch_alter_table('students', schema=None) as batch_op:
        batch_op.add_column(sa.Column('class_name', sa.Text(), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_students_class_name'), ['class_name'], unique=False
        )

    op.create_table(
        'schedule_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('class_name', sa.Text(), nullable=False),
        sa.Column('day_of_week', sa.Integer(), nullable=False),
        sa.Column('lesson_no', sa.Integer(), nullable=False),
        sa.Column('subject', sa.Text(), nullable=False),
        sa.Column('room', sa.Text(), nullable=True),
        sa.Column('starts_at', sa.Text(), nullable=False),
        sa.Column('ends_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_schedule_entries_class_name'),
        'schedule_entries',
        ['class_name'],
        unique=False,
    )
    # One lesson slot per class per day, enforced by the database: re-running
    # the seeder must update the row rather than add a second lesson 3.
    op.create_index(
        'uq_schedule_class_day_lesson',
        'schedule_entries',
        ['class_name', 'day_of_week', 'lesson_no'],
        unique=True,
    )

    op.create_table(
        'homework',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('class_name', sa.Text(), nullable=False),
        sa.Column('subject', sa.Text(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_homework_class_name'), 'homework', ['class_name'], unique=False
    )
    op.create_index(op.f('ix_homework_due_date'), 'homework', ['due_date'], unique=False)
    op.create_index('ix_homework_class_due', 'homework', ['class_name', 'due_date'])

    op.create_table(
        'lesson_notes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('class_name', sa.Text(), nullable=False),
        sa.Column('on_date', sa.Date(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_lesson_notes_class_name'), 'lesson_notes', ['class_name'], unique=False
    )
    op.create_index(
        op.f('ix_lesson_notes_on_date'), 'lesson_notes', ['on_date'], unique=False
    )

    op.create_table(
        'tournaments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('game', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('format', sa.Text(), nullable=True),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_tournaments_game'), 'tournaments', ['game'], unique=False)
    op.create_index(
        op.f('ix_tournaments_starts_at'), 'tournaments', ['starts_at'], unique=False
    )
    op.create_index(
        op.f('ix_tournaments_status'), 'tournaments', ['status'], unique=False
    )

    op.create_table(
        'student_badges',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('tournament_id', sa.Integer(), nullable=False),
        sa.Column('code', sa.Text(), nullable=False),
        sa.Column(
            'awarded_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['student_id'], ['students.id']),
        sa.ForeignKeyConstraint(['tournament_id'], ['tournaments.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'student_id', 'tournament_id', 'code', name='uq_student_badges_unique'
        ),
    )
    op.create_index(
        op.f('ix_student_badges_student_id'),
        'student_badges',
        ['student_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_student_badges_tournament_id'),
        'student_badges',
        ['tournament_id'],
        unique=False,
    )
    op.create_index(
        'ix_student_badges_student', 'student_badges', ['student_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_student_badges_student', table_name='student_badges')
    op.drop_index(
        op.f('ix_student_badges_tournament_id'), table_name='student_badges'
    )
    op.drop_index(op.f('ix_student_badges_student_id'), table_name='student_badges')
    op.drop_table('student_badges')
    op.drop_index(op.f('ix_tournaments_status'), table_name='tournaments')
    op.drop_index(op.f('ix_tournaments_starts_at'), table_name='tournaments')
    op.drop_index(op.f('ix_tournaments_game'), table_name='tournaments')
    op.drop_table('tournaments')
    op.drop_index(op.f('ix_lesson_notes_on_date'), table_name='lesson_notes')
    op.drop_index(op.f('ix_lesson_notes_class_name'), table_name='lesson_notes')
    op.drop_table('lesson_notes')
    op.drop_index('ix_homework_class_due', table_name='homework')
    op.drop_index(op.f('ix_homework_due_date'), table_name='homework')
    op.drop_index(op.f('ix_homework_class_name'), table_name='homework')
    op.drop_table('homework')
    op.drop_index('uq_schedule_class_day_lesson', table_name='schedule_entries')
    op.drop_index(
        op.f('ix_schedule_entries_class_name'), table_name='schedule_entries'
    )
    op.drop_table('schedule_entries')
    with op.batch_alter_table('students', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_students_class_name'))
        batch_op.drop_column('class_name')
