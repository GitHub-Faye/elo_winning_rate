"""
端到端验证：对 score_team_id=4295（霍冠达 vs 王昭）逐分重放，
计算六维雷达图分数。

数据源：motion_tool_score_log (羽毛球记分日志)
        motion_tool_score_team (比赛元信息)

六维:
  1. 进攻: 有发球权时得分率 (进攻得分/总进攻回合)
  2. 防守: 无发球权时失分率 -> 转成防守得分率
  3. 发球: 发球权回合中本方得分率 (serverBall=true 时)
  4. 接发: 接发回合中本方得分率 (serverBall=false 时)
  5. 抗压: D/L/R/K 公式 S=50+3.5D-2.5L+20R+15K-3E
  6. 连续得分/连续失分: 额外指标
  7. 场区: 换边前后落差（需 behavior_type=3 定位换边点）
"""

import json
import math
import pymysql
from collections import defaultdict
from datetime import datetime

# ──────────────────────────────────────────────
# 调参配置
# ──────────────────────────────────────────────
E_COEF = 1.0          # 抗压公式 E 系数（原 3→1，因 E 为"落后阶段丢分"近似，非真正失误）
SIGMOID_K = 12.0       # 得分率归一化 sigmoid 斜率（越大区分度越高）
SIGMOID_CENTER = 0.50  # 得分率归一化中心点（50%=中位数）


def sigmoid_map(score_pct: float) -> float:
    """把得分率(0~1)归一化到 0~100，中心 50% 映射到 50，增强视觉区分度。
    用 sigmoid 而非线性，保证中间区间有梯度、极端值被压缩到上下界。
    """
    return 100.0 / (1.0 + math.exp(-SIGMOID_K * (score_pct - SIGMOID_CENTER)))


def norm_offdef(raw_score: float) -> float:
    """归一化进攻/防守/发球/接发得分率(%)"""
    return round(sigmoid_map(raw_score / 100.0), 2)

DB_CONFIG = {
    "host": "43.137.99.117",
    "port": 63697,
    "user": "hjc_dev",
    "password": "123654",
    "database": "yzmp_dev",
    "charset": "utf8mb4",
}


def fetch_team_info(cursor, team_id):
    """获取比赛元信息"""
    cursor.execute(
        "SELECT score_team_id, layout_rounds_item_id AS battle_id, motion_type, "
        "score_type, games_num, game_score, game_max_score, "
        "team_one_name, team_two_name, team_one_score, team_two_score, "
        "is_change_sides, group_name, field_name "
        "FROM yzmp_dev.motion_tool_score_team "
        "WHERE score_team_id = %s", (team_id,)
    )
    return cursor.fetchone()


def parse_serverball(station_json_str, my_team_type):
    """
    从 station JSON 中解析当前谁在发球。
    station 格式: [{"teamType":1, "one":{name, serverBall}, "two":{name, serverBall}}, ...]
    my_team_type: 本方的 teamType (1=team_one, 2=team_two)
    返回: True=本方发球, False=对方发球, None=解析失败
    """
    try:
        station = json.loads(station_json_str)
        for side in station:
            if side.get("teamType") == my_team_type:
                # 本方队伍，检查 one 和 two 的 serverBall
                for pos in ["one", "two"]:
                    if pos in side and side[pos].get("serverBall") is True:
                        return True  # 本方发球
                return False  # 本方没发球→对方发球
        return None
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def fetch_logs(cursor, team_id):
    """获取指定比赛的所有记分日志（按时间正序）"""
    cursor.execute(
        "SELECT log_id, round_num, score_player_id, team_type, behavior_type, "
        "behavior, is_revoke, server, station, create_time "
        "FROM yzmp_dev.motion_tool_score_log "
        "WHERE score_team_id = %s AND is_revoke = 0 "
        "ORDER BY log_id ASC",
        (team_id,)
    )
    return cursor.fetchall()


