"""Pydantic schemas for Elo API

请求/响应模型，用于 POST /api/v1/elo/record 端点。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── 请求模型 ──


class EloRecordRequest(BaseModel):
    """Elo 记录请求体"""
    event_id: int = Field(..., description="赛事ID")
    battle_id: int = Field(..., description="对阵ID")
    source_order: int = Field(0, description="赛事内场序号")
    score_a: int = Field(..., ge=0, description="A 方得分（非负整数）")
    score_b: int = Field(..., ge=0, description="B 方得分（非负整数）")
    team_a: list[int] = Field(
        ..., min_length=1, max_length=2, description="A 方选手 user_id 列表（1人=单打，2人=双打）"
    )
    team_b: list[int] = Field(
        ..., min_length=1, max_length=2, description="B 方选手 user_id 列表（1人=单打，2人=双打）"
    )
    event_weight: float = Field(1.0, gt=0, description="赛事权重（大于 0）")
    played_at: Optional[datetime] = Field(None, description="比赛时间")

    @field_validator("score_a", "score_b")
    @classmethod
    def _check_score_non_negative(cls, v: int) -> int:
        """比分必须非负。"""
        if v < 0:
            raise ValueError("比分不能为负数")
        return v

    @field_validator("event_weight")
    @classmethod
    def _check_weight_positive(cls, v: float) -> float:
        """赛事权重必须大于 0。"""
        if v <= 0:
            raise ValueError("赛事权重必须大于 0")
        return v


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


# ── 预测模型 ──


class PredictionRequest(BaseModel):
    """胜率预测请求体"""
    team_a: list[int] = Field(
        ..., min_length=1, max_length=2,
        description="A 方选手 user_id 列表（1人=单打，2人=双打）",
    )
    team_b: list[int] = Field(
        ..., min_length=1, max_length=2,
        description="B 方选手 user_id 列表（1人=单打，2人=双打）",
    )

    @model_validator(mode="after")
    def _check_unique_ids(self) -> PredictionRequest:
        """同一方不能有重复选手，双方不能有重叠选手。"""
        if len(set(self.team_a)) != len(self.team_a):
            raise ValueError("Team A 中有重复选手 ID")
        if len(set(self.team_b)) != len(self.team_b):
            raise ValueError("Team B 中有重复选手 ID")
        if set(self.team_a) & set(self.team_b):
            raise ValueError("双方不能有相同的选手 ID")
        return self


class PlayerPredictionResult(BaseModel):
    """单名选手的胜率预测结果"""
    user_id: int
    rating: float
    games: int
    wins: int
    losses: int
    probability: float
    """最终预测胜率（clamp 后）"""
    elo_base_probability: float
    """Elo 基础胜率"""
    direct_adjustment: float
    """直接交手修正值"""
    indirect_adjustment: float
    """间接关系修正值"""
    direct_record_wins: int
    """对该方选手的直接交手胜场"""
    direct_record_losses: int
    """对该方选手的直接交手负场"""
    direct_record_total: int
    """对该方选手的总交手场次"""


class PlayerPredictionList(BaseModel):
    """一方队伍的预测结果列表"""
    players: list[PlayerPredictionResult]


class PredictionData(BaseModel):
    """完整的预测响应数据"""
    match_type: str = Field(
        ..., description="比赛类型: singles / doubles",
    )
    team_a: PlayerPredictionList
    team_b: PlayerPredictionList


class PredictionResponse(BaseModel):
    """胜率预测响应体"""
    success: bool = True
    data: PredictionData