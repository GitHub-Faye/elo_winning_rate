"""Elo 账号动态聚合路由服务

同一人可能用多个手机号/身份证在 Elo 表里开了多个账号。本模块不缓存任何映射，
每次动态地以输入身份键在 `elo_player_rating` 里找该人名下所有账号，返回其中
**Elo 分最高的那个账号**（player_key）作为查询展示 / 写入累加的目标键：

  手机号查：取该手机号账号；若它有身份证(card_code)，则聚合同等证的所有账号取最高；
            否则返回手机号自己账号。
  身份证查：直接聚合所有 `card_code = 该证` 的账号取最高。
  写入同理：新 Elo 记录绑定到「最高分账号」上（同一人所有比赛累加于其最高分账号）。

口径说明：返回的是"名下最好的一本账"，不是"全网合并积分"；低分账号不再展示、不再
累加（视为哨兵/历史账本）。这是产品确定的口径。

依赖：`elo_player_rating.card_code`（身份证参考字段）正确回填，才能按证聚合到同一人
的所有账号（重放时由应用表同行身份证自动带出）。
"""
from __future__ import annotations

from typing import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import EloPlayerRating

# 当前运动品类（与 services 层一致，后续可扩展为多品类）
CURRENT_SPORT = "badminton"


def _pick_best(rows: list, want_id: str | None = None):
    """在候选账号中取 Elo 最高者；并列时优先场次多、再优先主键即身份证、再按 key 稳定。

    Args:
        rows: 同一人的候选账号（EloPlayerRating）
        want_id: 期望作为主账号的身份证号（并列时优先 player_key == want_id 的账号）
    """
    if not rows:
        return None
    return max(rows, key=lambda r: (
        float(r.rating),            # 1) Elo 分高者胜
        r.games,                    # 2) 场次多者胜（更"成型"）
        1 if want_id and r.player_key == want_id else 0,  # 3) 主键即身份证者胜
        r.player_key,               # 4) 稳定兜底
    ))


async def resolve_to_primary(
    db: AsyncSession,
    keys: Sequence[str],
    sport_type: str = CURRENT_SPORT,
) -> dict[str, str]:
    """把输入身份键批量路由到其"最高分账号"的 player_key。

    Args:
        db: 数据库会话
        keys: 输入身份键（身份证号或手机号）
        sport_type: 运动品类

    Returns:
        {查询键: 最高分账号的 player_key}。找不到任何账号时返回键自身。
    """
    uniq = list(dict.fromkeys(k for k in keys if k))
    if not uniq:
        return {}

    # 一次性拉取所有命中账号：player_key 命中（手机号自账号 / 身份证自账号）
    # 或 card_code 命中（同证下其它手机号生成的分身账号）
    rows = (await db.execute(
        select(EloPlayerRating).where(
            or_(
                EloPlayerRating.player_key.in_(uniq),
                EloPlayerRating.card_code.in_(uniq),
            ),
            EloPlayerRating.sport_type == sport_type,
        )
    )).scalars().all()

    # 惰性导入，避免与 battle_card_service 的顶层 import 形成循环
    from services.battle_card_service import is_valid_id

    by_pk = {r.player_key: r for r in rows}
    by_card: dict[str, list] = {}
    for r in rows:
        if r.card_code:
            by_card.setdefault(r.card_code, []).append(r)

    result: dict[str, str] = {}
    for k in uniq:
        own = by_pk.get(k)
        if is_valid_id(k):
            # 身份证：聚合该证下所有账号（含主键即该证的账号）
            candidates = list(by_card.get(k, []))
            if own and own not in candidates:
                candidates.append(own)
            best = _pick_best(candidates, want_id=k)
            result[k] = best.player_key if best else k
        elif own and own.card_code:
            # 手机号：有身份证 → 聚合同证下所有账号
            candidates = list(by_card.get(own.card_code, []))
            best = _pick_best(candidates, want_id=own.card_code)
            result[k] = best.player_key if best else k
        else:
            # 手机号：无身份证 → 返回手机号自己的账号（或键自身）
            result[k] = own.player_key if own else k
    return result