def rebuild_score_sequence(logs, my_team_type):
    """
    从逐分日志重建比分序列和关键事件。
    返回每局的结构化数据列表。
    """
    games = defaultdict(list)  # round_num -> [events]

    current_round = None

    for row in logs:
        round_num = row["round_num"]
        if round_num != current_round:
            current_round = round_num

        # 从 behavior 提取当前分数
        score_text = row["behavior"]
        # 尝试提取 "加1分,目前分数为:X" 或 "+1分"
        my_score = None
        opp_score = None

        if "目前分数为" in score_text:
            # 这个分数是得分方的分数
            scorer_team = row["team_type"]
            try:
                s = int(score_text.split("目前分数为:")[1].strip())
                if scorer_team == my_team_type:
                    # 我方得分，所以 my_score=s，对方分数需从上下文中推算
                    # 这里先记下得分方和得分
                    my_score = s
                    opp_score = None  # 由后续推算
                else:
                    opp_score = s
                    my_score = None
            except (IndexError, ValueError):
                pass

        # 解析 serverBall
        my_serve = parse_serverball(row["station"], my_team_type)

        event = {
            "log_id": row["log_id"],
            "round_num": round_num,
            "scorer_team": row["team_type"],
            "is_my_score": row["team_type"] == my_team_type,
            "my_serve": my_serve,
            "my_score_raw": my_score,
            "opp_score_raw": opp_score,
            "behavior_type": row["behavior_type"],
            "behavior": row["behavior"],
            "create_time": row["create_time"],
        }
        games[round_num].append(event)

    return games


def calc_offense_defense(games, my_team_type):
    """计算进攻/防守/发球/接发维度"""
    # 全局统计
    total_my_serve_pts = 0  # 本方发球总回合
    total_my_serve_won = 0  # 本方发球并得分
    total_opp_serve_pts = 0  # 对方发球总回合
    total_opp_serve_won = 0  # 对方发球时本方得分

    total_my_score = 0
    total_opp_score = 0

    for round_num, events in games.items():
        for evt in events:
            if evt["behavior_type"] != 2:
                continue
            if evt["my_serve"] is None:
                continue

            if evt["my_serve"]:
                total_my_serve_pts += 1
                if evt["is_my_score"]:
                    total_my_serve_won += 1
                    total_my_score += 1
                else:
                    total_opp_score += 1
            else:
                total_opp_serve_pts += 1
                if evt["is_my_score"]:
                    total_opp_serve_won += 1
                    total_my_score += 1
                else:
                    total_opp_score += 1

    # 原始得分率
    offense_raw = (total_my_serve_won / total_my_serve_pts * 100) if total_my_serve_pts > 0 else 50.0
    defense_raw = (total_opp_serve_won / total_opp_serve_pts * 100) if total_opp_serve_pts > 0 else 50.0
    serve_raw = offense_raw
    receive_raw = defense_raw

    # 归一化映射到 0~100 视觉区分度
    offense_score = norm_offdef(offense_raw)
    defense_score = norm_offdef(defense_raw)
    serve_score = norm_offdef(serve_raw)
    receive_score = norm_offdef(receive_raw)

    return {
        "offense": offense_score,
        "defense": defense_score,
        "serve": serve_score,
        "receive": receive_score,
        "offense_raw": round(offense_raw, 2),
        "defense_raw": round(defense_raw, 2),
        "total_my_score": total_my_score,
        "total_opp_score": total_opp_score,
        "stats": {
            "my_serve_pts": total_my_serve_pts,
            "my_serve_won": total_my_serve_won,
            "opp_serve_pts": total_opp_serve_pts,
            "opp_serve_won": total_opp_serve_won,
        }
    }


