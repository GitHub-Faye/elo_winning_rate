"""Elo 评分 API 路由"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from core.database import get_db
from core.schemas import EloRecordRequest, EloRecordResponse, ErrorResponse
from services.elo_service import EloService

router = APIRouter(prefix="/api/v1/elo", tags=["elo"])


@router.post(
    "/record",
    response_model=EloRecordResponse,
    responses={
        400: {"model": ErrorResponse, "description": "业务校验失败（如比赛不存在、人数不匹配、无有效局数据）"},
        422: {"model": ErrorResponse},
    },
    summary="记录一场比赛并计算 Elo 变化",
    description="""接收 `event_id` + `battle_id`，根据 battle_id 从数据库获取对阵信息：

- `event_id` 仅作占位字段配合前端，不参与 Elo 计算
- 单打/双打由数据库中的选手人数自动判定

选手以身份证号（card_code）定位，未注册用户同样适用。
查询 DB 获取选手当前 Elo 分（新选手用默认值 1500），
计算后写入 `elo_match_record` 并更新 `elo_player_rating`。
""",
)
async def record_match(
    req: EloRecordRequest,
    db: AsyncSession = Depends(get_db),
) -> EloRecordResponse:
    """POST /api/v1/elo/record — 记录比赛并计算 Elo。"""
    service = EloService(db)
    try:
        return await service.record_match(req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
