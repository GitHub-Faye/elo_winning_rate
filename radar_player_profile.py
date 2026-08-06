"""
单打选手六维雷达图（以身份证 card_code 为入口）
=================================================
数据链路:
  card_code (身份证号)
    → motion_event_apply_user_setting.user_setting_id
    → motion_event_layout_stage_battle.player_one_user_ids / player_two_user_ids (battle_id)
    → motion_tool_score_team.layout_rounds_item_id = battle_id (score_team_id, 仅 score_type=1 单打)
    → motion_tool_score_log (逐分日志 + serverBall 发球权)
    → 六维雷达图: 进攻/防守/发球/接发/抗压/场区 + 连续得分/连续失分

只取最近 N 场单打，双打(score_type=2/3)不参与计算。

用法:
    python radar_player_profile.py --card 210103200504044518 --limit 10
"""

import argparse
import json
import math
import pymysql
from collections import defaultdict

DB_CONFIG = {
    "host": "43.137.99.117", "port": 63697,
    "user": "hjc_dev", "password": "123654",
    "database": "yzmp_dev", "charset": "utf8mb4",
}

# ── 调参 ──
E_COEF = 1.0            # 抗压 E 系数
SIGMOID_K = 12.0        # 得分率归一化斜率
SIGMOID_CENTER = 0.50   # 归一化中心
GAME_CAP = 21           # 每局封顶分


def sigmoid_map(pct):
    return 100.0 / (1.0 + math.exp(-SIGMOID_K * (pct - SIGMOID_CENTER)))


def norm_offdef(raw):
    return round(sigmoid_map(raw / 100.0), 2)


def fetch_conn():
    return pymysql.connect(**DB_CONFIG)


# ──────────────────────────────────────────────
# 一、身份证 → 选手身份
# ──────────────────────────────────────────────
def card_to_player(cursor, card_code):
    """根据身份证查选手基本信息(user_setting_id, name, event_id)。"""
    cursor.execute("""
        SELECT user_setting_id, name, event_id, card_code, group_sn
        FROM yzmp_dev.motion_event_apply_user_setting
        WHERE card_code = %s AND is_del = 0
        LIMIT 1
    """, (card_code,))
    return cursor.fetchone()


def player_to_battles(cursor, user_setting_id, name, limit):
    """
    根据 user_setting_id 找到该选手参加的所有单打比赛(battle)。
    通过 motion_event_layout_stage_battle 的两个 user_ids 字段匹配，
    再关联到 motion_tool_score_team 确认是单打且有逐分日志。
    """
    # 查找该选手出现在 player_one 或 player_two 的所有 battle
    cursor.execute("""
        SELECT DISTINCT b.battle_id, b.event_id, b.player_one_name, b.player_two_name,
               b.player_one_user_ids, b.player_two_user_ids, b.battle_time
        FROM yzmp_dev.motion_event_layout_stage_battle b
        WHERE (b.player_one_user_ids LIKE %s OR b.player_two_user_ids LIKE %s)
          AND b.is_del = 0
    """, (f"%{user_setting_id}%", f"%{user_setting_id}%"))
    battles = cursor.fetchall()

    # 尝试通过姓名兜底（不依赖 user_setting_id 匹配）
    if not battles:
        cursor.execute("""
            SELECT DISTINCT b.battle_id, b.event_id, b.player_one_name, b.player_two_name,
                   b.player_one_user_ids, b.player_two_user_ids, b.battle_time
            FROM yzmp_dev.motion_event_layout_stage_battle b
            WHERE (b.player_one_name = %s OR b.player_two_name = %s)
              AND b.is_del = 0
        """, (name, name))
        battles = cursor.fetchall()

    return battles


