"""Elo 比赛加减分日志

每名选手每场比赛一条记录，统一处理单打和双打。

单打：一场比赛产生 2 条记录（A 方 1 人 + B 方 1 人）
双打：一场比赛产生 4 条记录（A 方 2 人 + B 方 2 人）

选手定位键说明：
  - card_code（身份证号）是唯一可靠定位（未注册用户无 user_id，
    motion_event_apply_user_setting.member_id = 0，但 card_code 必有）
  - event_id → motion_event.event_id（数据链路已验证注入，数据库层不设 FK）
  - battle_id → motion_event_layout_stage_battle.battle_id（同上）
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Column, text
from sqlalchemy.dialects.mysql import DECIMAL, TINYINT, BIGINT, DATETIME, INTEGER, VARCHAR
from sqlmodel import Field, SQLModel


class EloPlayerRating(SQLModel, table=True):
    """选手 Elo 分段位分（按运动品类）"""

    __tablename__ = "elo_player_rating"

    card_code: str = Field(
        sa_column=Column(
            "card_code",
            VARCHAR(32),
            primary_key=True,
            comment="选手身份证号，逻辑外键 → motion_event_apply_user_setting.card_code（数据链路保证，不设数据库 FK）",
        ),
    )
    sport_type: str = Field(
        default="badminton",
        primary_key=True,
        sa_type=VARCHAR(32),
        sa_column_kwargs={"comment": "运动品类，如 badminton / tabletennis"},
    )
    rating: Decimal = Field(
        default=Decimal("1500.00"),
        sa_type=DECIMAL(10, 2),
        sa_column_kwargs={"comment": "当前 Elo 分"},
    )
    games: int = Field(
        default=0,
        sa_column_kwargs={"comment": "总比赛场次"},
    )
    wins: int = Field(
        default=0,
        sa_column_kwargs={"comment": "胜场"},
    )
    losses: int = Field(
        default=0,
        sa_column_kwargs={"comment": "负场"},
    )
    draws: int = Field(
        default=0,
        sa_column_kwargs={"comment": "平场"},
    )
    highest_rating: Decimal = Field(
        default=Decimal("1500.00"),
        sa_type=DECIMAL(10, 2),
        sa_column_kwargs={"comment": "历史最高 Elo 分"},
    )
    lowest_rating: Decimal = Field(
        default=Decimal("1500.00"),
        sa_type=DECIMAL(10, 2),
        sa_column_kwargs={"comment": "历史最低 Elo 分"},
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_type=DATETIME,
        sa_column_kwargs={"comment": "创建时间", "server_default": text("CURRENT_TIMESTAMP")},
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_type=DATETIME,
        sa_column_kwargs={
            "comment": "更新时间",
            "server_default": text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        },
    )


class EloMatchRecord(SQLModel, table=True):
    """比赛 Elo 变化日志：每人每场一条记录"""

    __tablename__ = "elo_match_record"

    id: int = Field(
        default=None,
        sa_column=Column("id", BIGINT, primary_key=True, autoincrement=True),
    )
    event_id: int = Field(
        sa_type=INTEGER,
        sa_column_kwargs={
            "comment": "赛事ID，逻辑外键 → motion_event.event_id（数据链路保证，不设数据库 FK）",
        },
    )
    battle_id: int = Field(
        sa_type=INTEGER,
        sa_column_kwargs={
            "comment": "对阵ID，逻辑外键 → motion_event_layout_stage_battle.battle_id（同上）",
        },
    )
    source_order: int = Field(
        default=0,
        sa_type=INTEGER,
        sa_column_kwargs={"comment": "赛事内场序号（event_index），用于回放排序"},
    )

    # ── 选手维度（一人一条，身份证号定位） ──
    card_code: str = Field(
        sa_column=Column("card_code", VARCHAR(32), comment="选手身份证号"),
    )
    team_side: str = Field(
        sa_type=VARCHAR(1),
        sa_column_kwargs={"comment": "所在方 A 或 B"},
    )
    team_size: int = Field(
        sa_type=TINYINT,
        sa_column_kwargs={"comment": "队内人数 1=单打 2=双打"},
    )
    is_winner: int = Field(
        sa_type=TINYINT,
        sa_column_kwargs={"comment": "本方是否获胜 1=是 0=否"},
    )

    # ── 赛前赛后 Elo 状态 ──
    rating_before: Decimal = Field(
        sa_type=DECIMAL(10, 2),
        sa_column_kwargs={"comment": "赛前 Elo"},
    )
    delta: Decimal = Field(
        sa_type=DECIMAL(10, 2),
        sa_column_kwargs={"comment": "Elo 变化（正=加分，负=减分）"},
    )
    rating_after: Decimal = Field(
        sa_type=DECIMAL(10, 2),
        sa_column_kwargs={"comment": "赛后 Elo"},
    )

    # ── 因子分解（完整复现计算所需） ──
    expected: Decimal = Field(
        sa_type=DECIMAL(10, 4),
        sa_column_kwargs={"comment": "本方预期胜率 E"},
    )
    k_factor: Decimal = Field(
        sa_type=DECIMAL(10, 2),
        sa_column_kwargs={"comment": "K 值"},
    )
    weight_multiplier: Decimal = Field(
        sa_type=DECIMAL(10, 4),
        sa_column_kwargs={"comment": "赛事权重 M_weight"},
    )
    margin_multiplier: Decimal = Field(
        sa_type=DECIMAL(10, 4),
        sa_column_kwargs={"comment": "分差倍率 M_margin"},
    )
    base_delta: Decimal = Field(
        sa_type=DECIMAL(10, 2),
        sa_column_kwargs={"comment": "clamp 前的普通变化"},
    )
    clamped_delta: Decimal = Field(
        sa_type=DECIMAL(10, 2),
        sa_column_kwargs={"comment": "clamp 后的普通变化"},
    )
    upset_bonus: Decimal = Field(
        default=Decimal("0.00"),
        sa_type=DECIMAL(10, 2),
        sa_column_kwargs={"comment": "越级加分 bonus"},
    )
    upset_penalty: Decimal = Field(
        default=Decimal("0.00"),
        sa_type=DECIMAL(10, 2),
        sa_column_kwargs={"comment": "被越级扣分 penalty"},
    )

    # ── 对方信息 ──
    opponent_card_code: str = Field(
        sa_column=Column("opponent_card_code", VARCHAR(32), comment="对手身份证号（双打时为第一个对手）"),
    )
    opponent_partner_card_code: Optional[str] = Field(
        default=None,
        sa_column=Column("opponent_partner_card_code", VARCHAR(32), comment="双打时第二个对手，单打为 NULL"),
    )

    # ── 比赛信息 ──
    score_self: int = Field(
        sa_type=INTEGER,
        sa_column_kwargs={"comment": "本方得分"},
    )
    score_opponent: int = Field(
        sa_type=INTEGER,
        sa_column_kwargs={"comment": "对方得分"},
    )
    played_at: Optional[datetime] = Field(
        default=None,
        sa_type=DATETIME,
        sa_column_kwargs={"comment": "比赛时间（来自 battle_time，可为空）"},
    )

    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_type=DATETIME,
        sa_column_kwargs={"comment": "创建时间", "server_default": text("CURRENT_TIMESTAMP")},
    )
