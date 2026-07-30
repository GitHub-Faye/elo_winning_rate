"""Pydantic schemas for Elo API

请求/响应模型，用于 POST /api/v1/elo/record 端点。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── 请求模型 ──


class EloRecordRequest(BaseModel):
    """Elo 记录请求体"""
    event_id: int = Field(..., description="赛事ID")
    battle_id: int = Field(..., description="对阵ID")
    source_order: int = Field(0, description="赛事内场序号")
    score_a: int = Field(..., description="A 方得分")
    score_b: int = Field(..., description="B 方得分")
    team_a: list[int] = Field(
        ..., min_length=1, max_length=2, description="A 方选手 user_id 列表（1人=单打，2人=双打）"
    )
    team_b: list[int] = Field(
        ..., min_length=1, max_length=2, description="B 方选手 user_id 列表（1人=单打，2人=双打）"
    )
    event_weight: float = Field(1.0, description="赛事权重")
    played_at: Optional[datetime] = Field(None, description="比赛时间")


# ── 响应模型 ──


class PlayerResult(BaseModel):
    """单名选手的 Elo 变化结果（含因子分解）"""
    user_id: int
    delta: float
    rating_after: float
    games_after: int
    wins_after: int
    losses_after: int
    # 因子分解
    rating_before: float
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


class RecordData(BaseModel):
    """按方分组的 Elo 变化数据"""
    team_a: list[PlayerResult]
    team_b: list[PlayerResult]


class EloRecordResponse(BaseModel):
    """Elo 记录响应体"""
    success: bool = True
    data: RecordData


class ErrorResponse(BaseModel):
    detail: str