# ──────────────────────────────────────────────
# 二、battle → score_team 单打比赛
# ──────────────────────────────────────────────
def battle_to_score_team(cursor, battle_id, user_setting_id):
    """
    由 battle_id 找到对应的运动记分(score_team)。
    返回单打比赛信息；若是双打返回 None。
    my_team_type: 该选手在本场是 team_type 1 还是 2。
    """
    # 确认该选手在本场是 player_one 还是 player_two
    cursor.execute("""
        SELECT player_one_user_ids, player_two_user_ids, player_one_name, player_two_name
        FROM yzmp_dev.motion_event_layout_stage_battle
        WHERE battle_id = %s
    """, (battle_id,))
    b = cursor.fetchone()
    if not b:
        return None
    one_ids = (b["player_one_user_ids"] or "")
    two_ids = (b["player_two_user_ids"] or "")
    is_one = str(user_setting_id) in one_ids.split(",")
    is_two = str(user_setting_id) in two_ids.split(",")
    # 若无 user_ids，退回姓名判断
    if not is_one and not is_two:
        return None

    # 找 motion_tool_score_team 中的记分记录（用 battle_id）
    cursor.execute("""
        SELECT t.score_team_id, t.score_type, t.layout_rounds_item_id,
               t.team_one_name, t.team_two_name,
               t.team_one_score, t.team_two_score, t.create_time
        FROM yzmp_dev.motion_tool_score_team t
        WHERE t.layout_rounds_item_id = %s
          AND t.motion_type = 'badminton'
    """, (battle_id,))
    teams = cursor.fetchall()
    for t in teams:
        if t["score_type"] == 1:  # 单打
            # 单打时 team_one 对应 player_one, team_two 对应 player_two
            my_team_type = 1 if is_one else 2
            return {
                "score_team_id": t["score_team_id"],
                "score_type": t["score_type"],
                "battle_id": battle_id,
                "is_doubles": False,
                "my_team_type": my_team_type,
                "team_one_name": t["team_one_name"],
                "team_two_name": t["team_two_name"],
                "team_one_score": t["team_one_score"],
                "team_two_score": t["team_two_score"],
                "create_time": t["create_time"],
            }
    return None  # 无双打参与


# ──────────────────────────────────────────────
# 三、逐分日志采集与解析
# ──────────────────────────────────────────────
def fetch_logs(cursor, team_id):
    cursor.execute("""
        SELECT log_id, round_num, score_player_id, team_type, behavior_type,
               behavior, is_revoke, server, station, create_time
        FROM yzmp_dev.motion_tool_score_log
        WHERE score_team_id = %s AND is_revoke = 0
        ORDER BY log_id ASC
    """, (team_id,))
    return cursor.fetchall()


def parse_serverball(station_json, my_team_type):
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


def rebuild_score_sequence(logs, my_team_type):
    games = defaultdict(list)
    for row in logs:
        evt = {
            "round_num": row["round_num"],
            "is_my_score": row["team_type"] == my_team_type,
            "my_serve": parse_serverball(row["station"], my_team_type),
            "behavior_type": row["behavior_type"],
            "behavior": row["behavior"],
        }
        # 换边事件
        if row["behavior_type"] == 3 or "交换场地" in (row["behavior"] or "") or "换边" in (row["behavior"] or ""):
            evt["is_sides"] = True
        else:
            evt["is_sides"] = False
        games[row["round_num"]].append(evt)
    return games


# ──────────────────────────────────────────────
# 四、六维计算
# ──────────────────────────────────────────────
def calc_offense_defense(games):
    my_serve_pts = my_serve_won = opp_serve_pts = opp_serve_won = 0
    for round_num, events in games.items():
        for ev in events:
            if ev["behavior_type"] != 2 or ev["my_serve"] is None:
                continue
            if ev["my_serve"]:
                my_serve_pts += 1
                if ev["is_my_score"]:
                    my_serve_won += 1
            else:
                opp_serve_pts += 1
                if ev["is_my_score"]:
                    opp_serve_won += 1
    offense_raw = my_serve_won / my_serve_pts * 100 if my_serve_pts else 50
    receive_raw = opp_serve_won / opp_serve_pts * 100 if opp_serve_pts else 50
    return {
        "offense": norm_offdef(offense_raw),
        "defense": norm_offdef(receive_raw),
        "serve": norm_offdef(offense_raw),
        "receive": norm_offdef(receive_raw),
        "offense_raw": round(offense_raw, 2),
        "receive_raw": round(receive_raw, 2),
        "total_my": sum(1 for e in games.values() for x in e if x["behavior_type"] == 2 and x["is_my_score"]),
        "total_opp": sum(1 for e in games.values() for x in e if x["behavior_type"] == 2 and not x["is_my_score"]),
        "stats": {"srv_pts": my_serve_pts, "srv_won": my_serve_won,
                  "recv_pts": opp_serve_pts, "recv_won": opp_serve_won},
    }


