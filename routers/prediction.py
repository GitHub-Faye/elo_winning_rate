"""胜率预测 API 路由"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from core.database import get_db
from core.schemas import PredictionRequest, PredictionResponse, ErrorResponse
from services.prediction_service import PredictionService

router = APIRouter(prefix="/api/v1/prediction", tags=["prediction"])


@router.post(
    "",
    response_model=PredictionResponse,
    responses={
        400: {"model": ErrorResponse, "description": "请求参数错误（如人数不匹配）"},
    },
    summary="预测一场比赛的胜率",
    description="""接收双方选手 ID，自动判断单打/双打：

- `team_a`/`team_b` 各 1 人 = 单打
- `team_a`/`team_b` 各 2 人 = 双打

查询 DB 获取选手 Elo 分和比赛记录，基于关系图预测胜率。
""",
)
async def predict(
    req: PredictionRequest,
    db: AsyncSession = Depends(get_db),
) -> PredictionResponse:
    """POST /api/v1/prediction — 预测比赛胜率。"""
    service = PredictionService(db)
    try:
        return await service.predict(req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
