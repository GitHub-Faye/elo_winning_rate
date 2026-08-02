"""比赛报名人积分查询服务 — 按 event_id 查询该赛事所有有效报名人的当前积分和段位

数据链路：
  motion_event_apply_user_setting（报名人，event_id + card_code 定位）
  → elo_player_rating（积分，card_code + sport_type 主键）

有效报名口径（与业务确认）：
  - is_del = 0（未删除）
  - pay_status = 1（已支付）
  - card_code 非空（无身份证无法定位积分）
  - 未建档选手（无 elo_player_rating 记录）返回系统默认 1500 / 定级中，is_new=true
"""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import EloPlayerRating
from core.schemas import EventPlayerRating, EventRatingData
from services.rating_service import PROVISIONAL_GAMES, get_badminton_rank

# 当前运动品类（与 services 层一致，后续可扩展为多品类）
CURRENT_SPORT = "badminton"


async def get_event_applicants(
    db: AsyncSession,
    event_id: int,
) -> list[EventPlayerRating]:
    """查询某赛事所有有效报名人（含选手姓名），按 card_code 去重。

    有效报名：is_del=0 且 pay_status=1 且 card_code 非空。
    同一身份证报名多个项目/多次时只保留一条。
    """
    # 报名表不在本服务管理的表清单内（无 SQLModel 模型），用 text() 直查
    stmt = text("""
        SELECT card_code, name
        FROM motion_event_apply_user_setting
        WHERE event_id = :event_id
          AND is_del = 0
          AND pay_status = 1
          AND card_code IS NOT NULL
          AND card_code != ''
    """)
    result = await db.execute(stmt, {"event_id": event_id})
    rows = result.fetchall()

    # 按 card_code 去重（同一人多项目/多次报名只保留一条，先到先得）
    unique: dict[str, str] = {}
    for row in rows:
        code = str(row.card_code).strip()
        if not code:
            continue
        unique.setdefault(code, row.name)

    # 去重后按身份证号排序，保证输出稳定
    return [
        EventPlayerRating(card_code=code, name=name)
        for code, name in sorted(unique.items())
    ]


async def get_event_ratings(
    db: AsyncSession,
    event_id: int,
) -> EventRatingData:
    """查询某赛事所有有效报名人的当前积分和段位（复用批量积分逻辑）。"""
    applicants = await get_event_applicants(db, event_id)
    if not applicants:
        return EventRatingData(event_id=event_id, sport_type=CURRENT_SPORT, results=[])

    card_codes = [a.card_code for a in applicants]

    # 批量查询积分
    stmt = select(EloPlayerRating).where(
        EloPlayerRating.card_code.in_(card_codes),
        EloPlayerRating.sport_type == CURRENT_SPORT,
    )
    result_db = await db.execute(stmt)
    rows = result_db.scalars().all()
    rating_map = {r.card_code: r for r in rows}

    results: list[EventPlayerRating] = []
    for applicant in applicants:
        code = applicant.card_code
        r = rating_map.get(code)
        if r is None:
            # 未建档：按系统默认（与 elo_service 的新选手默认值一致）
            results.append(EventPlayerRating(
                card_code=code,
                name=applicant.name,
                rating=1500.0,
                games=0,
                wins=0,
                losses=0,
                rank="定级中",
                is_provisional=True,
                is_new=True,
            ))
        else:
            rating = float(r.rating)
            results.append(EventPlayerRating(
                card_code=code,
                name=applicant.name,
                rating=rating,
                games=r.games,
                wins=r.wins,
                losses=r.losses,
                rank=get_badminton_rank(rating, r.games),
                is_provisional=r.games < PROVISIONAL_GAMES,
                is_new=False,
            ))

    return EventRatingData(event_id=event_id, sport_type=CURRENT_SPORT, results=results)