def calc_consecutive_streaks(games, my_team_type):
    """计算连续得分和连续失分"""
    all_my_streaks = []  # 每局连胜分段
    all_opp_streaks = []  # 每局连失分段

    for round_num in sorted(games.keys()):
        events = games[round_num]
        my_streak = 0
        opp_streak = 0
        my_streaks = []
        opp_streaks = []

        for evt in events:
            if evt["behavior_type"] != 2:
                continue
            if evt["is_my_score"]:
                my_streak += 1
                if opp_streak > 0:
                    opp_streaks.append(opp_streak)
                    opp_streak = 0
            else:
                opp_streak += 1
                if my_streak > 0:
                    my_streaks.append(my_streak)
                    my_streak = 0

        # 处理末尾未结束的段
        if my_streak > 0:
            my_streaks.append(my_streak)
        if opp_streak > 0:
            opp_streaks.append(opp_streak)

        all_my_streaks.extend(my_streaks)
        all_opp_streaks.extend(opp_streaks)

    avg_my_streak = (sum(all_my_streaks) / len(all_my_streaks)) if all_my_streaks else 0
    avg_opp_streak = (sum(all_opp_streaks) / len(all_opp_streaks)) if all_opp_streaks else 0

    return {
        "avg_consecutive_score": round(avg_my_streak, 2),
        "avg_consecutive_lose": round(avg_opp_streak, 2),
        "my_streaks": all_my_streaks,
        "opp_streaks": all_opp_streaks,
        "max_my_streak": max(all_my_streaks) if all_my_streaks else 0,
        "max_opp_streak": max(all_opp_streaks) if all_opp_streaks else 0,
    }


def calc_anti_pressure(games, my_team_type, game_score_cap=21):
    """计算抗压维度 S=50+3.5D-2.5L+20R+15K-3E"""
    game_results = []

    for round_num in sorted(games.keys()):
        events = games[round_num]

        # 重建完整比分曲线
        my_score = 0
        opp_score = 0
        score_curve = [(0, 0)]
        max_deficit = 0  # D: 最大落后分差
        max_consecutive_lose = 0  # L: 最长连续失分
        current_lose_streak = 0
        deficit = 0

        # 关键分统计
        total_key_pts = 0
        key_pts_won = 0
        # 定义关键分：局点、平分（>=game_score_cap-1 且分差<=1）
        # 或在 game_score_cap 附近

        # 落后阶段丢分统计（E 近似）
        behind_errors = 0

        for evt in events:
            if evt["behavior_type"] != 2:
                continue

            if evt["is_my_score"]:
                my_score += 1
                current_lose_streak = 0
            else:
                opp_score += 1
                current_lose_streak += 1
                max_consecutive_lose = max(max_consecutive_lose, current_lose_streak)

            score_curve.append((my_score, opp_score))
            deficit = opp_score - my_score
            max_deficit = max(max_deficit, deficit)

            # 关键分判定
            if my_score >= game_score_cap - 1 or opp_score >= game_score_cap - 1:
                if abs(my_score - opp_score) <= 1:
                    total_key_pts += 1
                    if evt["is_my_score"]:
                        key_pts_won += 1

            # 落后阶段丢分
            if deficit > 0 and not evt["is_my_score"]:
                behind_errors += 1

        # D: 最大落后
        D = max_deficit

        # L: 最长连续失分
        L = max_consecutive_lose

        # R: 逆转结果
        final_my = my_score
        final_opp = opp_score
        if D > 0 and final_my > final_opp:
            R = 1  # 落后翻盘
        elif D > 0 and final_my < final_opp:
            R = 0  # 落后输局
        else:
            R = 0.5  # 全程领先/拉锯

        # K: 关键分得分率
        K = key_pts_won / total_key_pts if total_key_pts > 0 else 0

        # E: 逆风失误
        E = behind_errors

        S = 50 + 3.5 * D - 2.5 * L + 20 * R + 15 * K - E_COEF * E
        S = max(0, min(100, S))

        game_results.append({
            "round": round_num,
            "my_score": final_my,
            "opp_score": final_opp,
            "D": D,
            "L": L,
            "R": R,
            "K": round(K, 2),
            "E": E,
            "S": round(S, 2),
            "key_pts_total": total_key_pts,
            "key_pts_won": key_pts_won,
        })

    # 全局加权
    total_weighted = 0
    total_weight = 0
    for g in game_results:
        # 逆风局权重1.2，顺风0.8，拉锯1.0
        if g["D"] > 0 and g["my_score"] > g["opp_score"]:
            w = 1.2  # 逆转局
        elif g["D"] > 0 and g["my_score"] < g["opp_score"]:
            w = 1.2  # 逆风输局
        elif g["D"] == 0:
            w = 0.8  # 顺风局
        else:
            w = 1.0
        total_weighted += g["S"] * w
        total_weight += w

    overall_S = round(total_weighted / total_weight, 2) if total_weight > 0 else 50

    return {
        "overall": overall_S,
        "games": game_results,
        "total_weighted": round(total_weighted, 2),
        "total_weight": round(total_weight, 2),
    }


