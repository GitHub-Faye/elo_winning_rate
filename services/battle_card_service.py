"""通用 battle_id 到选手统一定位键转换服务

根据 battle_id 获取参赛选手的统一定位键（身份证号优先，否则手机号），
支持单体赛和团体赛两种模式。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 身份证（18 位，末尾可为 X/x）
_ID_RE = re.compile(r"^\d{17}[\dXx]$")
# 中国手机号（11 位，1[3-9] 开头）
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def is_valid_id(code: Optional[str]) -> bool:
    """身份证号是否合法（18 位，末尾可为 X/x）。"""
    return bool(_ID_RE.match((code or "").strip()))


def is_valid_phone(ph: Optional[str]) -> bool:
    """手机号是否合法（11 位，1[3-9] 开头）。"""
    return bool(_PHONE_RE.match((ph or "").strip()))


def resolve_player_key(card_code: Optional[str], phone: Optional[str]) -> Optional[str]:
    """解析统一定位键：身份证优先，否则手机号；都不合法则返回 None。"""
    card = (card_code or "").strip()
    if is_valid_id(card):
        return card
    ph = (phone or "").strip()
    if is_valid_phone(ph):
        return ph
    return None


async def get_card_codes_by_battle_id(
    db: AsyncSession,
    battle_id: int,
) -> Optional[dict]:
    """根据 battle_id 获取参赛选手的统一定位键（身份证优先，否则手机号）。

    Args:
        db: 数据库会话
        battle_id: 对阵 ID

    Returns:
        包含双方选手统一定位键的字典，或 None（比赛不存在时）
    """
    # Step 1: 获取对阵基本信息
    stmt_battle = text("""
        SELECT
            battle_id, event_id, project_type,
            player_one_id, player_two_id,
            player_one_user_ids, player_two_user_ids,
            player_one_name, player_two_name,
            player_one_score, player_two_score,
            battle_time, item_score
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
        team_a, team_b = await _get_cards_from_user_ids(db, battle)
    else:
        # 单体赛路径：通过 player_one_id/player_two_id → stage_player → apply_id → user_setting
        team_a, team_b = await _get_cards_from_stage_player(db, battle)

    # Step 3: 各自解析统一定位键（身份证优先，否则手机号），无法定位的选手丢弃
    def _resolve(players: list[tuple]) -> tuple:
        keys, cards, phones, names = [], [], [], []
        for card, phone, name in players:
            key = resolve_player_key(card, phone)
            if key is None:
                continue
            keys.append(key)
            cards.append(card)
            phones.append(phone)
            names.append(name)
        return keys, cards, phones, names

    team_a_keys, team_a_cards, team_a_phones, names_a = _resolve(team_a)
    team_b_keys, team_b_cards, team_b_phones, names_b = _resolve(team_b)

    all_count = len(team_a) + len(team_b)
    missing_count = all_count - len(team_a_keys) - len(team_b_keys)

    return {
        "battle_id": battle["battle_id"],
        "event_id": battle["event_id"],
        "project_type": battle["project_type"],
        "team_a": team_a_keys,
        "team_b": team_b_keys,
        "team_a_names": names_a,
        "team_b_names": names_b,
        "team_a_cards": team_a_cards,
        "team_b_cards": team_b_cards,
        "team_a_phones": team_a_phones,
        "team_b_phones": team_b_phones,
        "score_a": battle["player_one_score"],
        "score_b": battle["player_two_score"],
        "item_score": battle.get("item_score"),
        "battle_time": battle["battle_time"],
        "is_valid": missing_count == 0,
        "missing_count": missing_count,
    }


async def _get_cards_from_stage_player(
    db: AsyncSession,
    battle: dict,
) -> tuple[list[tuple], list[tuple]]:
    """单体赛路径：通过 stage_player 获取 (card_code, phone, name) 三元组。"""
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

    team_a: list[tuple] = []
    team_b: list[tuple] = []

    # 处理 A 队
    if p1_stage:
        if p1_stage.get("player_user_ids"):
            user_ids = [int(uid.strip()) for uid in p1_stage["player_user_ids"].split(",") if uid.strip()]
            team_a = await _get_cards_by_user_setting_ids(db, user_ids, event_id)
        else:
            team_a = await _get_cards_by_apply_id(db, p1_stage["apply_id"], event_id)

    # 处理 B 队
    if p2_stage:
        if p2_stage.get("player_user_ids"):
            user_ids = [int(uid.strip()) for uid in p2_stage["player_user_ids"].split(",") if uid.strip()]
            team_b = await _get_cards_by_user_setting_ids(db, user_ids, event_id)
        else:
            team_b = await _get_cards_by_apply_id(db, p2_stage["apply_id"], event_id)

    return team_a, team_b


