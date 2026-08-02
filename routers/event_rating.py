"""比赛报名人积分查询 API 路由 — 按赛事查询所有有效报名人的当前积分和段位"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.schemas import ErrorResponse, EventRatingResponse
from services.event_rating_service import get_event_ratings

router = APIRouter(prefix="/api/v1", tags=["rating"])


@router.get(
    "/event/{event_id}/ratings",
    response_model=EventRatingResponse,
    responses={
        404: {"model": ErrorResponse, "description": "赛事不存在或没有有效报名人"},
    },
    summary="查询某赛事所有报名人的积分和段位",
    description="""根据赛事 ID 查询该赛事所有有效报名人（已支付且未删除）的当前 Elo 积分和段位。

段位规则（场次 < 2 为「定级中」，之后按积分）：
- 1900+ → 9段，1800+ → 8段，1700+ → 7段，1600+ → 6段
- 1500+ → 5段，1400+ → 4段，1300+ → 3段，1200+ → 2段，其余 → 1段

未建档选手（无 elo_player_rating 记录）返回系统默认 1500 / 定级中，is_new=true。
""",
)
async def get_event_ratings_endpoint(
    event_id: int,
    db: AsyncSession = Depends(get_db),
) -> EventRatingResponse:
    """GET /api/v1/event/{event_id}/ratings — 查询赛事全部报名人积分。"""
    data = await get_event_ratings(db, event_id)
    return EventRatingResponse(success=True, data=data)
