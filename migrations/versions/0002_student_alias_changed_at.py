"""student alias_changed_at

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10 19:50:08.060460

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NULL means the alias has never been changed by hand, so the first manual
    # change carries no cooldown.
    op.add_column(
        'students',
        sa.Column('alias_changed_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('students', 'alias_changed_at')