def calc_field_adaptability(games, my_team_type):
    """
    计算场区适应性（换边前后落差）。
    羽毛球11分换边。用 behavior_type=3 定位换边事件。
    由于日志里 addScore 和 sides 混排，我们用比分=11时作为换边点。
    """
    all_scores = []

    for round_num in sorted(games.keys()):
        events = games[round_num]
        # 先找出换边时的比分
        side_switch_at = None

        for evt in events:
            if evt["behavior_type"] == 3:
                # 交换场地事件
                side_switch_at = "event"
                break

        # 如果没找到换边事件，用 11 分作为近似
        if side_switch_at is None:
            side_switch_at = "score_11"

        # 分两段统计
        my_score = 0
        opp_score = 0
        A_my_score = 0
        A_opp_score = 0
        A_total_pts = 0
        B_my_score = 0
        B_opp_score = 0
        B_total_pts = 0
        switched = False

        for evt in events:
            if evt["behavior_type"] == 3:
                switched = True
                continue

            if evt["behavior_type"] != 2:
                continue

            if not switched:
                if evt["is_my_score"]:
                    A_my_score += 1
                else:
                    A_opp_score += 1
                A_total_pts += 1
            else:
                if evt["is_my_score"]:
                    B_my_score += 1
                else:
                    B_opp_score += 1
                B_total_pts += 1

        # 如果没找到换边事件，用 11 分手动分割
        if not switched:
            # 重新按分数分割
            my_s = 0
            opp_s = 0
            A_my_score = 0
            A_opp_score = 0
            B_my_score = 0
            B_opp_score = 0
            A_total_pts = 0
            B_total_pts = 0

            for evt in events:
                if evt["behavior_type"] != 2:
                    continue
                if evt["is_my_score"]:
                    my_s += 1
                else:
                    opp_s += 1

                # 如果有一方到11分，就是换边点
                if my_s < 11 and opp_s < 11:
                    if evt["is_my_score"]:
                        A_my_score += 1
                    else:
                        A_opp_score += 1
                    A_total_pts += 1
                else:
                    if evt["is_my_score"]:
                        B_my_score += 1
                    else:
                        B_opp_score += 1
                    B_total_pts += 1

        # ΔP: 得分差
        delta_P = abs(A_my_score - B_my_score)

        # ΔE: 失误落差（用"对方得分"近似失误）
        # A段失误=A_opp_score, B段失误=B_opp_score
        delta_E = abs(A_opp_score - B_opp_score)

        # ΔO: 进攻落差（用"有发球权时得分"近似主动进攻）
        # 这部分简化处理：用"本方得分"近似
        delta_O = abs(A_my_score - B_my_score)  # 与ΔP一致，简化

        # 场区分数
        field_score = 100 - (delta_P * 2.2 + delta_E * 3.0 + delta_O * 1.8)
        field_score = max(0, min(100, field_score))

        all_scores.append({
            "round": round_num,
            "A_my_score": A_my_score,
            "A_opp_score": A_opp_score,
            "B_my_score": B_my_score,
            "B_opp_score": B_opp_score,
            "delta_P": delta_P,
            "delta_E": delta_E,
            "delta_O": delta_O,
            "field_score": round(field_score, 2),
        })

    avg_field = sum(s["field_score"] for s in all_scores) / len(all_scores) if all_scores else 50

    return {
        "overall": round(avg_field, 2),
        "games": all_scores,
    }


