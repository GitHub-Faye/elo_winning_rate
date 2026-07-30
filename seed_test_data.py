"""
插入虚构比赛数据到真实数据库进行测试：
1. 先直接 INSERT 选手初始 rating
2. 再调用 POST /api/v1/elo/record 写入比赛记录（通过 Elo 计算自动生成 match_record + 更新 rating）
"""
import asyncio
import subprocess
import time
import json
import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from decimal import Decimal

# ── DB 连接 ──
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

# ── 选手定义 ──
PLAYERS = {
    1001: ("张三", 1500),
    1002: ("李四", 1500),
    1003: ("王五", 1500),
    1004: ("赵六", 1500),
    1005: ("钱七", 1500),
    1006: ("孙八", 1500),
    1007: ("周九", 1500),
    1008: ("吴十", 1500),
}

# ── 虚构比赛结果 ──
# (event_id, battle_id, score_a, score_b, team_a, team_b)
MATCHES = [
    # ── 单打：让选手之间建立关系 ──
    # 张三 vs 李四
    (1, 1, 21, 15, [1001], [1002]),
    (1, 2, 18, 21, [1001], [1002]),
    (1, 3, 21, 19, [1001], [1002]),
    # 王五 vs 赵六
    (1, 4, 21, 10, [1003], [1004]),
    (1, 5, 21, 12, [1003], [1004]),
    # 张三 vs 王五
    (1, 6, 21, 17, [1001], [1003]),
    (1, 7, 15, 21, [1001], [1003]),
    # 李四 vs 赵六
    (1, 8, 21, 14, [1002], [1004]),
    # 钱七 vs 孙八
    (2, 1, 21, 8, [1005], [1006]),
    (2, 2, 19, 21, [1005], [1006]),
    (2, 3, 21, 11, [1005], [1006]),
    # 钱七 vs 张三
    (2, 4, 21, 15, [1005], [1001]),
    (2, 5, 14, 21, [1005], [1001]),
    # 孙八 vs 李四
    (2, 6, 21, 18, [1006], [1002]),
    # 周九 vs 吴十
    (3, 1, 21, 20, [1007], [1008]),
    (3, 2, 10, 21, [1007], [1008]),
    (3, 3, 21, 13, [1007], [1008]),

    # ── 双打比赛，建立跨队关系 ──
    (4, 1, 22, 20, [1001, 1003], [1002, 1004]),
    (4, 2, 21, 15, [1005, 1007], [1006, 1008]),
    (4, 3, 18, 21, [1001, 1005], [1003, 1007]),
]

async def seed():
    print("清空旧数据...")
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM elo_match_record"))
        await conn.execute(text("DELETE FROM elo_player_rating"))

    print("插入选手初始 rating...")
    async with engine.begin() as conn:
        for uid, (name, rating) in PLAYERS.items():
            await conn.execute(
                text("""
                    INSERT INTO elo_player_rating
                        (user_id, sport_type, rating, games, wins, losses, draws, highest_rating, lowest_rating)
                    VALUES (:uid, 'badminton', :rating, 0, 0, 0, 0, :rating, :rating)
                """),
                {"uid": uid, "rating": rating},
            )
    print(f"  已插入 {len(PLAYERS)} 名选手")

    print("\n写入比赛记录（走 API）...")
    total = 0
    for event_id, battle_id, score_a, score_b, team_a, team_b in MATCHES:
        payload = {
            "event_id": event_id,
            "battle_id": battle_id,
            "source_order": battle_id,
            "score_a": score_a,
            "score_b": score_b,
            "team_a": team_a,
            "team_b": team_b,
            "event_weight": 1.0,
        }
        # 用 curl 调用 API
        import subprocess
        r = subprocess.run(
            ["curl", "-s", "-X", "POST", "http://127.0.0.1:8000/api/v1/elo/record",
             "-H", "Content-Type: application/json",
             "-d", json.dumps(payload)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            print(f"  ✗ curl error: {r.stderr}")
            continue
        resp = json.loads(r.stdout)
        if resp.get("success"):
            total += 1
            team = "单打" if len(team_a) == 1 else "双打"
            print(f"  ✓ {team} event#{event_id} battle#{battle_id}  {score_a}-{score_b}  {team_a} vs {team_b}")
        else:
            print(f"  ✗ FAILED event#{event_id} battle#{battle_id}: {r.stdout[:200]}")

    print(f"\n共写入 {total} 场比赛")

    # 验证数据
    async with engine.connect() as conn:
        r1 = await conn.execute(text("SELECT COUNT(*) FROM elo_match_record"))
        r2 = await conn.execute(text("SELECT COUNT(*) FROM elo_player_rating"))
        r3 = await conn.execute(text("SELECT user_id, rating, games, wins, losses FROM elo_player_rating ORDER BY user_id"))
        print(f"\n最终状态: match_record={r1.scalar()} 条, player_rating={r2.scalar()} 条")
        print("选手 rating 一览:")
        for row in r3:
            print(f"  user_id={row[0]:>4}  rating={float(row[1]):>8.2f}  games={row[2]}  wins={row[3]}  losses={row[4]}")

    engine.dispose()

asyncio.run(seed())
