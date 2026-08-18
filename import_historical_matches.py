#!/usr/bin/env python
"""把数据库既有历史比赛导入当前 Elo 业务库

数据源: `motion_event_layout_stage_battle`（对阵表）+ `motion_event_apply_user_setting`（报名人表）
目标:   `elo_match_record`（每人每场一条） + `elo_player_rating`（各选手当前积分）

核心链路: 对阵的对阵人员ID（player_*_user_ids，冗余的一行/多行 user_setting_id）
  → JOIN `motion_event_apply_user_setting.user_setting_id` 拿 card_code（身份证号）
  单打一行、双打拆逗号的四行，逐个 JOIN。card_code 全部拿到才导入，缺任一位跳过该场。

`user_setting_id` 是什么：
  它是旧运动平台「报名表」motion_event_apply_user_setting 里，每次「报名事件」的唯一主键，
  不是人的唯一标识。一个人在一场赛事报名一次 = 一行 user_setting_id；
  对阵表 motion_event_layout_stage_battle 的 player_*_user_ids 冗余存放该场对阵的
  user_setting_id（单打 1 个、双打 2 个、逗号拼接）。
  因此 user_setting_id 是在对阵表与报名表之间精确对齐到身份证号的桥（全局唯一、无重复），
  不能靠 name（姓名）对齐——同名异人 / 异名同人 / "wangb王兵" 之类脏名字都存在。

一个绕不开的坑：
  user_setting_id 定位的是一次报名，不是一个 IR人 —— 同一个 card_code 可能登记在多个
  user_setting_id 下（同人重复报名 / 被录入两次）。这会导致：
    - 双打里两个 user_setting_id 的 card_code 相同（如田蒲军/魏兴荣都指向同一身份证号）
      → elo_player_rating 主键冲突；
    - 跨场续跑时 card_code 撞到已存在的行 → UPDATE 0-matched。
  因此脚本对每场强制「同一场参赛选手的 card_code 必须互不相同」，SQL 层与 Python 层双重过滤，
  把两次报名当成两个人导入的脏对阵直接跳过。

重放策略:
  - 按 battle_time 升序逐场重放，保证 Elo 积分累计顺序与真实赛程一致。
  - 复用现业务 EloService.record_match()（集成 best_config.json 的最优参数）。
  - event_weight 统一 1.0，team 由 card_code 定位，单/双打自动判定。

用法:
    python import_historical_matches.py                 # 清空两张表后全量重放
    python import_historical_matches.py --keep          # 不清表，仅重放（用于续跑/补导）
    python import_historical_matches.py --limit 200     # 只导先 200 场（试跑）
    python import_historical_matches.py --dry-run       # 只统计可导入场次，不写库
    python import_historical_matches.py --pretty        # 打印明细日志
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 压掉 SQLAlchemy 的 INFO 回显（core.database 的 engine 设了 echo=True，
# 全量重放时逐条 SQL 会穿插在进度条里干扰显示）。仅对本脚本生效，不影响其它模块。
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

from core.database import DATABASE_URL, AsyncSessionLocal
from core.schemas import EloRecordRequest
from services.elo_service import EloService

ROOT = Path(__file__).parent


# ──────────────────────────────────────────────
# 最优 Elo 参数加载（best_config.json）
# ──────────────────────────────────────────────

def load_best_config() -> dict:
    """读取 best_config.json；不存在则回退空 dict（用 EloConfig 默认值）。"""
    cfg_path = ROOT / "best_config.json"
    if not cfg_path.exists():
        print("  [WARN] 未找到 best_config.json，使用 elo_compute.EloConfig 默认参数", file=sys.stderr)
        return {}
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [WARN] 读取 best_config.json 失败，使用默认参数: {e}", file=sys.stderr)
        return {}


# ──────────────────────────────────────────────
# 提取可精确对齐身份证号的比赛
# ──────────────────────────────────────────────

EXTRACT_SQL = text("""
-- 单打（project_type=1）：player_*_user_ids 各一行，直接 JOIN 拿 card_code
SELECT
    b.event_id,
    b.battle_id,
    b.event_index        AS source_order,
    b.battle_time,
    b.player_one_score   AS score_one,
    b.player_two_score   AS score_two,
    'singles'            AS match_type,
    u1.card_code         AS one_c1,
    /* 单打: one_c1=A方选手, two_c1=B方选手 */
    NULL                 AS one_c2,
    NULL                 AS one_c3,
    NULL                 AS one_c4,
    u2.card_code         AS two_c1,
    NULL                 AS two_c2,
    NULL                 AS two_c3,
    NULL                 AS two_c4
