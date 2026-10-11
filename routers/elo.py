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

**工作流程：**
1. 根据 `battle_id` 查询 `motion_event_layout_stage_battle` 获取比分和选手信息
2. 通过 `battle_card_service` 解析选手统一定位键（身份证优先，否则手机号）
3. 过滤掉既无有效身份证号也无有效手机号的选手
4. 多局比赛逐局独立计算 Elo，取均值作为最终变化
5. 胜负由「谁赢的局更多」决定（非总分）

**支持场景：**
- 单打（每队 1 人）和双打（每队 2 人）
- 部分选手无身份证（但有手机号）时，按手机号定位
- 新选手默认 Elo 为 1500

**定位键解析路径：**
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
