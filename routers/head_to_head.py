"""交手记录 API 路由"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from core.database import get_db
from core.schemas import HeadToHeadResponse, ErrorResponse
from services.head_to_head_service import get_head_to_head

router = APIRouter(prefix="/api/v1", tags=["head-to-head"])


@router.get(
    "/head-to-head/{player_a_card}/{player_b_card}",
    response_model=HeadToHeadResponse,
    responses={
        400: {"model": ErrorResponse},
    },
    summary="查询两名选手的交手记录",
    description="""查询两名选手之间所有比赛记录（含单打和双打），
返回每场的比分、获胜方，以及汇总统计数据。选手以身份证号定位。""",
)
async def head_to_head(
    player_a_card: str,
    player_b_card: str,
    db: AsyncSession = Depends(get_db),
) -> HeadToHeadResponse:
    """GET /api/v1/head-to-head/{player_a_card}/{player_b_card}"""
    if player_a_card == player_b_card:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="两名选手身份证号不能相同",
        )
    try:
        data = await get_head_to_head(db, player_a_card, player_b_card)
        return HeadToHeadResponse(success=True, data=data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
