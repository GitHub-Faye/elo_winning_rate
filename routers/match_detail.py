"""比赛结果分析 API 路由 — 根据 battle_id 查询积分变化、排名变化、段位差分"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.schemas import ErrorResponse, MatchDetailResponse
from services.match_detail_service import get_match_detail

router = APIRouter(prefix="/api/v1", tags=["match"])


@router.get(
    "/elo/match/{battle_id}",
    response_model=MatchDetailResponse,
    responses={
        404: {"model": ErrorResponse, "description": "比赛记录不存在"},
    },
    summary="查询比赛结果分析",
    description="""根据对阵 ID（battle_id）查询本场比赛的积分变化。

支持可选参数：
- sport_type: 运动品类（必填）
- card_code: 指定选手身份证号。传入时额外返回该选手的：
  - 距下一段位差分
  - 地区排名变化（赛前 vs 赛后）

不传 card_code 时仅返回所有选手的基础变化（积分加减、段位）。
""",
)
async def get_match_detail_endpoint(
    battle_id: int,
    sport_type: str = Query(..., description="运动品类（如 badminton）"),
    card_code: Optional[str] = Query(
        default=None,
        description="指定选手身份证号（不传返回所有人基础变化）",
    ),
    db: AsyncSession = Depends(get_db),
) -> MatchDetailResponse:
    """GET /api/v1/elo/match/{battle_id} — 比赛结果分析。"""
    data = await get_match_detail(db, battle_id, sport_type, card_code=card_code)
    if data is None:
        raise HTTPException(status_code=404, detail=f"battle_id={battle_id} 的比赛记录不存在")
    return MatchDetailResponse(success=True, data=data)
