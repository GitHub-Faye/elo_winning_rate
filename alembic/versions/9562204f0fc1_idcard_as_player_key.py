"""idcard_as_player_key

把选手定位键从 user_id(int) 切换为身份证号 card_code(varchar(32))。

- elo_player_rating: 主键 (card_code, sport_type)，删除 user_id
- elo_match_record: 新增 card_code / opponent_card_code / opponent_partner_card_code，
  删除 user_id / opponent_user_id / opponent_partner_id

存量数据为测试数据（user_id 1001-1008），按用户确认直接清空重建，不做迁移。

Revision ID: 9562204f0fc1
Revises: b1f888ad99af
Create Date: 2026-07-31 14:19:27.502432

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '9562204f0fc1'
down_revision: Union[str, Sequence[str], None] = 'b1f888ad99af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 存量为测试数据，直接清空（用户确认：不做迁移）
    op.execute("DELETE FROM elo_match_record")
    op.execute("DELETE FROM elo_player_rating")

    # ── elo_player_rating：主键切换为 (card_code, sport_type) ──
    op.drop_constraint("PRIMARY", "elo_player_rating", type_="primary")
    op.add_column(
        "elo_player_rating",
        sa.Column("card_code", mysql.VARCHAR(length=32), nullable=False, comment="选手身份证号（逻辑外键 → motion_event_apply_user_setting.card_code）"),
    )
    op.create_primary_key("pk_elo_player_rating", "elo_player_rating", ["card_code", "sport_type"])
    op.drop_column("elo_player_rating", "user_id")

    # ── elo_match_record：user_id → card_code ──
    op.add_column(
        "elo_match_record",
        sa.Column("card_code", mysql.VARCHAR(length=32), nullable=False, comment="选手身份证号"),
    )
    op.add_column(
        "elo_match_record",
        sa.Column("opponent_card_code", mysql.VARCHAR(length=32), nullable=True, comment="对手身份证号（双打时为第一个对手）"),
    )
    op.add_column(
        "elo_match_record",
        sa.Column("opponent_partner_card_code", mysql.VARCHAR(length=32), nullable=True, comment="双打时第二个对手，单打为 NULL"),
    )
    op.drop_column("elo_match_record", "opponent_partner_id")
    op.drop_column("elo_match_record", "opponent_user_id")
    op.drop_column("elo_match_record", "user_id")


def downgrade() -> None:
    """Downgrade schema."""
    # 回退同样清空数据（card_code 无法映射回 int user_id）
    op.execute("DELETE FROM elo_match_record")
    op.execute("DELETE FROM elo_player_rating")

    # ── elo_match_record：card_code → user_id ──
    op.add_column("elo_match_record", sa.Column("user_id", mysql.BIGINT(), nullable=True, comment="选手用户ID"))
    op.add_column("elo_match_record", sa.Column("opponent_user_id", mysql.BIGINT(), nullable=True, comment="对手用户ID（双打时为第一个对手）"))
    op.add_column("elo_match_record", sa.Column("opponent_partner_id", mysql.BIGINT(), nullable=True, comment="双打时第二个对手，单打为 NULL"))
    op.drop_column("elo_match_record", "opponent_partner_card_code")
    op.drop_column("elo_match_record", "opponent_card_code")
    op.drop_column("elo_match_record", "card_code")

    # ── elo_player_rating：card_code → user_id ──
    op.drop_constraint("pk_elo_player_rating", "elo_player_rating", type_="primary")
    op.add_column("elo_player_rating", sa.Column("user_id", mysql.BIGINT(), nullable=False, comment="用户ID，逻辑外键 → motion_user.user_id（数据链路保证，不设数据库 FK）"))
    op.create_primary_key("PRIMARY", "elo_player_rating", ["user_id", "sport_type"])
    op.drop_column("elo_player_rating", "card_code")
