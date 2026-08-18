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
        400: {"model": ErrorResponse, "description": "请求参数错误（如比赛不存在、人数不匹配、比分非法）"},
        422: {"model": ErrorResponse},
    },
    summary="记录一场比赛并计算 Elo 变化",
    description="""接收 battle_id，自动从数据库获取比赛信息并计算 Elo 变化。

**工作流程：**
1. 根据 `battle_id` 查询 `motion_event_layout_stage_battle` 获取比分和选手信息
2. 通过 `battle_card_service` 解析选手身份证号（card_code）
3. 过滤掉无有效身份证号的选手（18位），只保留有效选手参与计算
4. 多局比赛逐局独立计算 Elo，取均值作为最终变化
5. 胜负由「谁赢的局更多」决定（非总分）

**支持场景：**
- 单打（每队 1 人）和双打（每队 2 人）
- 部分选手无身份证号时，跳过无身份证号的选手，有效选手正常计算
- 新选手默认 Elo 为 1500

**身份证号解析路径：**
- 团体赛：`player_one_user_ids` → `apply_user_setting` → `card_code`
- 单体赛：`player_one_id` → `stage_player` → `apply_id` → `apply_user_setting` → `card_code`
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
