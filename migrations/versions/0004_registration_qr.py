"""registration qr

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Credentials for self-registered students. Invite-only students keep both
    # columns NULL; SQLite allows many NULLs in a unique index, so they do not
    # collide on ``email``.
    op.add_column('students', sa.Column('email', sa.Text(), nullable=True))
    op.add_column('students', sa.Column('password_hash', sa.Text(), nullable=True))
    op.create_index(
        op.f('ix_students_email'), 'students', ['email'], unique=True
    )

    # The QR code a novice shows to an existing student. Only a hash of the
    # token is stored, never the plaintext (which is encoded into the image).
    op.create_table(
        'registration_qr_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('token_hash', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('confirmed_by', sa.Integer(), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['confirmed_by'], ['students.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index(
        op.f('ix_registration_qr_tokens_expires_at'),
        'registration_qr_tokens',
        ['expires_at'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_registration_qr_tokens_expires_at'),
        table_name='registration_qr_tokens',
    )
    op.drop_table('registration_qr_tokens')
    op.drop_index(op.f('ix_students_email'), table_name='students')
    op.drop_column('students', 'password_hash')
    op.drop_column('students', 'email')
