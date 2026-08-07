"""个人比赛记录 API 路由 — 按身份证号查询某选手全部比赛记录"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.schemas import ErrorResponse, PlayerRecordsResponse
from services.player_records_service import get_player_records

router = APIRouter(prefix="/api/v1", tags=["player-records"])


@router.get(
    "/players/{card_code}/records",
    response_model=PlayerRecordsResponse,
    responses={
        400: {"model": ErrorResponse, "description": "请求参数错误"},
    },
    summary="查询选手全部比赛记录",
    description="""根据身份证号查询选手的全部比赛记录（含单打和双打）。

返回逐场比赛明细（比分、胜负、赛前赛后 Elo、对手），附汇总统计：
胜局/负局/胜率/单双打场次/场均得分/场均 Elo 变化。

比分和 Elo 均以该选手自身视角呈现；按比赛时间倒序，最近的排最前。
未查到时返回空记录与全零汇总。选手以身份证号（card_code）定位。""",
)
async def get_player_records_endpoint(
    card_code: str,
    db: AsyncSession = Depends(get_db),
) -> PlayerRecordsResponse:
    """GET /api/v1/players/{card_code}/records — 查询选手全部比赛记录。"""
    data = await get_player_records(db, card_code)
    return PlayerRecordsResponse(success=True, data=data)
