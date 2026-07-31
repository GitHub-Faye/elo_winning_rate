"""积分查询 API 路由 — 根据身份证号查询当前积分和段位"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.schemas import (
    ErrorResponse,
    RatingQueryRequest,
    RatingQueryResponse,
)
from services.rating_service import (
    get_player_ratings,
    get_badminton_rank,
)

router = APIRouter(prefix="/api/v1", tags=["rating"])

# 当前运动品类（与 services 层一致，后续可扩展为多品类）
CURRENT_SPORT = "badminton"


@router.get(
    "/rating/{card_code}",
    response_model=RatingQueryResponse,
    responses={
        400: {"model": ErrorResponse, "description": "请求参数错误"},
    },
    summary="查询选手当前积分和段位",
    description="""根据身份证号查询选手当前 Elo 积分、比赛场次和段位。

段位规则（场次 < 2 为「定级中」，之后按积分）：
- 1900+ → 9段，1800+ → 8段，1700+ → 7段，1600+ → 6段
- 1500+ → 5段，1400+ → 4段，1300+ → 3段，1200+ → 2段，其余 → 1段

未建档选手（无 elo_player_rating 记录）返回 rating/rank 为 null。
""",
)
async def get_rating(
    card_code: str,
    db: AsyncSession = Depends(get_db),
) -> RatingQueryResponse:
    """GET /api/v1/rating/{card_code} — 查询单名选手积分。"""
    data = await get_player_ratings(db, [card_code], CURRENT_SPORT)
    return RatingQueryResponse(success=True, data=data)


@router.post(
    "/rating/query",
    response_model=RatingQueryResponse,
    responses={
        400: {"model": ErrorResponse, "description": "请求参数错误"},
    },
    summary="批量查询选手积分和段位",
    description="""根据身份证号批量查询选手当前 Elo 积分和段位（去重，最多 50 个）。

未建档选手的 rating/rank 为 null。
""",
)
async def query_ratings(
    req: RatingQueryRequest,
    db: AsyncSession = Depends(get_db),
) -> RatingQueryResponse:
    """POST /api/v1/rating/query — 批量查询选手积分。"""
    data = await get_player_ratings(db, req.card_codes, CURRENT_SPORT)
    return RatingQueryResponse(success=True, data=data)
