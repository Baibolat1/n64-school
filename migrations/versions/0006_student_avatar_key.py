"""student avatar key

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NULL means the coloured circle with an initial; a value is a key of an
    # image from the built-in gallery (see app.core.avatars). Nothing here
    # stores a path or a filename: only one of the fixed keys ever fits.
    op.add_column(
        'students',
        sa.Column('avatar_key', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('students', 'avatar_key')
