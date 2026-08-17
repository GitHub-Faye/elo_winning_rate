"""比赛结果分析服务 — 根据 battle_id 查询积分变化、排名变化、段位差分

battle_id 全局唯一（elo_match_record 主键），无需 event_id 即可定位。
sport_type 由前端传入（elo_match_record 中无此字段）。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import EloMatchRecord, EloPlayerRating
from core.schemas import (
    MatchAnalysis,
    MatchDetailData,
    MatchPlayerResult,
)
from services.rating_service import (
    BADMINTON_RANK_TIERS,
    PROVISIONAL_GAMES,
    get_badminton_rank,
)


def _get_next_tier(rating: float, games: int) -> Optional[tuple[str, float]]:
    """返回 (下一段位名称, 门槛分数)，若已是最高段或定级中返回 None。

    从低段往高段遍历，找到第一个高于当前 rating 的门槛。
    定级中（games < 2）返回 None。
    """
    if games < PROVISIONAL_GAMES:
        return None
    displayed = int(rating + 0.5)
    # 从低到高遍历
    for threshold, rank in reversed(BADMINTON_RANK_TIERS):
        if displayed < threshold:
            return rank, threshold
    return None  # 已是 9 段


def _compute_region_rank(
    region_rows: list,
    target_card: str,
    target_rating: float,
) -> tuple[Optional[int], int]:
    """计算指定选手在地区内的排名。

    Args:
        region_rows: 地区所有已定级选手的 (card_code, rating) 列表
        target_card: 目标选手的 card_code
        target_rating: 目标选手的 rating（用于替换其在列表中的值）

    Returns:
        (rank, total) — rank 为 1-based，未找到返回 None
    """
    # 构建列表：用 target_rating 替换目标选手的 rating
    rows = []
    found = False
    for card, rating in region_rows:
        if card == target_card:
            rows.append((card, target_rating))
            found = True
        else:
            rows.append((card, float(rating)))

    if not found:
        # 目标选手不在地区内，追加
        rows.append((target_card, target_rating))

    # 排序：rating DESC, card_code ASC
    rows.sort(key=lambda r: (-r[1], r[0]))
    total = len(rows)

    for idx, (card, _) in enumerate(rows):
        if card == target_card:
            return idx + 1, total

    return None, total


async def get_match_detail(
    db: AsyncSession,
    battle_id: int,
    sport_type: str,
    card_code: Optional[str] = None,
) -> Optional[MatchDetailData]:
    """根据 battle_id 查询比赛结果分析。

    Args:
        db: 数据库会话
        battle_id: 对阵 ID（全局唯一）
        sport_type: 运动品类
        card_code: 可选，指定选手身份证号（传入时返回详细分析）

    Returns:
        MatchDetailData 或 None（比赛不存在时）
    """
    # Step 1: 查询比赛记录
    stmt = (
        select(EloMatchRecord)
        .where(EloMatchRecord.battle_id == battle_id)
        .order_by(EloMatchRecord.team_side, EloMatchRecord.id)
    )
    result = await db.execute(stmt)
    records = result.scalars().all()

    if not records:
        return None

    # Step 2: 基础信息
    first = records[0]
    event_id = first.event_id
    team_size = first.team_size
    played_at = first.played_at

    # 从 A 方选手获取比分（score_self / score_opponent）
    a_records = [r for r in records if r.team_side == "A"]
    b_records = [r for r in records if r.team_side == "B"]

    if a_records:
        score_a = a_records[0].score_self
        score_b = a_records[0].score_opponent
    else:
        score_a = 0
        score_b = 0

    match_type = "singles" if team_size == 1 else "doubles"

    # Step 3: 组装 players 列表
    players: list[MatchPlayerResult] = []
    for r in records:
        rating_before = float(r.rating_before)
        rating_after = float(r.rating_after)
        # 段位：elo_match_record 无 games 字段，默认 >=2（已参赛选手）
        rank_before = get_badminton_rank(rating_before, 2)
        rank_after = get_badminton_rank(rating_after, 2)

        players.append(MatchPlayerResult(
            card_code=r.card_code,
            team_side=r.team_side,
            is_winner=bool(r.is_winner),
            rating_before=rating_before,
            rating_after=rating_after,
            delta=float(r.delta),
            score_self=r.score_self,
            score_opponent=r.score_opponent,
            rank_before=rank_before,
            rank_after=rank_after,
        ))

    # Step 4: 若提供 card_code，计算详细分析
    analysis: Optional[MatchAnalysis] = None
    if card_code:
        # 找到目标选手的比赛记录
        target_record = None
        for r in records:
            if r.card_code == card_code:
                target_record = r
                break

        if target_record:
            rating_before = float(target_record.rating_before)
            rating_after = float(target_record.rating_after)

            # 查选手的 province/city/games
            player_stmt = select(
                EloPlayerRating.province,
                EloPlayerRating.city,
                EloPlayerRating.games,
            ).where(
                EloPlayerRating.card_code == card_code,
                EloPlayerRating.sport_type == sport_type,
            )
            player_result = await db.execute(player_stmt)
            player_row = player_result.one_or_none()

            # 默认 games=2（已参赛，至少有一场）
            player_games = 2
            province = None
            city = None
            if player_row:
                province, city, player_games = player_row

            # 段位（用实际场次判断定级中）
            rank_before = get_badminton_rank(rating_before, player_games)
            rank_after = get_badminton_rank(rating_after, player_games)

            # 距下一段位差分（定级中返回 None）
            next_tier_info = _get_next_tier(rating_after, player_games)
            points_to_next = None
            next_tier_name = None
            if next_tier_info:
                next_tier_name, threshold = next_tier_info
                points_to_next = round(threshold - rating_after, 2)

            # 地区排名变化
            region_rank_before = None
            region_rank_after = None
            region_total = None
            region_rank_change = None

            if province or city:
                # 查地区所有已定级选手
                conditions = [
                    EloPlayerRating.sport_type == sport_type,
                    EloPlayerRating.games >= PROVISIONAL_GAMES,
                ]
                if city:
                    conditions.append(EloPlayerRating.city == city)
                elif province:
                    conditions.append(EloPlayerRating.province == province)

                region_stmt = select(
                    EloPlayerRating.card_code,
                    EloPlayerRating.rating,
                ).where(*conditions)
                region_result = await db.execute(region_stmt)
                region_rows = region_result.all()

                # 赛前排名（用 rating_before）
                region_rank_before, region_total = _compute_region_rank(
                    [(r.card_code, float(r.rating)) for r in region_rows],
                    card_code,
                    rating_before,
                )

                # 赛后排名（用 rating_after）
                region_rank_after, _ = _compute_region_rank(
                    [(r.card_code, float(r.rating)) for r in region_rows],
                    card_code,
                    rating_after,
                )

                if region_rank_before is not None and region_rank_after is not None:
                    region_rank_change = region_rank_before - region_rank_after

            analysis = MatchAnalysis(
                card_code=card_code,
                delta=float(target_record.delta),
                rating_before=rating_before,
                rating_after=rating_after,
                rank_before=rank_before,
                rank_after=rank_after,
                points_to_next_tier=points_to_next,
                next_tier=next_tier_name,
                region_rank_before=region_rank_before,
                region_rank_after=region_rank_after,
                region_total=region_total,
                region_rank_change=region_rank_change,
            )

    return MatchDetailData(
        battle_id=battle_id,
        event_id=event_id,
        match_type=match_type,
        score_a=score_a,
        score_b=score_b,
        played_at=played_at,
        players=players,
        analysis=analysis,
    )