async def _get_cards_from_user_ids(
    db: AsyncSession,
    battle: dict,
) -> tuple[list[tuple], list[tuple]]:
    """团体赛路径：通过 user_ids 获取 (card_code, phone, name) 三元组。"""
    user_ids_str = battle["player_one_user_ids"] or ""
    user_ids_str_b = battle["player_two_user_ids"] or ""
    event_id = battle["event_id"]

    # 解析 user_setting_id 列表
    user_ids_a = [int(uid.strip()) for uid in user_ids_str.split(",") if uid.strip()]
    user_ids_b = [int(uid.strip()) for uid in user_ids_str_b.split(",") if uid.strip()]

    team_a = await _get_cards_by_user_setting_ids(db, user_ids_a, event_id)
    team_b = await _get_cards_by_user_setting_ids(db, user_ids_b, event_id)

    return team_a, team_b


async def _get_cards_by_apply_id(
    db: AsyncSession,
    apply_id: int,
    event_id: int,
) -> list[tuple]:
    """通过 apply_id 查询该战队所有选手的 (card_code, phone, name)。"""
    stmt = text("""
        SELECT user_setting_id, card_code, phone, name
        FROM motion_event_apply_user_setting
        WHERE apply_id = :apply_id
          AND event_id = :event_id
          AND is_del = 0
          AND pay_status = 1
        ORDER BY user_setting_id
    """)
    result = await db.execute(stmt, {"apply_id": apply_id, "event_id": event_id})
    rows = result.fetchall()

    players = []
    for row in rows:
        row = dict(row._mapping)
        players.append((
            row.get("card_code") or "",
            row.get("phone") or "",
            row.get("name") or "",
        ))
    return players


async def _get_cards_by_user_setting_ids(
    db: AsyncSession,
    user_setting_ids: list[int],
    event_id: int,
) -> list[tuple]:
    """通过 user_setting_id 列表查询 (card_code, phone, name)。"""
    if not user_setting_ids:
        return []

    # 去重 user_setting_ids
    unique_ids = list(set(user_setting_ids))
    if not unique_ids:
        return []

    # 构建 IN 子句的占位符
    placeholders = ", ".join([f":uid{i}" for i in range(len(unique_ids))])
    params = {f"uid{i}": uid for i, uid in enumerate(unique_ids)}

    stmt = text(f"""
        SELECT user_setting_id, card_code, phone, name
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

    players = []
    for row in rows:
        row = dict(row._mapping)
        players.append((
            row.get("card_code") or "",
            row.get("phone") or "",
            row.get("name") or "",
        ))
    return players


# ── 批量查询接口 ──


async def get_card_codes_by_battle_ids(
    db: AsyncSession,
    battle_ids: list[int],
) -> list[dict]:
    """批量查询多个 battle_id 的统一定位键信息。"""
    results = []
    for battle_id in battle_ids:
        result = await get_card_codes_by_battle_id(db, battle_id)
        if result:
            results.append(result)
    return results


async def get_battles_by_player_key(
    db: AsyncSession,
    player_key: str,
    limit: int = 100,
) -> list[dict]:
    """根据统一定位键（身份证优先，否则手机号）查询该选手参加的所有对阵。

    这是 radar_service.py 中 card_to_player + player_to_battles 的改进版本。
    """
    # Step 1: 通过统一定位键获取 user_setting_id
    stmt_user = text("""
        SELECT user_setting_id, event_id, name
        FROM motion_event_apply_user_setting
        WHERE (card_code = :player_key OR phone = :player_key)
          AND is_del = 0
          AND pay_status = 1
        LIMIT 1
    """)
    result = await db.execute(stmt_user, {"player_key": player_key})
    user_row = result.fetchone()

    if user_row is None:
        return []

    user_row = dict(user_row._mapping)
    user_setting_id = user_row["user_setting_id"]
    event_id = user_row["event_id"]

    # Step 2: 查询该选手参加的所有 battle
    # 使用 FIND_IN_SET 精确匹配逗号分隔的 user_setting_id 列表，
    # 避免 LIKE '%12%' 误匹配 '123' 的子串问题
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
              -- 团体赛路径：直接匹配 battle 表的 user_ids 字段
              FIND_IN_SET(:uid, b.player_one_user_ids) > 0
              OR FIND_IN_SET(:uid, b.player_two_user_ids) > 0
              -- 单体赛路径：通过 stage_player 的 player_user_ids 匹配
              OR FIND_IN_SET(:uid, p1.player_user_ids) > 0
              OR FIND_IN_SET(:uid, p2.player_user_ids) > 0
          )
        ORDER BY b.battle_time DESC
        LIMIT :limit
    """)
    result = await db.execute(stmt_battles, {
        "uid": user_setting_id,
        "limit": limit,
    })
    battles = result.fetchall()

    return [dict(row._mapping) for row in battles]