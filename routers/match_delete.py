"""删除比赛记录 API 路由 — 按 battle_id 删除一场比赛并撤回积分"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from core.database import get_db
from core.schemas import (
    DeleteMatchResponse,
    ErrorResponse,
    MatchDeleteErrorResponse,
)
from services.match_delete_service import delete_match

router = APIRouter(prefix="/api/v1/elo", tags=["elo"])


@router.delete(
    "/records/{battle_id}",
    response_model=DeleteMatchResponse,
    responses={
        404: {"model": MatchDeleteErrorResponse, "description": "该场比赛不存在"},
        400: {"model": ErrorResponse, "description": "请求参数错误"},
    },
    summary="删除一场比赛并撤回积分",
    description="""根据对阵 ID 删除一场比赛,并撤回相关选手的积分。

- 该场比赛的全部记录(`elo_match_record`,每人每场一条)都会被删除。
- 对于该场是其个人最新一场的选手,积分会自动回滚到该场赛前状态
  (退回赛前积分,场次/胜/负 -1,并还原历史峰值/谷值)。
- 若该场对某选手并非其最新一场,则仅删除该场的记录,积分保留不动
  (当前积分是后续场次累计的结果,无法仅凭该场回滚)。
  如需同步这类选手的积分,请从最新一场起按倒序逐场删除。

未找到该场比赛时返回 404。
""",
)
async def delete_match_endpoint(
    battle_id: int,
    db: AsyncSession = Depends(get_db),
) -> DeleteMatchResponse:
    """DELETE /api/v1/elo/records/{battle_id} — 删除一场比赛。"""
    resp = await delete_match(db, battle_id)
    if not resp.data.deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=MatchDeleteErrorResponse(
                detail="该场比赛不存在",
                battle_id=battle_id,
                code="match_not_found",
            ).model_dump(),
        )
    return resp
