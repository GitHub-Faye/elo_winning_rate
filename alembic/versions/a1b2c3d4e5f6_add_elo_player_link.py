"""index elo_player_rating.card_code

动态最高分聚合方案：查询/写入时以身份证号(card_code) 找同一人所有 Elo 账号，
取其中 Elo 分最高的账号作为查询展示/写入累加目标。为此给 `elo_player_rating.card_code`
加普通索引，加速 `WHERE card_code = :id` 的"按证聚合全部同证账号"高频查询。

（对比：早期尝试过 `elo_player_link` 映射表方案，后改为动态聚合、不再需要该表，
故不在这里创建它。）

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 按身份证聚合同一人所有账号 → 需按 card_code 快速查找
    op.create_index("ix_elo_player_rating_card_code", "elo_player_rating", ["card_code"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_elo_player_rating_card_code", table_name="elo_player_rating")