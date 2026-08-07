"""个人比赛记录服务 — 按身份证号查询某选手全部比赛记录（含单打和双打）

数据来源：`elo_match_record`（每人每场一条，card_code 定位）。
按 `played_at`/`source_order` 排序，以前选手视角呈现比分与 Elo 变化，
并附带胜局/胜率/场均得分/Elo 变化等汇总统计。

字段说明：
  - card_code  选手身份证号（定位键）
  - team_size  队内人数 1=单打 2=双打
  - is_winner  本方是否获胜（EloMatchRecord.is_winner 为 1/0，转 bool）
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import EloMatchRecord
from core.schemas import PlayerRecord, PlayerRecordsData, PlayerRecordSummary


def _default_time(rec: EloMatchRecord) -> datetime:
    """记录缺省时间（None → 纪元起点），用于稳定排序（未排期的比赛放最后）。"""
    return rec.played_at if rec.played_at is not None else datetime.min


async def get_player_records(
    db: AsyncSession,
    card_code: str,
) -> PlayerRecordsData:
    """查询某选手全部比赛记录，按时间倒序返回。

    未查到时返回空 records 与全零汇总（不代表选手已建档，仅表示无记录）。
    """
    stmt = (
        select(EloMatchRecord)
        .where(EloMatchRecord.card_code == card_code)
        .order_by(EloMatchRecord.id.desc())
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    # 应用层排序：先按比赛时间倒序（未知时间排最后），再按 id 倒序稳定
    rows.sort(
        key=lambda r: (_default_time(r), r.id),
        reverse=True,
    )

    total_matches = len(rows)
    total_singles = 0
    total_doubles = 0
    wins = 0
    losses = 0
    sum_score_self = 0
    sum_score_opponent = 0
    sum_delta = 0.0

    records: list[PlayerRecord] = []
    for r in rows:
        if r.team_size == 1:
            total_singles += 1
        else:
            total_doubles += 1

        if r.is_winner:
            wins += 1
        else:
            losses += 1

        sum_score_self += r.score_self
        sum_score_opponent += r.score_opponent
        sum_delta += float(r.delta)

        records.append(PlayerRecord(
            event_id=r.event_id,
            battle_id=r.battle_id,
            source_order=r.source_order or 0,
            team_size=r.team_size,
            is_winner=bool(r.is_winner),
            score_self=r.score_self,
            score_opponent=r.score_opponent,
            rating_before=float(r.rating_before),
            rating_after=float(r.rating_after),
            delta=float(r.delta),
            opponent_card_code=r.opponent_card_code,
            opponent_partner_card_code=r.opponent_partner_card_code,
            played_at=r.played_at,
        ))

    summary = PlayerRecordSummary(
        total_matches=total_matches,
        total_singles=total_singles,
        total_doubles=total_doubles,
        wins=wins,
        losses=losses,
        win_rate=(wins / total_matches) if total_matches else None,
        avg_score_self=(sum_score_self / total_matches) if total_matches else None,
        avg_score_opponent=(sum_score_opponent / total_matches) if total_matches else None,
        avg_delta=(sum_delta / total_matches) if total_matches else None,
    )

    return PlayerRecordsData(card_code=card_code, summary=summary, records=records)
