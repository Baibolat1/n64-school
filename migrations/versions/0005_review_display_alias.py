"""review display alias

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A review now carries the same alias snapshot as a post or a comment, so
    # hiding behind "Аноним" and keeping a nickname work the same way in every
    # part of the site. SQLite cannot add a NOT NULL column to a table without a
    # default, hence the server_defaults here; the application always supplies
    # both values explicitly, so they only ever cover pre-existing rows.
    op.add_column(
        'reviews',
        sa.Column(
            'display_alias', sa.Text(), nullable=False, server_default='Аноним'
        ),
    )
    op.add_column(
        'reviews',
        sa.Column(
            'is_anonymous', sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )

    # The column was never written to: what the filter produces is stored in
    # ``comment`` itself, exactly as a post's body is, so no second copy of the
    # text exists (see app.services.publication).
    op.drop_column('reviews', 'anonymized_text')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'reviews',
        sa.Column('anonymized_text', sa.Text(), nullable=True),
    )
    op.drop_column('reviews', 'is_anonymous')
    op.drop_column('reviews', 'display_alias')
