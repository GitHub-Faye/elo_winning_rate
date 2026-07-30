"""Elo 评分 API 路由"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.schemas import EloRecordRequest, EloRecordResponse, ErrorResponse
from services import EloService

router = APIRouter(prefix="/api/v1/elo", tags=["elo"])


@router.post(
    "/record",
    response_model=EloRecordResponse,
    responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="记录一场比赛并计算 Elo 变化",
    description="""接收比赛结果，自动判断单打/双打：

- `players_a`/`players_b` 各 1 人 = 单打
- `players_a`/`players_b` 各 2 人 = 双打

写入 `elo_match_record` 并更新 `elo_player_rating`。
""",
)
async def record_match(
    req: EloRecordRequest,
    db: AsyncSession = Depends(get_db),
) -> EloRecordResponse:
    """POST /api/v1/elo/record — 记录比赛并计算 Elo。"""
    service = EloService(db)
    return await service.record_match(req)