FROM motion_event_layout_stage_battle b
JOIN motion_event_apply_user_setting u1
    ON u1.user_setting_id = CAST(b.player_one_user_ids AS UNSIGNED)
    AND u1.event_id = b.event_id
    AND u1.card_code REGEXP '^[0-9]{17}[0-9Xx]$'
JOIN motion_event_apply_user_setting u2
    ON u2.user_setting_id = CAST(b.player_two_user_ids AS UNSIGNED)
    AND u2.event_id = b.event_id
    AND u2.card_code REGEXP '^[0-9]{17}[0-9Xx]$'
WHERE b.status = 2
    AND b.is_empty = 0
    AND b.project_type = 1
    AND b.player_one_score != b.player_two_score
    AND b.player_one_score >= 0 AND b.player_two_score >= 0
    AND b.player_one_user_ids IS NOT NULL AND b.player_one_user_ids != ''
    AND b.player_two_user_ids IS NOT NULL AND b.player_two_user_ids != ''
    AND b.player_one_user_ids NOT LIKE '%,%'      -- 单打必须单 user_setting_id
    AND b.player_two_user_ids NOT LIKE '%,%'

UNION ALL

-- 双打（project_type=2）：player_*_user_ids 为"id1,id2"，拆逗号分别 JOIN
SELECT
    b.event_id,
    b.battle_id,
    b.event_index        AS source_order,
    b.battle_time,
    b.player_one_score   AS score_one,
    b.player_two_score   AS score_two,
    'doubles'            AS match_type,
    u1.card_code         AS one_c1,
    u2.card_code         AS one_c2,
    NULL                 AS one_c3,
    NULL                 AS one_c4,
    b1.card_code         AS two_c1,
    b2.card_code         AS two_c2,
    NULL                 AS two_c3,
    NULL                 AS two_c4
FROM motion_event_layout_stage_battle b
JOIN motion_event_apply_user_setting u1
    ON u1.user_setting_id = CAST(SUBSTRING_INDEX(b.player_one_user_ids, ',', 1) AS UNSIGNED)
    AND u1.event_id = b.event_id
    AND u1.card_code REGEXP '^[0-9]{17}[0-9Xx]$'
JOIN motion_event_apply_user_setting u2
    ON u2.user_setting_id = CAST(SUBSTRING_INDEX(b.player_one_user_ids, ',', -1) AS UNSIGNED)
    AND u2.event_id = b.event_id
    AND u2.card_code REGEXP '^[0-9]{17}[0-9Xx]$'
JOIN motion_event_apply_user_setting b1
    ON b1.user_setting_id = CAST(SUBSTRING_INDEX(b.player_two_user_ids, ',', 1) AS UNSIGNED)
    AND b1.event_id = b.event_id
    AND b1.card_code REGEXP '^[0-9]{17}[0-9Xx]$'
JOIN motion_event_apply_user_setting b2
    ON b2.user_setting_id = CAST(SUBSTRING_INDEX(b.player_two_user_ids, ',', -1) AS UNSIGNED)
    AND b2.event_id = b.event_id
    AND b2.card_code REGEXP '^[0-9]{17}[0-9Xx]$'
