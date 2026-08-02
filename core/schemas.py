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
    team_a: list[str] = Field(
        ..., min_length=1, max_length=2, description="A 方选手身份证号列表（1人=单打，2人=双打）"
    )
    team_b: list[str] = Field(
        ..., min_length=1, max_length=2, description="B 方选手身份证号列表（1人=单打，2人=双打）"
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
    card_code: str
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
    opponent_card_code: str
    opponent_partner_card_code: Optional[str] = None


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
    team_a: list[str] = Field(
        ..., min_length=1, max_length=2,
        description="A 方选手身份证号列表（1人=单打，2人=双打）",
    )
    team_b: list[str] = Field(
        ..., min_length=1, max_length=2,
        description="B 方选手身份证号列表（1人=单打，2人=双打）",
    )

    @model_validator(mode="after")
    def _check_unique_ids(self) -> PredictionRequest:
        """同一方不能有重复选手，双方不能有重叠选手。"""
        if len(set(self.team_a)) != len(self.team_a):
            raise ValueError("Team A 中有重复选手")
        if len(set(self.team_b)) != len(self.team_b):
            raise ValueError("Team B 中有重复选手")
        if set(self.team_a) & set(self.team_b):
            raise ValueError("双方不能有相同的选手")
        return self


class PlayerPredictionResult(BaseModel):
    """单名选手的胜率预测结果"""
    card_code: str
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


# ── 交手记录模型 ──


class HeadToHeadRecord(BaseModel):
    """单条交手记录"""
    event_id: int
    battle_id: int
    team_size: int
    """1=单打 2=双打"""
    score_a: int
    """选手 A 得分"""
    score_b: int
    """选手 B 得分"""
    winner_card: str
    """获胜方选手身份证号"""
    played_at: Optional[datetime] = None


class HeadToHeadData(BaseModel):
    """交手记录汇总"""
    player_a_card: str
    player_b_card: str
    total_matches: int
    a_wins: int
    b_wins: int
    records: list[HeadToHeadRecord]


class HeadToHeadResponse(BaseModel):
    """交手记录响应"""
    success: bool = True
    data: HeadToHeadData


# ── 积分查询模型 ──


class RatingQueryRequest(BaseModel):
    """按身份证号批量查询积分请求体"""
    card_codes: list[str] = Field(
        ..., min_length=1, max_length=50,
        description="选手身份证号列表（去重，最多 50 个）",
    )

    @model_validator(mode="after")
    def _dedupe_card_codes(self) -> RatingQueryRequest:
        """去除重复身份证号（同一选手只查一次）。"""
        seen: set[str] = set()
        deduped: list[str] = []
        for code in self.card_codes:
            if code not in seen:
                seen.add(code)
                deduped.append(code)
        self.card_codes = deduped
        return self


class PlayerRatingResult(BaseModel):
    """单名选手的积分查询结果"""
    card_code: str
    """选手身份证号"""
    rating: Optional[float] = None
    """当前 Elo 分（未建档选手为 null）"""
    games: Optional[int] = None
    """总比赛场次"""
    wins: Optional[int] = None
    """胜场"""
    losses: Optional[int] = None
    """负场"""
    rank: Optional[str] = None
    """段位（1段-9段；场次<2 为「定级中」；未建档为 null）"""
    is_provisional: bool = False
    """是否处于定级期（场次 < 2）"""
    is_new: bool = False
    """是否未建档（无 elo_player_rating 记录）"""


class RatingQueryData(BaseModel):
    """批量积分查询数据"""
    sport_type: str
    """运动品类（如 badminton）"""
    results: list[PlayerRatingResult]


class RatingQueryResponse(BaseModel):
    """积分查询响应体"""
    success: bool = True
    data: RatingQueryData


# ── 赛事报名人积分模型 ──


class EventPlayerRating(PlayerRatingResult):
    """赛事报名人积分查询结果（在积分基础上附加选手姓名）"""
    name: Optional[str] = None
    """报名时填写的姓名"""


class EventRatingData(BaseModel):
    """赛事报名人积分查询数据"""
    event_id: int
    """赛事 ID"""
    sport_type: str
    """运动品类（如 badminton）"""
    results: list[EventPlayerRating]


class EventRatingResponse(BaseModel):
    """赛事报名人积分查询响应体"""
    success: bool = True
    data: EventRatingData