def calc_consecutive(games):
    my_s, opp_s = [], []
    for round_num in sorted(games.keys()):
        m = o = 0
        for ev in games[round_num]:
            if ev["behavior_type"] != 2:
                continue
            if ev["is_my_score"]:
                m += 1
                if o:
                    opp_s.append(o); o = 0
            else:
                o += 1
                if m:
                    my_s.append(m); m = 0
        if m: my_s.append(m)
        if o: opp_s.append(o)
    return {
        "avg_score": round(sum(my_s)/len(my_s), 2) if my_s else 0,
        "avg_lose": round(sum(opp_s)/len(opp_s), 2) if opp_s else 0,
        "max_score": max(my_s) if my_s else 0,
        "max_lose": max(opp_s) if opp_s else 0,
        "score_segs": my_s,
        "lose_segs": opp_s,
    }


def calc_anti_pressure(games, game_cap=GAME_CAP):
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
            if (my >= game_cap-1 or opp >= game_cap-1) and abs(my-opp) <= 1:
                key_tot += 1
                if ev["is_my_score"]:
                    key_won += 1
            if deficit > 0 and not ev["is_my_score"]:
                behind_errors += 1
        D, L = max_deficit, max_lose_streak
        R = 1 if (D > 0 and my > opp) else (0 if (D > 0 and my < opp) else 0.5)
        K = key_won / key_tot if key_tot else 0
        E = behind_errors
        S = 50 + 3.5*D - 2.5*L + 20*R + 15*K - E_COEF*E
        S = max(0, min(100, S))
        results.append({"round": round_num, "score": f"{my}:{opp}", "D": D, "L": L,
                        "R": R, "K": round(K, 2), "E": E, "S": round(S, 2)})
    tw = wsum = 0
    for g in results:
        m, o = map(int, g["score"].split(":"))
        w = 1.2 if (g["D"] > 0 and m > o) else (0.8 if g["D"] == 0 else 1.0)
        tw += g["S"] * w
        wsum += w
    return {"overall": round(tw/wsum, 2) if wsum else 50, "games": results}


def calc_field(games):
    scores = []
    for round_num in sorted(games.keys()):
        events = games[round_num]
        switch_idx = next((i for i, ev in enumerate(events) if ev.get("is_sides")), None)
        myA = oppA = myB = oppB = 0
        if switch_idx is None:
            my = opp = 0
            for ev in events:
                if ev["behavior_type"] != 2:
                    continue
                if ev["is_my_score"]:
                    my += 1
                    (myA if (my < 11 and opp < 11) else myB).__class__  # noqa
                    if my < 11 and opp < 11: myA += 1
                    else: myB += 1
                else:
                    opp += 1
                    if my < 11 and opp < 11: oppA += 1
                    else: oppB += 1
        else:
            for ev in events[:switch_idx+1]:
                if ev["behavior_type"] == 2:
                    if ev["is_my_score"]: myA += 1
                    else: oppA += 1
            for ev in events[switch_idx+1:]:
                if ev["behavior_type"] == 2:
                    if ev["is_my_score"]: myB += 1
                    else: oppB += 1
        dp = abs(myA - myB)
        de = abs(oppA - oppB)
        do = dp
        fs = max(0, min(100, 100 - (dp*2.2 + de*3.0 + do*1.8)))
        scores.append({"round": round_num, "A": f"{myA}:{oppA}", "B": f"{myB}:{oppB}",
                       "dp": dp, "de": de, "do": do, "fs": round(fs, 2)})
    return {"overall": round(sum(s["fs"] for s in scores)/len(scores), 2) if scores else 50, "games": scores}