def main():
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    team_id = 4295
    # 忻州师院是 team_one (team_type=1), 太原师院是 team_two (team_type=2)

    print("=" * 70)
    print("端到端验证：羽毛球比赛逐分重放 -> 六维雷达图")
    print("=" * 70)

    team_info = fetch_team_info(cursor, team_id)
    if not team_info:
        print("未找到比赛信息")
        return

    print(f"\n比赛: {team_info['team_one_name']} vs {team_info['team_two_name']}")
    print(f"比分: {team_info['team_one_score']}:{team_info['team_two_score']}")
    print(f"局数: {team_info['games_num']}, 每局{team_info['game_score']}分, 延长{team_info['game_max_score']}分")

    logs = fetch_logs(cursor, team_id)
    print(f"\n总日志条数: {len(logs)}")
    print(f"局数(日志中): {len(set(l['round_num'] for l in logs))}")

    # 为霍冠达（忻州师院，team_type=1）计算
    print("\n" + "=" * 70)
    print(f"选手: 霍冠达（{team_info['team_one_name']}，team_type=1）")
    print("=" * 70)

    games = rebuild_score_sequence(logs, my_team_type=1)

    # 1. 进攻/防守/发球/接发
    ods = calc_offense_defense(games, my_team_type=1)
    print(f"\n📊 进攻/防守/发球/接发")
    print(f"  总分: {ods['total_my_score']}:{ods['total_opp_score']}")
    print(f"  进攻(发球权得分率): {ods['offense']} (原始{ods['offense_raw']}%)")
    print(f"  防守(接发权得分率): {ods['defense']} (原始{ods['defense_raw']}%)")
    print(f"  发球: {ods['serve']} (原始{ods['offense_raw']}%)")
    print(f"  接发: {ods['receive']} (原始{ods['defense_raw']}%)")
    print(f"  明细: 本方发球 {ods['stats']['my_serve_pts']} 回合, 得分 {ods['stats']['my_serve_won']}")
    print(f"        对方发球 {ods['stats']['opp_serve_pts']} 回合, 本方得分 {ods['stats']['opp_serve_won']}")

    # 2. 连续得分/连续失分
    streaks = calc_consecutive_streaks(games, my_team_type=1)
    print(f"\n📊 连续得分/连续失分")
    print(f"  平均连续得分: {streaks['avg_consecutive_score']}")
    print(f"  平均连续失分: {streaks['avg_consecutive_lose']}")
    print(f"  最长连胜: {streaks['max_my_streak']}")
    print(f"  最长连失: {streaks['max_opp_streak']}")
    print(f"  连胜分段: {streaks['my_streaks']}")
    print(f"  连失分段: {streaks['opp_streaks']}")

    # 3. 抗压
    anti = calc_anti_pressure(games, my_team_type=1, game_score_cap=21)
    print(f"\n📊 抗压 (S=50+3.5D-2.5L+20R+15K-3E)")
    print(f"  综合抗压得分: {anti['overall']}")
    for g in anti["games"]:
        print(f"  第{g['round']}局: 比分 {g['my_score']}:{g['opp_score']}, "
              f"D={g['D']}, L={g['L']}, R={g['R']}, K={g['K']}, E={g['E']}, "
              f"S={g['S']}")

    # 4. 场区
    field = calc_field_adaptability(games, my_team_type=1)
    print(f"\n📊 场区适应性")
    print(f"  综合场区得分: {field['overall']}")
    for g in field["games"]:
        print(f"  第{g['round']}局: 前半段 {g['A_my_score']}:{g['A_opp_score']}, "
              f"后半段 {g['B_my_score']}:{g['B_opp_score']}, "
              f"ΔP={g['delta_P']}, ΔE={g['delta_E']}, ΔO={g['delta_O']}, "
              f"得分={g['field_score']}")

    # ===== 为王昭计算 =====
    print("\n" + "=" * 70)
    print(f"选手: 王昭（{team_info['team_two_name']}，team_type=2）")
    print("=" * 70)

    games2 = rebuild_score_sequence(logs, my_team_type=2)

    ods2 = calc_offense_defense(games2, my_team_type=2)
    print(f"\n📊 进攻/防守/发球/接发")
    print(f"  总分: {ods2['total_my_score']}:{ods2['total_opp_score']}")
    print(f"  进攻(发球权得分率): {ods2['offense']} (原始{ods2['offense_raw']}%)")
    print(f"  防守(接发权得分率): {ods2['defense']} (原始{ods2['defense_raw']}%)")
    print(f"  发球: {ods2['serve']} (原始{ods2['offense_raw']}%)")
    print(f"  接发: {ods2['receive']} (原始{ods2['defense_raw']}%)")
    print(f"  明细: 本方发球 {ods2['stats']['my_serve_pts']} 回合, 得分 {ods2['stats']['my_serve_won']}")
    print(f"        对方发球 {ods2['stats']['opp_serve_pts']} 回合, 本方得分 {ods2['stats']['opp_serve_won']}")

    streaks2 = calc_consecutive_streaks(games2, my_team_type=2)
    print(f"\n📊 连续得分/连续失分")
    print(f"  平均连续得分: {streaks2['avg_consecutive_score']}")
    print(f"  平均连续失分: {streaks2['avg_consecutive_lose']}")
    print(f"  最长连胜: {streaks2['max_my_streak']}")
    print(f"  最长连失: {streaks2['max_opp_streak']}")
    print(f"  连胜分段: {streaks2['my_streaks']}")
    print(f"  连失分段: {streaks2['opp_streaks']}")

    anti2 = calc_anti_pressure(games2, my_team_type=2, game_score_cap=21)
    print(f"\n📊 抗压 (S=50+3.5D-2.5L+20R+15K-3E)")
    print(f"  综合抗压得分: {anti2['overall']}")
    for g in anti2["games"]:
        print(f"  第{g['round']}局: 比分 {g['my_score']}:{g['opp_score']}, "
              f"D={g['D']}, L={g['L']}, R={g['R']}, K={g['K']}, E={g['E']}, "
              f"S={g['S']}")

    field2 = calc_field_adaptability(games2, my_team_type=2)
    print(f"\n📊 场区适应性")
    print(f"  综合场区得分: {field2['overall']}")
    for g in field2["games"]:
        print(f"  第{g['round']}局: 前半段 {g['A_my_score']}:{g['A_opp_score']}, "
              f"后半段 {g['B_my_score']}:{g['B_opp_score']}, "
              f"ΔP={g['delta_P']}, ΔE={g['delta_E']}, ΔO={g['delta_O']}, "
              f"得分={g['field_score']}")

    # 汇总雷达图
    print("\n" + "=" * 70)
    print("📡 六维雷达图最终分数汇总")
    print("=" * 70)
    print(f"{'维度':<16} {'霍冠达':<12} {'王昭':<12}")
    print("-" * 40)
    print(f"{'进攻':<16} {ods['offense']:<12} {ods2['offense']:<12}")
    print(f"{'防守':<16} {ods['defense']:<12} {ods2['defense']:<12}")
    print(f"{'发球':<16} {ods['serve']:<12} {ods2['serve']:<12}")
    print(f"{'接发':<16} {ods['receive']:<12} {ods2['receive']:<12}")
    print(f"{'抗压':<16} {anti['overall']:<12} {anti2['overall']:<12}")
    print(f"{'场区':<16} {field['overall']:<12} {field2['overall']:<12}")
    print(f"{'连续得分':<16} {streaks['avg_consecutive_score']:<12} {streaks2['avg_consecutive_score']:<12}")
    print(f"{'连续失分':<16} {streaks['avg_consecutive_lose']:<12} {streaks2['avg_consecutive_lose']:<12}")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()