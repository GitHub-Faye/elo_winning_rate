"""通用 battle_id 到身份证号转换服务

根据 battle_id 获取参赛选手的身份证号，支持单体赛和团体赛两种模式。
"""
from __future__ import annotations

from typing import Optional
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_card_codes_by_battle_id(
    db: AsyncSession,
    battle_id: int,
) -> Optional[dict]:
    """根据 battle_id 获取参赛选手的身份证号。

    Args:
        db: 数据库会话
        battle_id: 对阵 ID

    Returns:
        包含双方选手身份证号的字典，或 None（比赛不存在时）
    """
    # Step 1: 获取对阵基本信息
    stmt_battle = text("""
        SELECT
            battle_id, event_id, project_type,
            player_one_id, player_two_id,
            player_one_user_ids, player_two_user_ids,
            player_one_name, player_two_name,
            player_one_score, player_two_score,
            battle_time
        FROM motion_event_layout_stage_battle
        WHERE battle_id = :battle_id AND is_del = 0
    """)
    result = await db.execute(stmt_battle, {"battle_id": battle_id})
    battle = result.fetchone()

    if battle is None:
        return None

    battle = dict(battle._mapping)

    # Step 2: 根据 player_one_user_ids 是否存在判断获取路径
    # 如果有 player_one_user_ids，直接使用团体赛路径
    # 如果没有，通过 player_one_id → stage_player → apply_id 链路获取
    if battle.get("player_one_user_ids"):
        # 团体赛路径：直接通过 player_one_user_ids/player_two_user_ids → user_setting
        team_a, team_b, names_a, names_b = await _get_cards_from_user_ids(
            db, battle
        )
    else:
        # 单体赛路径：通过 player_one_id/player_two_id → stage_player → apply_id → user_setting
        team_a, team_b, names_a, names_b = await _get_cards_from_stage_player(
            db, battle
        )

    # Step 3: 验证完整性
    all_cards = team_a + team_b
    valid_cards = [c for c in all_cards if c and len(c) == 18]
    missing_count = len(all_cards) - len(valid_cards)

    return {
        "battle_id": battle["battle_id"],
        "event_id": battle["event_id"],
        "project_type": battle["project_type"],
        "team_a": team_a,
        "team_b": team_b,
        "team_a_names": names_a,
        "team_b_names": names_b,
        "score_a": battle["player_one_score"],
        "score_b": battle["player_two_score"],
        "battle_time": battle["battle_time"],
        "is_valid": missing_count == 0,
        "missing_count": missing_count,
    }


