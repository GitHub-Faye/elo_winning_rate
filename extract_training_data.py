"""从数据库提取 Elo 训练数据

从 motion_event_layout_stage_battle 和 motion_event_apply_user_setting 中
提取历史比赛记录，用于 EloConfig 参数优化训练。

数据链路:
  battle.player_one_name + battle.event_id
    → user_setting.name + user_setting.event_id
    → user_setting.card_code (选手唯一标识)

用法:
    python extract_training_data.py --output training_data.json
    python extract_training_data.py --output training_data.json --limit 5000
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import AsyncSessionLocal


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────


@dataclass
class PlayerInfo:
    """单个选手信息"""
    card_code: str
    name: str


@dataclass
class TrainingMatch:
    """一场比赛的训练数据"""
    event_id: int
    battle_id: int
    event_index: int
    battle_time: Optional[str]
    project_type: int  # 1=单打 2=双打

    # 选手信息
    team_a: list[PlayerInfo]  # 单打1人，双打2人
    team_b: list[PlayerInfo]

    # 比分
    score_a: int
    score_b: int

    # 结果
    winner_side: str  # "A" 或 "B"


# ──────────────────────────────────────────────
# SQL 查询
# ──────────────────────────────────────────────

# 主查询：提取可匹配 card_code 的比赛（去重）
EXTRACT_SQL = text("""
SELECT
    b.event_id,
    b.battle_id,
    b.event_index,
    b.battle_time,
    b.project_type,
    b.player_one_name,
    b.player_two_name,
    b.player_one_score,
    b.player_two_score,
    us1.card_code AS player_one_card,
    us2.card_code AS player_two_card
FROM motion_event_layout_stage_battle b
JOIN (
    SELECT event_id, name, MIN(card_code) AS card_code
    FROM motion_event_apply_user_setting
    WHERE card_code IS NOT NULL AND card_code != ''
    GROUP BY event_id, name
) us1
    ON b.player_one_name = us1.name
    AND b.event_id = us1.event_id
JOIN (
    SELECT event_id, name, MIN(card_code) AS card_code
    FROM motion_event_apply_user_setting
    WHERE card_code IS NOT NULL AND card_code != ''
    GROUP BY event_id, name
) us2
    ON b.player_two_name = us2.name
    AND b.event_id = us2.event_id
WHERE b.status = 2
    AND b.is_empty = 0
    AND b.player_one_name IS NOT NULL
    AND b.player_two_name IS NOT NULL
    AND b.player_one_score != b.player_two_score
    AND b.player_one_score >= 0 AND b.player_two_score >= 0
GROUP BY b.battle_id
ORDER BY b.event_id, b.event_index
LIMIT :limit
""")


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────


def _parse_players(name_str: str, card_str: str) -> list[PlayerInfo]:
    """解析选手姓名和身份证号（处理双打逗号分隔）。

    双打时 name_str="张三,李四", card_str="110101...,110102..."
    单打时 name_str="张三", card_str="110101..."
    """
    names = [n.strip() for n in name_str.split(",") if n.strip()]
    cards = [c.strip() for c in card_str.split(",") if c.strip()]

    # 如果 card 数量不够，用第一个 card 填充
    while len(cards) < len(names):
        cards.append(cards[0] if cards else "UNKNOWN")

    return [
        PlayerInfo(card_code=cards[i], name=names[i])
        for i in range(len(names))
    ]


def _determine_winner(score_a: int, score_b: int) -> str:
    """根据比分判断获胜方"""
    return "A" if score_a > score_b else "B"


# ──────────────────────────────────────────────
# 提取主函数
# ──────────────────────────────────────────────


async def extract_training_matches(
    limit: int = 50000,
) -> list[TrainingMatch]:
    """从数据库提取训练数据。

    Args:
        limit: 最大提取场次数。

    Returns:
        按 event_id + event_index 排序的比赛列表。
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(EXTRACT_SQL, {"limit": limit})
        rows = result.fetchall()

    matches: list[TrainingMatch] = []
    skipped = 0

    for row in rows:
        try:
            team_a = _parse_players(row.player_one_name, row.player_one_card)
            team_b = _parse_players(row.player_two_name, row.player_two_card)

            match = TrainingMatch(
                event_id=row.event_id,
                battle_id=row.battle_id,
                event_index=row.event_index,
                battle_time=str(row.battle_time) if row.battle_time else None,
                project_type=row.project_type or 1,
                team_a=team_a,
                team_b=team_b,
                score_a=row.player_one_score,
                score_b=row.player_two_score,
                winner_side=_determine_winner(row.player_one_score, row.player_two_score),
            )
            matches.append(match)
        except Exception as e:
            skipped += 1
            if skipped <= 5:
                print(f"  [WARN] 跳过 battle_id={row.battle_id}: {e}", file=sys.stderr)

    print(f"提取完成: {len(matches)} 场比赛, 跳过 {skipped} 场", file=sys.stderr)
    return matches


# ──────────────────────────────────────────────
# 统计信息
# ──────────────────────────────────────────────


def print_stats(matches: list[TrainingMatch]) -> None:
    """打印数据集统计信息"""
    events = set()
    players = set()
    singles = 0
    doubles = 0

    for m in matches:
        events.add(m.event_id)
        for p in m.team_a + m.team_b:
            players.add(p.card_code)
        if m.project_type == 1:
            singles += 1
        else:
            doubles += 1

    print(f"\n{'='*50}", file=sys.stderr)
    print(f"数据集统计:", file=sys.stderr)
    print(f"  比赛总数: {len(matches)}", file=sys.stderr)
    print(f"  赛事数量: {len(events)}", file=sys.stderr)
    print(f"  选手数量: {len(players)}", file=sys.stderr)
    print(f"  单打场次: {singles}", file=sys.stderr)
    print(f"  双打场次: {doubles}", file=sys.stderr)
    print(f"{'='*50}\n", file=sys.stderr)


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="提取 Elo 训练数据")
    parser.add_argument(
        "--output", "-o",
        default="training_data.json",
        help="输出文件路径 (默认: training_data.json)",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=50000,
        help="最大提取场次数 (默认: 50000)",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="只打印统计信息，不保存文件",
    )
    args = parser.parse_args()

    import asyncio

    async def _run():
        matches = await extract_training_matches(limit=args.limit)
        print_stats(matches)

        if not args.stats_only:
            # 转为可序列化的 dict
            data = [asdict(m) for m in matches]
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"已保存到 {args.output}", file=sys.stderr)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
