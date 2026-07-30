"""Pydantic schemas for Elo API

请求/响应模型，用于 POST /api/v1/elo/record 端点。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


# ── 请求模型 ──


class PlayerInput(BaseModel):
    """单名选手的输入"""
    user_id: int = Field(..., description="选手用户ID")
    rating: float = Field(1500.0, description="当前 Elo 分")
    games: int = Field(0, description="已赛总场次")
    wins: int = Field(0, description="胜场")
    losses: int = Field(0, description="负场")


class EloRecordRequest(BaseModel):
    """Elo 记录请求体"""
    event_id: int = Field(..., description="赛事ID")
    battle_id: int = Field(..., description="对阵ID")
    source_order: int = Field(0, description="赛事内场序号")
    score_a: int = Field(..., description="A 方得分")
    score_b: int = Field(..., description="B 方得分")
    players_a: list[PlayerInput] = Field(
        ..., min_length=1, max_length=2, description="A 方选手列表（1人=单打，2人=双打）"
    )
    players_b: list[PlayerInput] = Field(
        ..., min_length=1, max_length=2, description="B 方选手列表（1人=单打，2人=双打）"
    )
    event_weight: float = Field(1.0, description="赛事权重")
    played_at: Optional[datetime] = Field(None, description="比赛时间")


# ── 响应模型 ──


class EloRecordResponse(BaseModel):
    """Elo 记录响应体"""
    model_config = {"from_attributes": True}
    battle_id: int
    records: list[EloPlayerRecord]
    team_size: int


class EloPlayerRecord(BaseModel):
    """单名选手的 Elo 变化记录"""
    model_config = {"from_attributes": True}
    user_id: int
    team_side: str
    is_winner: bool
    rating_before: float
    delta: float
    rating_after: float
    expected: float
    k_factor: float
    weight_multiplier: float
    margin_multiplier: float
    base_delta: float
    clamped_delta: float
    upset_bonus: float
    upset_penalty: float
    opponent_user_id: int
    opponent_partner_id: Optional[int] = None


class ErrorResponse(BaseModel):
    detail: str