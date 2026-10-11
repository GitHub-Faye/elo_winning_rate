"""unified player_key (card_or_phone)

把选手定位键从「身份证号 card_code」升级为「统一定位键 player_key」：
- player_key = card_code 优先，否则 phone（手机号）
- elo_player_rating: 主键切换为 (player_key, sport_type)，新增 phone，card_code 变为可空参考字段
- elo_match_record: 新增 player_key（建索引）+ phone，card_code 变为可空参考字段

存量数据：player_key = card_code 直接回填，无需清空重建。
downgrade 时手机号-only 的行（card_code 为空）无法还原非空 card_code，会被删除。

Revision ID: f1a2b3c4d5e6
Revises: c2d3e4f5a6b7
Create Date: 2026-10-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ── elo_player_rating：主键切换为 (player_key, sport_type) ──
    op.add_column(
        "elo_player_rating",
        sa.Column("player_key", mysql.VARCHAR(length=64), nullable=True,
                  comment="统一选手定位键：身份证号优先，否则手机号"),
    )
    op.add_column(
        "elo_player_rating",
        sa.Column("phone", mysql.VARCHAR(length=50), nullable=True,
                  comment="手机号（参考字段，逻辑外键 → motion_event_apply_user_setting.phone）"),
    )
    op.execute("UPDATE elo_player_rating SET player_key = card_code WHERE player_key IS NULL")
    op.alter_column("elo_player_rating", "player_key", existing_type=mysql.VARCHAR(64), nullable=False)
    op.drop_constraint("PRIMARY", "elo_player_rating", type_="primary")
    op.create_primary_key("PRIMARY", "elo_player_rating", ["player_key", "sport_type"])
    op.alter_column("elo_player_rating", "card_code", existing_type=mysql.VARCHAR(32), nullable=True)

    # ── elo_match_record：新增 player_key + phone ──
    op.add_column(
        "elo_match_record",
        sa.Column("player_key", mysql.VARCHAR(length=64), nullable=True,
                  comment="统一选手定位键：身份证号优先，否则手机号"),
    )
    op.add_column(
        "elo_match_record",
        sa.Column("phone", mysql.VARCHAR(length=50), nullable=True, comment="手机号（参考字段）"),
    )
    op.execute("UPDATE elo_match_record SET player_key = card_code WHERE player_key IS NULL")
    op.alter_column("elo_match_record", "player_key", existing_type=mysql.VARCHAR(64), nullable=False)
    op.alter_column("elo_match_record", "card_code", existing_type=mysql.VARCHAR(32), nullable=True)
    op.create_index("ix_elo_match_record_player_key", "elo_match_record", ["player_key"])


def downgrade() -> None:
    """Downgrade schema."""
    # 手机号-only 的行（card_code 为空）无法还原非空 card_code，删除
    op.execute("DELETE FROM elo_match_record WHERE card_code IS NULL")
    op.drop_index("ix_elo_match_record_player_key", table_name="elo_match_record")
    op.drop_column("elo_match_record", "phone")
    op.drop_column("elo_match_record", "player_key")
    op.alter_column("elo_match_record", "card_code", existing_type=mysql.VARCHAR(32), nullable=False)

    op.execute("DELETE FROM elo_player_rating WHERE card_code IS NULL")
    op.drop_constraint("PRIMARY", "elo_player_rating", type_="primary")
    op.create_primary_key("PRIMARY", "elo_player_rating", ["card_code", "sport_type"])
    op.drop_column("elo_player_rating", "phone")
    op.drop_column("elo_player_rating", "player_key")