WHERE b.status = 2
    AND b.is_empty = 0
    AND b.project_type = 2
    AND b.player_one_score != b.player_two_score
    AND b.player_one_score >= 0 AND b.player_two_score >= 0
    AND b.player_one_user_ids IS NOT NULL AND b.player_one_user_ids != ''
    AND b.player_two_user_ids IS NOT NULL AND b.player_two_user_ids != ''
    AND (b.player_one_user_ids LIKE '%,%' OR b.player_one_user_ids LIKE '%,')  -- 双打须双 id
    AND (b.player_two_user_ids LIKE '%,%' OR b.player_two_user_ids LIKE '%,')
    /* 同一方两名 user_setting_id 必须不同（否则 SUBSTRING_INDEX 两个位置返回同一 id，
       JOIN 到同一 card_code，导致 elo_player_rating 主键冲突 / Elo 积分错乱）。 */
    AND CAST(SUBSTRING_INDEX(b.player_one_user_ids, ',', 1) AS UNSIGNED)
      < CAST(SUBSTRING_INDEX(b.player_one_user_ids, ',', -1) AS UNSIGNED)
    AND CAST(SUBSTRING_INDEX(b.player_two_user_ids, ',', 1) AS UNSIGNED)
      < CAST(SUBSTRING_INDEX(b.player_two_user_ids, ',', -1) AS UNSIGNED)
    /* 同一方两名用户ID必须不同（SUBSTRING_INDEX 前/后不同 id 才可能不同 card_code）。 */
    AND CAST(SUBSTRING_INDEX(b.player_one_user_ids, ',', 1) AS UNSIGNED)
      != CAST(SUBSTRING_INDEX(b.player_one_user_ids, ',', -1) AS UNSIGNED)
    AND CAST(SUBSTRING_INDEX(b.player_two_user_ids, ',', 1) AS UNSIGNED)
      != CAST(SUBSTRING_INDEX(b.player_two_user_ids, ',', -1) AS UNSIGNED)
    /* 全部 4 个 card_code 必须互不相同（否则同一人不同 user_setting_id 登记了相同身份证号，
       导致 elo_player_rating 主键冲突 / Elo 积分错乱）。 */
    AND u1.card_code != u2.card_code
    AND b1.card_code != b2.card_code
    AND u1.card_code != b1.card_code AND u1.card_code != b2.card_code
    AND u2.card_code != b1.card_code AND u2.card_code != b2.card_code
