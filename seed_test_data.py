"""
插入虚构比赛数据到真实数据库进行测试（身份证号作为选手定位键）：
1. 先直接 INSERT 选手初始 rating（card_code 为主键）
2. 再调用 POST /api/v1/elo/record 写入比赛记录（通过 Elo 计算自动生成 match_record + 更新 rating）

选手以身份证号（card_code）定位，未注册用户同样适用。
"""
import asyncio
import json
import os
import subprocess
import sys
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── DB 连接 ──
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

# ── 选手定义（身份证号 → (姓名, 初始分)） ──
PLAYERS = {
    # 张三
    "110101199001011234": ("张三", 1500),
    # 李四
    "110101199202024567": ("李四", 1500),
    # 王五
    "110101199303036789": ("王五", 1500),
    # 赵六
    "110101199404041122": ("赵六", 1500),
    # 钱七
    "110101199505055566": ("钱七", 1500),
    # 孙八
    "110101199606067788": ("孙八", 1500),
    # 周九
    "110101199707079900": ("周九", 1500),
    # 吴十
    "110101199808080011": ("吴十", 1500),
}

# 简写映射：便于下面 MATCHES 用短名
P = {name: card for card, (name, _) in PLAYERS.items()}


# ── 虚构比赛结果 ──
# (event_id, battle_id, score_a, score_b, team_a, team_b)  — team 为身份证号列表
MATCHES = [
    # ── 单打：让选手之间建立关系 ──
    # 张三 vs 李四
    (1, 1, 21, 15, [P["张三"]], [P["李四"]]),
    (1, 2, 18, 21, [P["张三"]], [P["李四"]]),
    (1, 3, 21, 19, [P["张三"]], [P["李四"]]),
    # 王五 vs 赵六
    (1, 4, 21, 10, [P["王五"]], [P["赵六"]]),
    (1, 5, 21, 12, [P["王五"]], [P["赵六"]]),
    # 张三 vs 王五
    (1, 6, 21, 17, [P["张三"]], [P["王五"]]),
    (1, 7, 15, 21, [P["张三"]], [P["王五"]]),
    # 李四 vs 赵六
    (1, 8, 21, 14, [P["李四"]], [P["赵六"]]),
    # 钱七 vs 孙八
    (2, 1, 21, 8, [P["钱七"]], [P["孙八"]]),
    (2, 2, 19, 21, [P["钱七"]], [P["孙八"]]),
    (2, 3, 21, 11, [P["钱七"]], [P["孙八"]]),
    # 钱七 vs 张三
    (2, 4, 21, 15, [P["钱七"]], [P["张三"]]),
    (2, 5, 14, 21, [P["钱七"]], [P["张三"]]),
    # 孙八 vs 李四
    (2, 6, 21, 18, [P["孙八"]], [P["李四"]]),
    # 周九 vs 吴十
    (3, 1, 21, 20, [P["周九"]], [P["吴十"]]),
    (3, 2, 10, 21, [P["周九"]], [P["吴十"]]),
    (3, 3, 21, 13, [P["周九"]], [P["吴十"]]),

    # ── 双打比赛，建立跨队关系 ──
    (4, 1, 22, 20, [P["张三"], P["王五"]], [P["李四"], P["赵六"]]),
    (4, 2, 21, 15, [P["钱七"], P["周九"]], [P["孙八"], P["吴十"]]),
    (4, 3, 18, 21, [P["张三"], P["钱七"]], [P["王五"], P["周九"]]),
]


async def seed():
    print("清空旧数据...")
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM elo_match_record"))
        await conn.execute(text("DELETE FROM elo_player_rating"))

    print("插入选手初始 rating...")
    async with engine.begin() as conn:
        for card, (name, rating) in PLAYERS.items():
            await conn.execute(
                text("""
                    INSERT INTO elo_player_rating
                        (card_code, sport_type, rating, games, wins, losses, draws, highest_rating, lowest_rating)
                    VALUES (:card, 'badminton', :rating, 0, 0, 0, 0, :rating, :rating)
                """),
                {"card": card, "rating": rating},
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
        r3 = await conn.execute(text("SELECT card_code, rating, games, wins, losses FROM elo_player_rating ORDER BY card_code"))
        print(f"\n最终状态: match_record={r1.scalar()} 条, player_rating={r2.scalar()} 条")
        print("选手 rating 一览:")
        for row in r3:
            print(f"  card={row[0]}  rating={float(row[1]):>8.2f}  games={row[2]}  wins={row[3]}  losses={row[4]}")

    engine.dispose()


asyncio.run(seed())
