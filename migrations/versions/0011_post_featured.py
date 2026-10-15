"""featured (pinned) posts

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A flagged post is raised to the top of the feed and wears a badge; the
    # default is what every existing post gets. The column is the whole feature
    # -- no table, since a post carries at most one pin (see app.models.content).
    op.add_column(
        'posts',
        sa.Column(
            'is_featured',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('posts', 'is_featured')
