"""单打选手六维雷达图 API 路由 — 按身份证查询最近 N 场单打的进攻/防守/发球/接发/抗压/场区"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from core.database import get_db
from core.schemas import ErrorResponse, RadarResponse
from services.radar_service import profile_player_by_card

router = APIRouter(prefix="/api/v1", tags=["radar"])


@router.get(
    "/radar/{card_code}",
    response_model=RadarResponse,
    responses={
        400: {"model": ErrorResponse, "description": "请求参数错误（未找到选手或无单打记录）"},
    },
    summary="查询单名选手最近 N 场单打的六维雷达图",
    description="""根据身份证号查询选手最近 N 场单打比赛的六维雷达图分数。

六维指标（0-100，越高越强，通过发球权 serverBall 判定攻守）：
- 进攻：本方有发球权时得分率（归一化）
- 防守：本方接对方发球时得分率（归一化）
- 发球：本方发球回合得分率
- 接发：本方接发回合得分率
- 抗压：S=50+3.5D-2.5L+20R+15K-E×系数（D最大落后/L最长连失/R逆转/K关键分）
- 场区：换边前后落差（11分换边规则）→ 落差越小分越高

额外指标：平均连续得分 / 平均连续失分。

只统计单打（score_type=1），双打（五羽轮比/团体）不参与。""",
)
async def get_radar(
    card_code: str,
    limit: int = Query(10, ge=1, le=50, description="最近 N 场单打"),
    db: AsyncSession = Depends(get_db),
) -> RadarResponse:
    """GET /api/v1/radar/{card_code}?limit=10 — 查询单名选手雷达图。"""
    try:
        data = await profile_player_by_card(db, card_code, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return RadarResponse(success=True, data=data)