async def _get_cards_from_stage_player(
    db: AsyncSession,
    battle: dict,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """单体赛路径：通过 stage_player 获取身份证号。"""
    player_one_id = battle["player_one_id"]
    player_two_id = battle["player_two_id"]
    event_id = battle["event_id"]

    # 查询 stage_player 表获取 apply_id 和 player_user_ids
    stmt_stage = text("""
        SELECT id, apply_id, player_user_ids, player_names
        FROM motion_event_layout_stage_player
        WHERE id IN (:id1, :id2) AND event_id = :event_id AND is_del = 0
    """)
    result = await db.execute(stmt_stage, {
        "id1": player_one_id,
        "id2": player_two_id,
        "event_id": event_id,
    })
    stage_rows = result.fetchall()

    stage_map = {row.id: dict(row._mapping) for row in stage_rows}

    # 获取 A 队和 B 队的 stage_player 信息
    p1_stage = stage_map.get(player_one_id)
    p2_stage = stage_map.get(player_two_id) if player_two_id else None

    # 处理 A 队
    if p1_stage:
        # 如果 stage_player 有 player_user_ids，使用它获取身份证号
        if p1_stage.get("player_user_ids"):
            user_ids = [int(uid.strip()) for uid in p1_stage["player_user_ids"].split(",") if uid.strip()]
            team_a, names_a = await _get_cards_by_user_setting_ids(db, user_ids, event_id)
        else:
            # 否则通过 apply_id 获取
            team_a, names_a = await _get_cards_by_apply_id(db, p1_stage["apply_id"], event_id)
    else:
        team_a, names_a = [], []

    # 处理 B 队
    if p2_stage:
        # 如果 stage_player 有 player_user_ids，使用它获取身份证号
        if p2_stage.get("player_user_ids"):
            user_ids = [int(uid.strip()) for uid in p2_stage["player_user_ids"].split(",") if uid.strip()]
            team_b, names_b = await _get_cards_by_user_setting_ids(db, user_ids, event_id)
        else:
            # 否则通过 apply_id 获取
            team_b, names_b = await _get_cards_by_apply_id(db, p2_stage["apply_id"], event_id)
    else:
        team_b, names_b = [], []

    return team_a, team_b, names_a, names_b


async def _get_cards_from_user_ids(
    db: AsyncSession,
    battle: dict,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """团体赛路径：通过 user_ids 获取身份证号。"""
    user_ids_str = battle["player_one_user_ids"] or ""
    user_ids_str_b = battle["player_two_user_ids"] or ""
    event_id = battle["event_id"]

    # 解析 user_setting_id 列表
    user_ids_a = [int(uid.strip()) for uid in user_ids_str.split(",") if uid.strip()]
    user_ids_b = [int(uid.strip()) for uid in user_ids_str_b.split(",") if uid.strip()]

    # 查询 A 队
    team_a, names_a = await _get_cards_by_user_setting_ids(
        db, user_ids_a, event_id
    )

    # 查询 B 队
    team_b, names_b = await _get_cards_by_user_setting_ids(
        db, user_ids_b, event_id
    )

    return team_a, team_b, names_a, names_b


async def _get_cards_by_apply_id(
    db: AsyncSession,
    apply_id: int,
    event_id: int,
) -> tuple[list[str], list[str]]:
    """通过 apply_id 查询该战队所有选手的身份证号。"""
    stmt = text("""
        SELECT user_setting_id, card_code, name
        FROM motion_event_apply_user_setting
        WHERE apply_id = :apply_id
          AND event_id = :event_id
          AND is_del = 0
          AND pay_status = 1
        ORDER BY user_setting_id
    """)
    result = await db.execute(stmt, {"apply_id": apply_id, "event_id": event_id})
    rows = result.fetchall()

    cards = []
    names = []
    for row in rows:
        row = dict(row._mapping)
        card = row.get("card_code", "")
        name = row.get("name", "")
        cards.append(card if card else "")
        names.append(name)

    return cards, names


async def _get_cards_by_user_setting_ids(
    db: AsyncSession,
    user_setting_ids: list[int],
    event_id: int,
) -> tuple[list[str], list[str]]:
    """通过 user_setting_id 列表查询身份证号。"""
    if not user_setting_ids:
        return [], []

    # 去重 user_setting_ids
    unique_ids = list(set(user_setting_ids))
    if not unique_ids:
        return [], []

    # 构建 IN 子句的占位符
    placeholders = ", ".join([f":uid{i}" for i in range(len(unique_ids))])
    params = {f"uid{i}": uid for i, uid in enumerate(unique_ids)}

    stmt = text(f"""
        SELECT user_setting_id, card_code, name
        FROM motion_event_apply_user_setting
        WHERE user_setting_id IN ({placeholders})
          AND event_id = :event_id
          AND is_del = 0
          AND pay_status = 1
        ORDER BY user_setting_id
    """)
    params["event_id"] = event_id

    result = await db.execute(stmt, params)
    rows = result.fetchall()

    cards = []
    names = []
    for row in rows:
        row = dict(row._mapping)
        card = row.get("card_code", "")
        name = row.get("name", "")
        cards.append(card if card else "")
        names.append(name)

    return cards, names


# ── 批量查询接口 ──


async def get_card_codes_by_battle_ids(
    db: AsyncSession,
    battle_ids: list[int],
) -> list[dict]:
    """批量查询多个 battle_id 的身份证号信息。"""
    results = []
    for battle_id in battle_ids:
        result = await get_card_codes_by_battle_id(db, battle_id)
        if result:
            results.append(result)
    return results


async def get_battles_by_card_code(
    db: AsyncSession,
    card_code: str,
    limit: int = 100,
) -> list[dict]:
    """根据身份证号查询该选手参加的所有对阵。

    这是 radar_service.py 中 card_to_player + player_to_battles 的改进版本。
    """
    # Step 1: 通过 card_code 获取 user_setting_id
    stmt_user = text("""
        SELECT user_setting_id, event_id, name
        FROM motion_event_apply_user_setting
        WHERE card_code = :card_code
          AND is_del = 0
          AND pay_status = 1
        LIMIT 1
    """)
    result = await db.execute(stmt_user, {"card_code": card_code})
    user_row = result.fetchone()

    if user_row is None:
        return []

    user_row = dict(user_row._mapping)
    user_setting_id = user_row["user_setting_id"]
    event_id = user_row["event_id"]

    # Step 2: 查询该选手参加的所有 battle
    stmt_battles = text("""
        SELECT DISTINCT
            b.battle_id,
            b.event_id,
            b.player_one_name,
            b.player_two_name,
            b.player_one_user_ids,
            b.player_two_user_ids,
            b.player_one_score,
            b.player_two_score,
            b.battle_time,
            b.project_type
        FROM motion_event_layout_stage_battle b
        LEFT JOIN motion_event_layout_stage_player p1
            ON p1.id = b.player_one_id AND p1.event_id = b.event_id
        LEFT JOIN motion_event_layout_stage_player p2
            ON p2.id = b.player_two_id AND p2.event_id = b.event_id
        WHERE b.is_del = 0
          AND (
              -- 团体赛路径：直接匹配 user_setting_id
              b.player_one_user_ids LIKE :like_one
              OR b.player_two_user_ids LIKE :like_two
              -- 单体赛路径：通过 stage_player 的 player_user_ids 匹配
              OR p1.player_user_ids LIKE :like_one
              OR p2.player_user_ids LIKE :like_two
          )
        ORDER BY b.battle_time DESC
        LIMIT :limit
    """)
    result = await db.execute(stmt_battles, {
        "like_one": f"%{user_setting_id}%",
        "like_two": f"%{user_setting_id}%",
        "limit": limit,
    })
    battles = result.fetchall()

    return [dict(row._mapping) for row in battles]
