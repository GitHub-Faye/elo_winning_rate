"""删除比赛记录服务 — 按 battle_id 删除一场比赛,并撤回积分(让积分保持一致)

数据来源:`elo_match_record`(每人每场一条),`elo_player_rating`(各选手当前积分)。

数据一致性逻辑:
  记录始终删除。积分仅在「该场为该选手的最新一场」时才回滚(退回该场赛前状态);
  否则保留当前积分 -> 该场比赛的比分/Elo 变化从历史中消失,但积分不受影响。

为什么不是无条件回滚积分:
  - 每名选手只保留积分快照,没有逐场历史。
  - 若删除中间场次却回退积分,当前积分会与其它(保留的)场次错位,无法自动补齐。
  -> 接口允许多次调用:先删最新一场(回滚),再删更早的场次(仅删记录,不动积分)。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import EloMatchRecord, EloPlayerRating
from core.schemas import (
    DeleteMatchData,
    DeleteMatchResult,
    DeleteMatchResponse,
    RollbackResult,
)

# 当前运动品类(与 services 层一致)
CURRENT_SPORT = "badminton"


async def delete_match(
    db: AsyncSession,
    battle_id: int,
) -> DeleteMatchResponse:
    """删除一场比赛(record + 选手最新场积分回滚),返回删除与回滚明细。"""
    # 读取该场比赛的全部记录(每人一条,单打 2 条 / 双打 4 条)
    result_records = await db.execute(
        select(EloMatchRecord)
        .where(
            EloMatchRecord.battle_id == battle_id,
        )
        .order_by(EloMatchRecord.id)
    )
    match_rows = list(result_records.scalars().all())

    if not match_rows:
        # 比赛不存在:返回 deleted=False,由路由决定是否映射为 404。
        return _empty_response(battle_id)

    # 判定哪些选手的本场为其个人最新一场(这些人才回滚积分)
    is_latest_map = await _latest_match_flags(db, match_rows)

    players_affected: list[DeleteMatchResult] = []
    skipped_any = False
    # 每名选手处理一次(双打同场 2 人各自回滚)
    for code, rec in _dedupe(match_rows):
        if is_latest_map[code]:
            rb = await _rollback_player(db, code, rec)
            players_affected.append(DeleteMatchResult(
                card_code=code, removed=True, rollback=rb,
            ))
        else:
            skipped_any = True
            players_affected.append(DeleteMatchResult(
                card_code=code, removed=True, rollback=None,
            ))

    # 真正删除该场比赛的全部 record(任何玩家,含非最新场)
    await db.execute(
        delete(EloMatchRecord).where(
            EloMatchRecord.battle_id == battle_id,
        )
    )

    # 提交一笔事务:积分回滚 + 记录删除一起生效
    await db.commit()

    # 从记录中提取 event_id
    event_id = match_rows[0].event_id if match_rows else None

    notice = (
        "存在未回滚积分的选手:这些选手的该场并非其最新一场,积分保留不动;"
        "如需同步他们的积分,请从最新一场起按倒序逐场删除。"
        if skipped_any else None
    )
    data = DeleteMatchData(
        event_id=event_id,
        battle_id=battle_id,
        match_type=_match_type(match_rows),
        deleted=True,
        total_records_deleted=len(match_rows),
        players_affected=players_affected,
    )
    return DeleteMatchResponse(success=True, data=data, notice=notice)


# ── 内部工具 ──


def _empty_response(battle_id: int) -> DeleteMatchResponse:
    """比赛不存在时的空响应(deleted=False,由路由决定是否映射为 404)。"""
    data = DeleteMatchData(
        event_id=None,
        battle_id=battle_id,
        match_type="singles",
        deleted=False,
        total_records_deleted=0,
        players_affected=[],
    )
    return DeleteMatchResponse(
        success=True, data=data, notice="该场比赛不存在,未发生任何删除或积分变更。",
    )


def _match_type(match_rows: list[EloMatchRecord]) -> str:
    """按第一行 team_size 判定单打/双打。"""
    size = match_rows[0].team_size
    return "singles" if size == 1 else "doubles"


def _dedupe(match_rows: list[EloMatchRecord]) -> list[tuple[str, EloMatchRecord]]:
    """按 card_code 去重,返回 [(card_code, 该场中一条记录), ...]。"""
    seen: set[str] = set()
    out: list[tuple[str, EloMatchRecord]] = []
    for r in match_rows:
        if r.card_code not in seen:
            seen.add(r.card_code)
            out.append((r.card_code, r))
    return out


async def _latest_match_flags(
    db: AsyncSession,
    match_rows: list[EloMatchRecord],
) -> dict[str, bool]:
    """判定该场是否为其每名选手的个人最新一场。

    仅做内存内判断(读当前所有记录比对),不写库。
    返回 {card_code: 该选手本场 id 是否为其全部记录中最大}。
    """
    codes = list({r.card_code for r in match_rows})

    # 各选手在该场中的最大 id(双打 2 行取较大者,统一为一行)
    this_max_id: dict[str, int] = {}
    for r in match_rows:
        if this_max_id.get(r.card_code, 0) < r.id:
            this_max_id[r.card_code] = r.id

    # 各选手在所有记录中的最大 id(一条查询拿到全局最大)
    result = await db.execute(
        select(EloMatchRecord.card_code, EloMatchRecord.id).where(
            EloMatchRecord.card_code.in_(codes)
        )
    )
    global_max_id: dict[str, int] = {}
    for code, rid in result.all():
        if global_max_id.get(code, 0) < rid:
            global_max_id[code] = rid

    return {code: global_max_id.get(code, 0) == this_max_id[code] for code in codes}


async def _rollback_player(
    db: AsyncSession,
    card_code: str,
    rec: EloMatchRecord,
) -> RollbackResult:
    """回滚单名选手的积分(仅当该场为其最新一场时调用)。

    退回该场赛前状态(当前积分 == 该场赛后 rating_after):
      rating           -> rating_before
      games            -> -1
      wins             -> -1 if 该场为胜  else 不变
      losses           -> -1 if 该场为负  else 不变
      highest_rating   -> 退回赛前值(赛前即当时历史峰值,现被本场抬高的需还原)
      lowest_rating    -> 退回赛前值(赛前即当时历史谷值)
    """
    # 是否存在该选手的积分记录(应有,因为该场已记录过)
    result = await db.execute(
        select(EloPlayerRating).where(
            EloPlayerRating.card_code == card_code,
            EloPlayerRating.sport_type == CURRENT_SPORT,
        )
    )
    rating = result.scalar_one_or_none()

    if rating is None:
        # 理论不会到(有 match_record 必有 rating),防御性跳过
        return RollbackResult(
            card_code=card_code,
            is_latest_match=True,
            deleted=True,
        )

    before = float(rec.rating_before)
    rating.rating = _dec(before)
    rating.games = max(0, rating.games - 1)
    if rec.is_winner:
        rating.wins = max(0, rating.wins - 1)
    else:
        rating.losses = max(0, rating.losses - 1)
    rating.highest_rating = _dec(min(before, float(rating.highest_rating)))
    rating.lowest_rating = _dec(min(before, float(rating.lowest_rating)))

    # 派发 flush,让后续 commit 一起持久化(不单独 commit,保持本函数无副作用边界)
    # 不在这里 flush — 由 delete_match 最后统一 commit 生效
    return RollbackResult(
        card_code=card_code,
        is_latest_match=True,
        rating_after=float(rating.rating),
        games_after=rating.games,
        wins_after=rating.wins,
        losses_after=rating.losses,
        deleted=True,
    )


def _dec(v: float):
    """float -> Decimal 小数位数(与 elo_service 一致,量化到 0.01)。"""
    from decimal import Decimal
    return Decimal(str(v)).quantize(Decimal("0.01"))
