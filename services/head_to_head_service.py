"""交手记录服务 — 查询两名选手之间的所有胜负记录"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import EloMatchRecord
from core.schemas import HeadToHeadData, HeadToHeadRecord


async def get_head_to_head(
    db: AsyncSession,
    player_a_card: str,
    player_b_card: str,
) -> HeadToHeadData:
    """查询两名选手之间的所有交手记录（含单打和双打）。"""
    # 选手 A 的所有比赛
    stmt_a = select(EloMatchRecord).where(EloMatchRecord.card_code == player_a_card)
    result_a = await db.execute(stmt_a)
    records_a = list(result_a.scalars().all())

    # 选手 B 的所有比赛
    stmt_b = select(EloMatchRecord).where(EloMatchRecord.card_code == player_b_card)
    result_b = await db.execute(stmt_b)
    records_b = list(result_b.scalars().all())

    # B 的记录按 (event_id, battle_id) 建立索引
    b_lookup: dict[tuple[int, int], EloMatchRecord] = {}
    for r in records_b:
        b_lookup[(r.event_id, r.battle_id)] = r

    head_to_head: list[HeadToHeadRecord] = []
    a_wins = 0
    b_wins = 0

    for a_rec in records_a:
        key = (a_rec.event_id, a_rec.battle_id)
        b_rec = b_lookup.get(key)
        if b_rec is None:
            continue
        # 同一方不算对手
        if a_rec.team_side == b_rec.team_side:
            continue

        # 统一以 A 方视角呈现比分
        if a_rec.team_side == "A":
            score_a = a_rec.score_self
            score_b = a_rec.score_opponent
        else:
            score_a = a_rec.score_opponent
            score_b = a_rec.score_self

        winner_card = player_a_card if a_rec.is_winner else player_b_card

        head_to_head.append(HeadToHeadRecord(
            event_id=a_rec.event_id,
            battle_id=a_rec.battle_id,
            team_size=a_rec.team_size,
            score_a=score_a,
            score_b=score_b,
            winner_card=winner_card,
            played_at=a_rec.played_at,
        ))

        if a_rec.is_winner:
            a_wins += 1
        else:
            b_wins += 1

    return HeadToHeadData(
        player_a_card=player_a_card,
        player_b_card=player_b_card,
        total_matches=a_wins + b_wins,
        a_wins=a_wins,
        b_wins=b_wins,
        records=head_to_head,
    )
