"""identity_service 动态最高分聚合路由测试

resolve_to_primary 不缓存映射，每次动态按身份证聚合同一人所有账号并取 Elo 最高者。
conftest 把各服务模块里的 resolve_to_primary 打成恒等路由；本文件直接测
identity_service 本身，不受影响。
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from services.identity_service import resolve_to_primary

ID = "110101199001011234"
PHONE_A = "13800138000"
PHONE_B = "13900139000"
PHONE_NEW = "13700137000"


def _acc(player_key, card_code, rating, games=0):
    """构造一条 EloPlayerRating（SimpleNamespace）。"""
    return SimpleNamespace(
        player_key=player_key, card_code=card_code, sport_type="badminton",
        rating=Decimal(str(rating)), games=games,
    )


def _db(rows):
    db = AsyncMock(spec=AsyncSession)
    e = MagicMock()
    e.scalars().all.return_value = rows
    db.execute = AsyncMock(return_value=e)
    return db


class TestResolveToPrimary:
    @pytest.mark.asyncio
    async def test_phone_routes_to_highest_sibling_same_card(self):
        """手机号账号带身份证 → 聚合同证下所有账号，取 Elo 最高的。"""
        rows = [
            _acc(PHONE_A, ID, 1500, games=10),   # 低分分身
            _acc(PHONE_B, ID, 1700, games=30),   # 高分主账
        ]
        db = _db(rows)
        res = await resolve_to_primary(db, [PHONE_A])
        assert res == {PHONE_A: PHONE_B}

    @pytest.mark.asyncio
    async def test_phone_without_card_returns_own(self):
        """手机号账号无身份证 → 返回手机号自己的账号。"""
        rows = [_acc(PHONE_A, None, 1550, games=15)]
        db = _db(rows)
        res = await resolve_to_primary(db, [PHONE_A])
        assert res == {PHONE_A: PHONE_A}

    @pytest.mark.asyncio
    async def test_id_aggregates_all_same_card(self):
        """身份证 query → 聚合所有同证账号取最高。"""
        rows = [
            _acc(PHONE_A, ID, 1500, games=10),
            _acc(ID, ID, 1700, games=30),        # 主键即身份证
            _acc(PHONE_B, ID, 1600, games=20),
        ]
        db = _db(rows)
        res = await resolve_to_primary(db, [ID])
        assert res == {ID: ID}

    @pytest.mark.asyncio
    async def test_new_phone_returns_key(self):
        """表里没有的手机号 → 返回键自身。"""
        db = _db([])
        res = await resolve_to_primary(db, [PHONE_NEW])
        assert res == {PHONE_NEW: PHONE_NEW}

    @pytest.mark.asyncio
    async def test_tiebreak_prefers_id_keyed_account(self):
        """同名同分同场次 → 并列时优先主键即身份证的账号。"""
        rows = [
            _acc(PHONE_A, ID, 1600, games=20),
            _acc(ID, ID, 1600, games=20),
        ]
        db = _db(rows)
        res = await resolve_to_primary(db, [ID])
        assert res == {ID: ID}

    @pytest.mark.asyncio
    async def test_mixed_input_and_dedup(self):
        """混合输入：不同身份各自路由到最优账号，重复键去重。"""
        rows = [
            _acc(PHONE_A, ID, 1500, games=10),
            _acc(PHONE_B, ID, 1700, games=30),
            _acc(PHONE_NEW, None, 1550, games=5),
        ]
        db = _db(rows)
        res = await resolve_to_primary(db, [PHONE_A, PHONE_B, PHONE_NEW, PHONE_A])
        assert res == {
            PHONE_A: PHONE_B,   # 同证，取最高
            PHONE_B: PHONE_B,   # 本身就是最高
            PHONE_NEW: PHONE_NEW,
        }

    @pytest.mark.asyncio
    async def test_empty_input(self):
        """空输入 → 空 dict。"""
        db = _db([])
        res = await resolve_to_primary(db, [])
        assert res == {}