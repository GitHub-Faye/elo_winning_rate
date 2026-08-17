"""单打选手六维雷达图服务 — 以身份证 card_code 定位选手最近 N 场单打

数据链路（读外部赛事记分表，非本服务管理的建模表，用 text() 直查）：
  card_code (身份证号)
    → motion_event_apply_user_setting（user_setting_id, name）
    → motion_event_layout_stage_battle（player_one_user_ids/two_user_ids → battle_id）
    → motion_tool_score_team（layout_rounds_item_id=battle_id, 仅 score_type=1 单打）
    → motion_tool_score_log（逐分 + station JSON 里的 serverBall 发球权）
    → 六维雷达图：进攻/防守/发球/接发/抗压/场区 + 连续得分/连续失分

只取最近 N 场单打，双打（score_type=2/3）不参与计算。
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── 调参 ──
E_COEF = 1.0            # 抗压公式 E 系数（E≈落后阶段丢分，非真正失误，故降低）
SIGMOID_K = 12.0        # 得分率归一化 sigmoid 斜率
SIGMOID_CENTER = 0.50   # 归一化中心（50%=中位）
GAME_CAP = 21           # 每局封顶分（用于关键分判定）
MAX_RECENT_GAMES = 10   # 默认取最近 N 场单打


def sigmoid_map(pct: float) -> float:
    """得分率(0~1)归一化到 0~100，中心 50% 映射到 50，增强视觉区分。
    用 sigmoid 而非线性，次极端值被拉开、极端值压缩到上下界。
    """
    return 100.0 / (1.0 + math.exp(-SIGMOID_K * (pct - SIGMOID_CENTER)))


def norm_offdef(raw: float) -> float:
    """归一化进攻/防守/发球/接发得分率（%）"""
    return round(sigmoid_map(raw / 100.0), 2)


# ──────────────────────────────────────────────
# 一、身份证 → 选手身份
# ──────────────────────────────────────────────
async def card_to_player(db: AsyncSession, card_code: str) -> Optional[dict]:
    """根据身份证查选手基本信息(user_setting_id, name, event_id)。"""
    stmt = text("""
        SELECT user_setting_id, name, event_id, card_code, group_sn
        FROM motion_event_apply_user_setting
        WHERE card_code = :code AND is_del = 0
        LIMIT 1
    """)
    result = await db.execute(stmt, {"code": card_code})
    row = result.fetchone()
    if row is None:
        return None
    return dict(row._mapping)


async def player_to_battles(db: AsyncSession, user_setting_id: int, name: str) -> list[dict]:
    """找到该选手参加的所有 battle（单打/双打均含，后续按 score_type 过滤）。"""
    stmt = text("""
        SELECT DISTINCT b.battle_id, b.event_id, b.player_one_name, b.player_two_name,
               b.player_one_user_ids, b.player_two_user_ids, b.battle_time
        FROM motion_event_layout_stage_battle b
        WHERE (b.player_one_user_ids LIKE :like_one OR b.player_two_user_ids LIKE :like_two)
          AND b.is_del = 0
    """)
    result = await db.execute(stmt, {
        "like_one": f"%{user_setting_id}%",
        "like_two": f"%{user_setting_id}%",
    })
    battles = [dict(r._mapping) for r in result.fetchall()]

    # 姓名兜底（不依赖 user_setting_id 匹配）
    if not battles:
        stmt2 = text("""
            SELECT DISTINCT b.battle_id, b.event_id, b.player_one_name, b.player_two_name,
                   b.player_one_user_ids, b.player_two_user_ids, b.battle_time
            FROM motion_event_layout_stage_battle b
            WHERE (b.player_one_name = :name OR b.player_two_name = :name)
              AND b.is_del = 0
        """)
        r2 = await db.execute(stmt2, {"name": name})
        battles = [dict(r._mapping) for r in r2.fetchall()]
    return battles


async def battle_to_score_team(db: AsyncSession, battle_id: int, user_setting_id: int) -> Optional[dict]:
    """battle_id → 单打 score_team。若是双打返回 None。"""
    stmt = text("""
        SELECT player_one_user_ids, player_two_user_ids
        FROM motion_event_layout_stage_battle WHERE battle_id = :bid
    """)
    result = await db.execute(stmt, {"bid": battle_id})
    b = result.fetchone()
    if b is None:
        return None
    # 用 dict() 确保键名访问
    b = dict(b._mapping)
    one_ids = (b.get("player_one_user_ids") or "")
    two_ids = (b.get("player_two_user_ids") or "")
    is_one = str(user_setting_id) in one_ids.split(",")
    is_two = str(user_setting_id) in two_ids.split(",")
    if not is_one and not is_two:
        return None

    stmt2 = text("""
        SELECT t.score_team_id, t.score_type, t.layout_rounds_item_id,
               t.team_one_name, t.team_two_name,
               t.team_one_score, t.team_two_score, t.create_time
        FROM motion_tool_score_team t
        WHERE t.layout_rounds_item_id = :bid AND t.motion_type = 'badminton'
    """)
    r2 = await db.execute(stmt2, {"bid": battle_id})
    for team in r2.fetchall():
        team = dict(team._mapping)
        if team["score_type"] == 1:  # 单打
            return {
                "score_team_id": team["score_team_id"],
                "score_type": team["score_type"],
                "battle_id": battle_id,
                "my_team_type": 1 if is_one else 2,
                "team_one_name": team["team_one_name"],
                "team_two_name": team["team_two_name"],
                "team_one_score": team["team_one_score"],
                "team_two_score": team["team_two_score"],
                "create_time": team["create_time"],
            }
    return None


# ──────────────────────────────────────────────
# 二、逐分日志解析
# ──────────────────────────────────────────────
async def fetch_logs(db: AsyncSession, team_id: int) -> list[dict]:
    stmt = text("""
        SELECT log_id, round_num, score_player_id, team_type, behavior_type,
               behavior, is_revoke, server, station, create_time
        FROM motion_tool_score_log
        WHERE score_team_id = :tid AND is_revoke = 0
        ORDER BY log_id ASC
    """)
    result = await db.execute(stmt, {"tid": team_id})
    return [dict(r._mapping) for r in result.fetchall()]


def parse_serverball(station_json: Optional[str], my_team_type: int) -> Optional[bool]:
    """解析 station JSON，返回: True=本方发球, False=对方发球, None=解析失败。"""
    if not station_json:
        return None
    try:
        st = json.loads(station_json)
        for side in st:
            if side.get("teamType") == my_team_type:
                for pos in ("one", "two"):
                    if pos in side and side[pos].get("serverBall") is True:
                        return True
                return False
        return None
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def rebuild_score_sequence(logs: list[dict], my_team_type: int) -> dict[int, list[dict]]:
    """从逐分日志重建每局事件序列（含发球权、换边标记）。"""
    games: dict[int, list[dict]] = defaultdict(list)
    for row in logs:
        behavior = row["behavior"] or ""
        evt = {
            "round_num": row["round_num"],
            "is_my_score": row["team_type"] == my_team_type,
            "my_serve": parse_serverball(row["station"], my_team_type),
            "behavior_type": row["behavior_type"],
            "behavior": behavior,
        }
        is_sides = (
            row["behavior_type"] == 3
            or "交换场地" in behavior
            or "换边" in behavior
        )
        evt["is_sides"] = is_sides
        games[row["round_num"]].append(evt)
    return games


# ──────────────────────────────────────────────
# 三、六维计算（纯函数，可单测）
# ──────────────────────────────────────────────
def calc_offense_defense(games: dict) -> dict:
    """进攻/防守/发球/接发（通过 serverBall 判定发球权）。"""
    my_serve_pts = my_serve_won = opp_serve_pts = opp_serve_won = 0
    my_score = opp_score = 0
    for events in games.values():
        for ev in events:
            if ev["behavior_type"] != 2 or ev["my_serve"] is None:
                continue
            if ev["my_serve"]:
                my_serve_pts += 1
                if ev["is_my_score"]:
                    my_serve_won += 1
                    my_score += 1
                else:
                    opp_score += 1
            else:
                opp_serve_pts += 1
                if ev["is_my_score"]:
                    opp_serve_won += 1
                    my_score += 1
                else:
                    opp_score += 1
    offense_raw = my_serve_won / my_serve_pts * 100 if my_serve_pts else 50.0
    receive_raw = opp_serve_won / opp_serve_pts * 100 if opp_serve_pts else 50.0
    return {
        "offense": norm_offdef(offense_raw),
        "defense": norm_offdef(receive_raw),
        "serve": norm_offdef(offense_raw),
        "receive": norm_offdef(receive_raw),
        "offense_raw": round(offense_raw, 2),
        "receive_raw": round(receive_raw, 2),
        "total_my_score": my_score,
        "total_opp_score": opp_score,
    }


def calc_consecutive(games: dict) -> dict:
    """连续得分/连续失分：连胜分段均值、连失分段均值、最大段。"""
    my_s, opp_s = [], []
    for round_num in sorted(games.keys()):
        m = o = 0
        for ev in games[round_num]:
            if ev["behavior_type"] != 2:
                continue
            if ev["is_my_score"]:
                m += 1
                if o:
                    opp_s.append(o)
                    o = 0
            else:
                o += 1
                if m:
                    my_s.append(m)
                    m = 0
        if m:
            my_s.append(m)
        if o:
            opp_s.append(o)
    return {
        "avg_score": round(sum(my_s) / len(my_s), 2) if my_s else 0.0,
        "avg_lose": round(sum(opp_s) / len(opp_s), 2) if opp_s else 0.0,
        "max_score": max(my_s) if my_s else 0,
        "max_lose": max(opp_s) if opp_s else 0,
    }


def calc_anti_pressure(games: dict, game_cap: int = GAME_CAP) -> dict:
    """抗压：S = 50 + 3.5D - 2.5L + 20R + 15K - E_COEF*E（单局），跨局逆风加权平均。"""
    results = []
    for round_num in sorted(games.keys()):
        my = opp = 0
        max_deficit = max_lose_streak = cur_lose = 0
        key_tot = key_won = behind_errors = 0
        for ev in games[round_num]:
            if ev["behavior_type"] != 2:
                continue
            if ev["is_my_score"]:
                my += 1
                cur_lose = 0
            else:
                opp += 1
                cur_lose += 1
                max_lose_streak = max(max_lose_streak, cur_lose)
            deficit = opp - my
            max_deficit = max(max_deficit, deficit)
            if (my >= game_cap - 1 or opp >= game_cap - 1) and abs(my - opp) <= 1:
                key_tot += 1
                if ev["is_my_score"]:
                    key_won += 1
            if deficit > 0 and not ev["is_my_score"]:
                behind_errors += 1
        D, L = max_deficit, max_lose_streak
        R = 1 if (D > 0 and my > opp) else (0 if (D > 0 and my < opp) else 0.5)
        K = key_won / key_tot if key_tot else 0.0
        E = behind_errors
        S = 50 + 3.5 * D - 2.5 * L + 20 * R + 15 * K - E_COEF * E
        S = max(0, min(100, S))
        results.append({
            "round": round_num, "score": f"{my}:{opp}",
            "D": D, "L": L, "R": R, "K": round(K, 2), "E": E,
            "S": round(S, 2),
        })
    # 权重：逆转局 1.2，顺风局 0.8，逆风输/拉锯 1.0
    tw = wsum = 0.0
    for g in results:
        m, o = map(int, g["score"].split(":"))
        w = 1.2 if (g["D"] > 0 and m > o) else (0.8 if g["D"] == 0 else 1.0)
        tw += g["S"] * w
        wsum += w
    return {
        "overall": round(tw / wsum, 2) if wsum else 50.0,
        "games": results,
    }


def calc_field(games: dict) -> dict:
    """场区：换边前后落差 → 100 - (ΔP*2.2 + ΔE*3.0 + ΔO*1.8)。"""
    scores = []
    for round_num in sorted(games.keys()):
        events = games[round_num]
        switch_idx = next((i for i, ev in enumerate(events) if ev.get("is_sides")), None)
        myA = oppA = myB = oppB = 0
        if switch_idx is None:
            # 无换边日志，用 11 分切分（羽毛球规则：任一方达到 11 分换边）
            my = opp = 0
            for ev in events:
                if ev["behavior_type"] != 2:
                    continue
                if ev["is_my_score"]:
                    my += 1
                    if my < 11 and opp < 11:
                        myA += 1
                    else:
                        myB += 1
                else:
                    opp += 1
                    if my < 11 and opp < 11:
                        oppA += 1
                    else:
                        oppB += 1
        else:
            for ev in events[:switch_idx + 1]:
                if ev["behavior_type"] == 2:
                    if ev["is_my_score"]:
                        myA += 1
                    else:
                        oppA += 1
            for ev in events[switch_idx + 1:]:
                if ev["behavior_type"] == 2:
                    if ev["is_my_score"]:
                        myB += 1
                    else:
                        oppB += 1
        dp = abs(myA - myB)
        de = abs(oppA - oppB)
        do = dp  # 无进攻分类，用得分差近似
        fs = max(0, min(100, 100 - (dp * 2.2 + de * 3.0 + do * 1.8)))
        scores.append({
            "round": round_num, "A": f"{myA}:{oppA}", "B": f"{myB}:{oppB}",
            "delta_score": dp, "delta_error": de, "delta_offense": do,
            "score": round(fs, 2),
        })
    return {
        "overall": round(sum(s["score"] for s in scores) / len(scores), 2) if scores else 50.0,
        "games": scores,
    }


# ──────────────────────────────────────────────
# 四、主流程
# ──────────────────────────────────────────────
async def profile_player_by_card(db: AsyncSession, card_code: str, limit: int = MAX_RECENT_GAMES) -> dict:
    """按身份证计算选手最近 limit 场单打的六维雷达图。"""
    player = await card_to_player(db, card_code)
    if player is None:
        raise ValueError(f"未找到身份证 {card_code} 对应的报名选手")
    name = player["name"]
    user_setting_id = player["user_setting_id"]

    battles = await player_to_battles(db, user_setting_id, name)

    # 逐个 battle 关联到单打 score_team，过滤双打
    singles = []
    for b in battles:
        st = await battle_to_score_team(db, b["battle_id"], user_setting_id)
        if st:
            singles.append(st)

    # 按时间取最近 limit 场
    singles.sort(key=lambda x: (x.get("create_time") is None, x.get("create_time") or ""), reverse=True)
    matches = singles[:limit]

    if not matches:
        return {
            "name": name, "card_code": card_code, "matches": 0,
            "total_singles": 0,
            "offense": 0, "defense": 0, "serve": 0, "receive": 0,
            "anti_pressure": 0, "field": 0,
            "consecutive_score": 0, "consecutive_lose": 0,
            "match_details": [],
        }

    agg = {
        "offense": [], "defense": [], "serve": [], "receive": [],
        "anti": [], "field": [], "score_str": [], "lose_str": [],
    }
    details = []
    for m in matches:
        logs = await fetch_logs(db, m["score_team_id"])
        games = rebuild_score_sequence(logs, m["my_team_type"])
        ods = calc_offense_defense(games)
        cons = calc_consecutive(games)
        anti = calc_anti_pressure(games)
        field = calc_field(games)

        my_score = m["team_one_score"] if m["my_team_type"] == 1 else m["team_two_score"]
        opp = m["team_two_score"] if m["my_team_type"] == 1 else m["team_one_score"]
        opp_name = m["team_two_name"] if m["my_team_type"] == 1 else m["team_one_name"]

        agg["offense"].append(ods["offense"])
        agg["defense"].append(ods["defense"])
        agg["serve"].append(ods["serve"])
        agg["receive"].append(ods["receive"])
        agg["anti"].append(anti["overall"])
        agg["field"].append(field["overall"])
        agg["score_str"].append(cons["avg_score"])
        agg["lose_str"].append(cons["avg_lose"])

        details.append({
            "score_team_id": m["score_team_id"],
            "battle_id": m["battle_id"],
            "opponent": opp_name,
            "score": f"{my_score}:{opp}",
            "create_time": str(m.get("create_time"))[:10] if m.get("create_time") else None,
            "offense": ods["offense"],
            "defense": ods["defense"],
            "serve": ods["serve"],
            "receive": ods["receive"],
            "anti_pressure": anti["overall"],
            "field": field["overall"],
            "consecutive_score": cons["avg_score"],
            "consecutive_lose": cons["avg_lose"],
        })

    n = len(matches)
    return {
        "name": name,
        "card_code": card_code,
        "matches": n,
        "total_singles": len(singles),
        "offense": round(sum(agg["offense"]) / n, 2),
        "defense": round(sum(agg["defense"]) / n, 2),
        "serve": round(sum(agg["serve"]) / n, 2),
        "receive": round(sum(agg["receive"]) / n, 2),
        "anti_pressure": round(sum(agg["anti"]) / n, 2),
        "field": round(sum(agg["field"]) / n, 2),
        "consecutive_score": round(sum(agg["score_str"]) / n, 2),
        "consecutive_lose": round(sum(agg["lose_str"]) / n, 2),
        "match_details": details,
    }
