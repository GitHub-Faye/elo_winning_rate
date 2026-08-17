"""积分查询服务 — 根据身份证号查询当前积分并映射段位

按 `elo_player_rating` 查询选手当前 Elo 分（card_code + sport_type 主键）。
未建档选手返回 null（积分 1500 是记录比赛时才落库，查询侧不臆造数据）。

段位口径与参考实现 elo_core_reference.rating_tier 一致：
  场次 < 2            → 「定级中」
  rating >= 1900      → 9段
  rating >= 1800      → 8段
  rating >= 1700      → 7段
  rating >= 1600      → 6段
  rating >= 1500      → 5段
  rating >= 1400      → 4段
  rating >= 1300      → 3段
  rating >= 1200      → 2段
  其余                → 1段
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import EloPlayerRating
from core.schemas import PlayerRatingResult, RatingQueryData

# 段位门槛（分数从高到低，四舍五入后比较）
BADMINTON_RANK_TIERS = [
    (1900, "9段"),
    (1800, "8段"),
    (1700, "7段"),
    (1600, "6段"),
    (1500, "5段"),
    (1400, "4段"),
    (1300, "3段"),
    (1200, "2段"),
]
# 定级期场次门槛（与 EloConfig.new_player_games 一致）
PROVISIONAL_GAMES = 2


def get_badminton_rank(rating: float, games: int) -> str:
    """根据积分和场次返回段位。

    场次 < PROVISIONAL_GAMES → 「定级中」（与参考实现一致，不看分数）。
    否则按四舍五入后的积分映射 1段-9段。
    """
    if games < PROVISIONAL_GAMES:
        return "定级中"
    # 与参考实现一致：int(rating + 0.5) 四舍五入（避免 Python banker's rounding 的歧义）
    displayed = int(rating + 0.5)
    for threshold, rank in BADMINTON_RANK_TIERS:
        if displayed >= threshold:
            return rank
    return "1段"


async def get_player_ratings(
    db: AsyncSession,
    card_codes: list[str],
    sport_type: str,
) -> RatingQueryData:
    """批量查询选手积分（按传入顺序返回，未建档选手按系统默认值 1500/定级中）。"""
    if not card_codes:
        return RatingQueryData(sport_type=sport_type, results=[])

    # 去重后查询（同一选手只查一次）
    unique_codes = list(dict.fromkeys(card_codes))
    stmt = select(EloPlayerRating).where(
        EloPlayerRating.card_code.in_(unique_codes),
        EloPlayerRating.sport_type == sport_type,
    )
    result_db = await db.execute(stmt)
    rows = result_db.scalars().all()
    rating_map = {r.card_code: r for r in rows}

    results: list[PlayerRatingResult] = []
    for code in unique_codes:
        r = rating_map.get(code)
        if r is None:
            # 未建档：按系统默认（与 elo_service 的新选手默认值一致）
            results.append(PlayerRatingResult(
                card_code=code,
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
            results.append(PlayerRatingResult(
                card_code=code,
                rating=rating,
                games=r.games,
                wins=r.wins,
                losses=r.losses,
                rank=get_badminton_rank(rating, r.games),
                is_provisional=r.games < PROVISIONAL_GAMES,
                is_new=False,
            ))

    return RatingQueryData(sport_type=sport_type, results=results)


async def get_player_ratings_by_card(
    db: AsyncSession,
    card_code: str,
    sport_type: str,
) -> PlayerRatingResult:
    """查询单名选手积分（复用批量逻辑）。"""
    data = await get_player_ratings(db, [card_code], sport_type)
    return data.results[0]


async def get_player_ratings_with_ranking(
    db: AsyncSession,
    card_codes: list[str],
    sport_type: str,
    province: Optional[str] = None,
    city: Optional[str] = None,
) -> RatingQueryData:
    """批量查询选手积分 + 地区排名。

    在 get_player_ratings 基础上，若提供了 province/city，
    查询该地区所有已定级选手（games >= 2），计算目标选手的排名。

    排名规则：按 rating DESC, card_code ASC 排序，1-based rank。
    """
    # Step 1: 获取基础积分数据
    data = await get_player_ratings(db, card_codes, sport_type)
    data.province = province
    data.city = city

    # Step 2: 若无地区筛选，直接返回（region_rank/region_total 保持 None）
    if not province and not city:
        return data

    # Step 3: 查询该地区所有已定级选手
    conditions = [
        EloPlayerRating.sport_type == sport_type,
        EloPlayerRating.games >= PROVISIONAL_GAMES,
    ]
    if city:
        # city 优先级高于 province
        conditions.append(EloPlayerRating.city == city)
    elif province:
        conditions.append(EloPlayerRating.province == province)

    stmt = select(
        EloPlayerRating.card_code,
        EloPlayerRating.rating,
    ).where(*conditions)

    result_db = await db.execute(stmt)
    region_rows = result_db.all()

    # Step 4: 排序并构建 rank_map（rating DESC, card_code ASC）
    region_rows.sort(key=lambda r: (-float(r.rating), r.card_code))
    rank_map = {row.card_code: idx + 1 for idx, row in enumerate(region_rows)}
    region_total = len(region_rows)

    # Step 5: 为每个请求的选手填充排名
    for result in data.results:
        if result.is_new or result.is_provisional:
            # 未建档或定级中的选手不参与排名
            result.region_total = region_total
            continue

        rank = rank_map.get(result.card_code)
        result.region_rank = rank
        result.region_total = region_total

    return data
