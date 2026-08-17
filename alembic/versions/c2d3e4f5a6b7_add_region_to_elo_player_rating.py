"""add province and city to elo_player_rating

Revision ID: c2d3e4f5a6b7
Revises: 9562204f0fc1
Create Date: 2026-08-17 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, Sequence[str], None] = '9562204f0fc1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add province and city columns to elo_player_rating."""
    op.add_column('elo_player_rating',
        sa.Column('province', mysql.VARCHAR(length=64), nullable=True,
                  comment='归属省份（来自 motion_user.address_province）')
    )
    op.add_column('elo_player_rating',
        sa.Column('city', mysql.VARCHAR(length=64), nullable=True,
                  comment='归属城市（来自 motion_user.address_city）')
    )


def downgrade() -> None:
    """Remove province and city columns from elo_player_rating."""
    op.drop_column('elo_player_rating', 'city')
    op.drop_column('elo_player_rating', 'province')