# ──────────────────────────────────────────────
# 五、主流程
# ──────────────────────────────────────────────
def profile_by_card(card_code, limit=10):
    conn = fetch_conn()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    player = card_to_player(cursor, card_code)
    if not player:
        print(f"❌ 未找到身份证 {card_code} 对应的选手")
        return None
    name = player["name"]
    uid = player["user_setting_id"]
    print(f"✅ 选手识别: {name} (user_setting_id={uid}, event_id={player['event_id']})")
    print(f"   身份证: {card_code}")

    # 找该选手所有比赛 battle
    battles = player_to_battles(cursor, uid, name, None)
    print(f"   参与比赛 battle 数: {len(battles)}")

    # 逐个 battle 关联到单打 score_team，过滤双打
    singles = []
    for b in battles:
        st = battle_to_score_team(cursor, b["battle_id"], uid)
        if st:
            st["battle_time"] = b.get("battle_time")
            singles.append(st)

    # 按时间取最近 limit 场
    singles.sort(key=lambda x: (x.get("create_time") is None, x.get("create_time") or ""), reverse=True)
    matches = singles[:limit]

    print(f"   单打比赛: {len(singles)} 场 (取最近 {len(matches)} 场)")

    if not matches:
        print("   ❌ 无单打比赛数据")
        return None

    agg = defaultdict(list)
    for m in matches:
        logs = fetch_logs(cursor, m["score_team_id"])
        games = rebuild_score_sequence(logs, m["my_team_type"])

        ods = calc_offense_defense(games)
        cons = calc_consecutive(games)
        anti = calc_anti_pressure(games)
        field = calc_field(games)

        my_score = m["team_one_score"] if m["my_team_type"] == 1 else m["team_two_score"]
        opp = m["team_two_score"] if m["my_team_type"] == 1 else m["team_one_score"]
        opp_name = m["team_two_name"] if m["my_team_type"] == 1 else m["team_one_name"]
        mtime = str(m.get("create_time"))[:10]

        print(f"\n  ▸ 场次 {m['score_team_id']} ({mtime}) vs {opp_name} 比分 {my_score}:{opp}")
        ag = {"offense": ods["offense"], "defense": ods["defense"], "serve": ods["serve"],
              "receive": ods["receive"], "anti": anti["overall"], "field": field["overall"],
              "score_str": cons["avg_score"], "lose_str": cons["avg_lose"]}
        for k, v in ag.items():
            agg[k].append(v)
        # 单场明细
        print(f"      进攻{ods['offense']} 防守{ods['defense']} 发球{ods['serve']} 接发{ods['receive']} | "
              f"抗压{anti['overall']} 场区{field['overall']} | 连得分{cons['avg_score']} 连失分{cons['avg_lose']}")

    cursor.close()
    conn.close()

    # 汇总
    result = {"name": name, "card_code": card_code, "matches": len(matches),
              "offense": round(sum(agg["offense"])/len(agg["offense"]), 2),
              "defense": round(sum(agg["defense"])/len(agg["defense"]), 2),
              "serve": round(sum(agg["serve"])/len(agg["serve"]), 2),
              "receive": round(sum(agg["receive"])/len(agg["receive"]), 2),
              "anti_pressure": round(sum(agg["anti"])/len(agg["anti"]), 2),
              "field": round(sum(agg["field"])/len(agg["field"]), 2),
              "consecutive_score": round(sum(agg["score_str"])/len(agg["score_str"]), 2),
              "consecutive_lose": round(sum(agg["lose_str"])/len(agg["lose_str"]), 2)}
    print("\n" + "=" * 55)
    print(f"📡 {name} 最近{len(matches)}场单打六维雷达图")
    print("=" * 55)
    print(f"  进攻: {result['offense']}")
    print(f"  防守: {result['defense']}")
    print(f"  发球: {result['serve']}")
    print(f"  接发: {result['receive']}")
    print(f"  抗压: {result['anti_pressure']}")
    print(f"  场区: {result['field']}")
    print(f"  连续得分: {result['consecutive_score']}")
    print(f"  连续失分: {result['consecutive_lose']}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="按身份证算单打选手六维雷达图")
    parser.add_argument("--card", required=True, help="身份证号 card_code")
    parser.add_argument("--limit", type=int, default=10, help="最近N场单打")
    args = parser.parse_args()
    result = profile_by_card(args.card, args.limit)
    if result:
        print("\n✅ JSON:", json.dumps(result, ensure_ascii=False))