""")


@dataclass
class ImportedMatch:
    """一场已解析、待重放的重排比赛。"""

    battle_id: int
    match_type: str          # 'singles' / 'doubles' — 仅用于统计显示


async def extract_importable_matches(
    session: AsyncSession,
    limit: int | None = None,
) -> list[ImportedMatch]:
    """从数据库提取可精确对齐身份证号的比赛，按 battle_time 升序。

    Args:
        session: 只读会话（用于提取）。
        limit: 最大提取场次（None = 全部）。

    返回:
        自然顺序（battle_time 升序，为 NULL 排最后）匹配列表。
    """
    # Step 1: 获取所有符合条件的 battle_id
    stmt_battles = text("""
        SELECT battle_id, project_type
        FROM motion_event_layout_stage_battle
        WHERE status = 2
          AND is_empty = 0
          AND player_one_score != player_two_score
          AND player_one_score >= 0 AND player_two_score >= 0
        ORDER BY battle_time
    """)
    if limit:
        stmt_battles = text(f"SELECT * FROM ({stmt_battles}) t LIMIT :limit")

    result = await session.execute(stmt_battles, {"limit": limit} if limit else {})
    rows = result.fetchall()

    matches = []
    for row in rows:
        battle_id = row[0]
        project_type = row[1]
        match_type = "singles" if project_type == 1 else "doubles"
        matches.append(ImportedMatch(
            battle_id=battle_id,
            match_type=match_type,
        ))

    return matches


# ──────────────────────────────────────────────
# 重放
# ──────────────────────────────────────────────

async def replay_matches(
    session: AsyncSession,
    matches: list[ImportedMatch],
    config: dict,
    pretty: bool = False,
) -> dict:
    """按顺序逐场调用 EloService.record_match() 重放，返回统计。

    默认模式：每场用 `\\r` 覆盖方式刷新实时百分比进度条；
    pretty=True 时改为逐场打印一行明细（带进度标注）。
    """
    service = EloService(session)
    service.config = _config_from_dict(config)   # 注入最优参数

    total = len(matches)
    played = 0
    errors: list[str] = []
    singles = 0
    doubles = 0
    t0 = time.time()

    def _render_progress(i: int) -> str:
        """渲染覆盖式进度条：百分比 + 已处理/总数 + 速率 + 已用时间。"""
        el = time.time() - t0
        if i <= 0:
            rate = 0.0
        else:
            rate = i / el if el > 0 else 0.0
        pct = 100.0 * i / total if total else 0.0
        bar_w = 30
        filled = int(bar_w * i / total) if total else bar_w
        bar = "#" * filled + "-" * (bar_w - filled)
        # 尾随空格避免进度条覆盖上次残留字符
        return f"\r  [{bar}] {pct:5.1f}%  {i}/{total}  {rate:5.1f}场/s  {el:6.1f}s   "

    for i, m in enumerate(matches, 1):
        try:
            req = EloRecordRequest(
                battle_id=m.battle_id,
                event_weight=1.0,
            )
            await service.record_match(req)
            played += 1
            if m.match_type == "singles":
                singles += 1
            else:
                doubles += 1
            if pretty:
                print(_render_progress(i), flush=True)
                print(f"  ✓ {m.match_type} battle#{m.battle_id}", flush=True)
            else:
                print(_render_progress(i), end="", flush=True)
        except Exception as e:   # noqa: BLE001 —— 单场失败不阻断整体重放
            errors.append(f"battle#{m.battle_id}: {e}")
            # 失败时不刷新进度条序号行，另起一行打印错误，避免与进度条粘连
            print("", flush=True)
            print(f"  [ERR] battle#{m.battle_id}: {e}", file=sys.stderr, flush=True)
            # 该场事务已回滚，session 进入 failed 状态，需显式 reset 才能继续下一场
            try:
                await session.rollback()
            except Exception:
                pass
            service = EloService(session)
            service.config = _config_from_dict(config)

    # 收尾：进度条最后换行 + 最终统计
    if total and not pretty:
        print("", flush=True)
    if errors:
        print(f"\n  ⚠ {len(errors)} 场重放失败（因积分会跳跃，建议处理后从最新断点续导）", file=sys.stderr)
        for e in errors[:10]:
            print(f"      {e}", file=sys.stderr)

    return {
        "played": played,
        "singles": singles,
        "doubles": doubles,
        "errors": len(errors),
        "elapsed": time.time() - t0,
    }


def _config_from_dict(data: dict):
    """把 best_config dict -> EloConfig 实例（缺省字段用默认值）。"""
    from elo_compute import EloConfig
    valid_fields = {f for f in EloConfig.__dataclass_fields__}
    kwargs = {k: v for k, v in data.items() if k in valid_fields}
    return EloConfig(**kwargs)


# ──────────────────────────────────────────────
# 清表
# ──────────────────────────────────────────────

async def wipe_tables(session: AsyncSession) -> None:
    """清空 elo_match_record 与 elo_player_rating（整体重放前调用）。"""
    await session.execute(text("DELETE FROM elo_match_record"))
    await session.execute(text("DELETE FROM elo_player_rating"))
    await session.commit()
    print("  ✓ 已清空 elo_match_record 与 elo_player_rating")


# ──────────────────────────────────────────────
# 统计打印
# ──────────────────────────────────────────────

async def print_summary(session: AsyncSession, matched: int) -> None:
    """打印导入后业务库的最终状态。"""
    r1 = await session.execute(text(
        "SELECT COUNT(*) FROM elo_match_record"
    ))
    r2 = await session.execute(text(
        "SELECT COUNT(*) FROM elo_player_rating"
    ))
    r3 = await session.execute(text(
        "SELECT event_id, COUNT(*) FROM elo_match_record"
    ))
    r4 = await session.execute(text(
        "SELECT card_code, rating, games, wins, losses "
        "FROM elo_player_rating ORDER BY rating DESC LIMIT 15"
    ))
    print(f"\n{'=' * 60}")
    print(f"导入结果: 可匹配 {matched} 场当前业务库")
    print(f"  elo_match_record  = {r1.scalar()} 条")
    print(f"  elo_player_rating = {r2.scalar()} 名选手")
    print("\n按赛事分布:")
    for row in r3:
        print(f"    event#{row[0]:<6} {row[1]} 条")
    print("\n积分榜 Top15:")
    for row in r4:
        print(f"    {row[0]}  rating={float(row[1]):>8.2f}  games={row[2]:>3}  "
              f"wins={row[3]:>3}  losses={row[4]:>3}")
    print(f"{'=' * 60}")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="把数据库既有历史比赛导入当前 Elo 业务库（user_setting_id→card_code 精确对齐，按时间重放）",
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="不清空现有 elo_match_record/elo_player_rating，直接追加重放（默认会清空）",
    )
    parser.add_argument(
        "--limit", "-l", type=int, default=None,
        help="只抽取前 N 场（按 battle_time），用于试跑/断点续导 (默认全部)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只统计可导入场次并打印，不写库、不清表",
    )
    parser.add_argument(
        "--config", default=str(ROOT / "best_config.json"),
        help="最佳 Elo 参数 JSON 路径（默认 best_config.json）",
    )
    parser.add_argument(
        "--pretty", "-v", action="store_true",
        help="逐场打印重放明细",
    )
    args = parser.parse_args()

    # 读 best_config（用于重放前确认，实际注入在 replay_matches）
    config = load_best_config()

    async def _run():
        async with AsyncSessionLocal() as session:
            print("⏳ 正在提取可精确对齐身份证号的历史比赛...")
            matches = await extract_importable_matches(session, args.limit)
            print(f"  提取到 {len(matches)} 场可导入比赛 "
                  f"({sum(1 for m in matches if m.match_type=='singles')} 单打 / "
                  f"{sum(1 for m in matches if m.match_type=='doubles')} 双打)")

            if args.dry_run:
                print("\n[dry-run] 仅统计，未写库。")
                if not args.keep:
                    print("  （说明：正式导入会清空 elo_match_record 与 elo_player_rating）")
                # 预览前 5 场
                for m in matches[:5]:
                    print(f"  {m.match_type} event#{m.event_id} battle#{m.battle_id} "
                          f"{m.score_a}-{m.score_b}  A={m.team_a} B={m.team_b}")
                return

            if not args.keep:
                print("\n⏳ 清空现有业务库（整体重放）...")
                await wipe_tables(session)
            else:
                print("\n[keep] 保留现有业务库数据，追加重放...")

            print(f"\n⏳ 开始重放，共 {len(matches)} 场"
                  f"（EloConfig 参数来自 {args.config}）...")
            stats = await replay_matches(session, matches, config, pretty=args.pretty)

            print(f"\n重放完成: 成功 {stats['played']} / 共 {len(matches)} 场，"
                  f"单打 {stats['singles']}，双打 {stats['doubles']}，"
                  f"失败 {stats['errors']}，耗时 {stats['elapsed']:.1f}s")

        # 用新会话打印最终状态（session 已关闭）
        async with AsyncSessionLocal() as summary_session:
            await print_summary(summary_session, matched=len(matches))

    asyncio.run(_run())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n中断。请用 --keep 从断点继续重放（已重放场次已提交）。", file=sys.stderr)
        sys.exit(1)
