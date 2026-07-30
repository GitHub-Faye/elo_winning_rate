"""Tests for EloService — 通过 mock AsyncSession 实现可测试性"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas import EloRecordRequest
from services import EloService


@pytest.fixture
def mock_db() -> AsyncMock:
    """创建一个 mock 的 AsyncSession。"""
    db = AsyncMock(spec=AsyncSession)
    db.add = MagicMock()
    db.flush = AsyncMock()

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=execute_result)
    return db


@pytest.fixture
def service(mock_db: AsyncMock) -> EloService:
    return EloService(mock_db)


# ── 单打测试 ──


class TestSingles:
    """单打场景测试"""

    def _make_request(
        self,
        score_a: int = 21,
        score_b: int = 15,
        user_a: int = 1,
        user_b: int = 2,
    ) -> EloRecordRequest:
        return EloRecordRequest(
            event_id=1,
            battle_id=100,
            source_order=0,
            score_a=score_a,
            score_b=score_b,
            team_a=[user_a],
            team_b=[user_b],
            event_weight=1.0,
        )

    def _fake_rating(self, **kw):
        """创建 SimpleNamespace 模拟 EloPlayerRating（新选手默认 None）。"""
        return None

    @pytest.mark.asyncio
    async def test_singles_winner_gains_rating(self, service: EloService):
        """胜者加分，败者减分。"""
        resp = await service.record_match(self._make_request(score_a=21, score_b=15))

        assert resp.success is True
        assert len(resp.data.team_a) == 1
        assert len(resp.data.team_b) == 1

        ra = resp.data.team_a[0]
        rb = resp.data.team_b[0]

        assert ra.user_id == 1
        assert rb.user_id == 2
        assert ra.delta > 0, f"胜者应加分，但 delta={ra.delta}"
        assert rb.delta < 0, f"败者应减分，但 delta={rb.delta}"
        assert abs(ra.delta + rb.delta) < 1, (
            f"双方 delta 应近似对称，但 {ra.delta} vs {rb.delta}"
        )
        # 验证响应含 games_after/wins_after/losses_after
        assert ra.games_after >= 0
        assert ra.wins_after >= 0
        assert rb.losses_after >= 0

    @pytest.mark.asyncio
    async def test_singles_upset_bonus(self, service: EloService):
        """定级期新人爆冷胜高段位 → 触发越级加分 bonus。
        mock DB 返回新人 rating 低、games=0；对方高 rating、games=50。"""
        existing_rating_b = SimpleNamespace(
            user_id=2, sport_type="badminton",
            rating=Decimal("1700.00"), games=50, wins=30, losses=20,
            draws=0, highest_rating=Decimal("1800.00"), lowest_rating=Decimal("1500.00"),
        )
        # execute 顺序：load user1(None) → load user2(1700) → upsert user1(None) → upsert user2(1700)
        exec_result_1 = MagicMock(); exec_result_1.scalar_one_or_none.return_value = None
        exec_result_2 = MagicMock(); exec_result_2.scalar_one_or_none.return_value = existing_rating_b
        exec_result_3 = MagicMock(); exec_result_3.scalar_one_or_none.return_value = None
        exec_result_4 = MagicMock(); exec_result_4.scalar_one_or_none.return_value = existing_rating_b
        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[exec_result_1, exec_result_2, exec_result_3, exec_result_4])
        svc = EloService(mock_db)

        req = self._make_request(score_a=21, score_b=18)
        resp = await svc.record_match(req)

        ra = resp.data.team_a[0]
        assert ra.upset_bonus > 0, f"新人爆冷应有 bonus，但={ra.upset_bonus}"
        assert ra.delta > ra.clamped_delta, (
            f"delta={ra.delta} 应大于 clamped_delta={ra.clamped_delta}（含 bonus）"
        )

    @pytest.mark.asyncio
    async def test_singles_upset_penalty(self, service: EloService):
        """高段位输给定级新人 → 被越级扣分 penalty。"""
        existing_rating_a = SimpleNamespace(
            user_id=1, sport_type="badminton",
            rating=Decimal("1700.00"), games=50, wins=30, losses=20,
            draws=0, highest_rating=Decimal("1800.00"), lowest_rating=Decimal("1500.00"),
        )
        # execute 顺序：load user1(1700) → load user2(None) → upsert user1(1700) → upsert user2(None)
        exec_result_1 = MagicMock(); exec_result_1.scalar_one_or_none.return_value = existing_rating_a
        exec_result_2 = MagicMock(); exec_result_2.scalar_one_or_none.return_value = None
        exec_result_3 = MagicMock(); exec_result_3.scalar_one_or_none.return_value = existing_rating_a
        exec_result_4 = MagicMock(); exec_result_4.scalar_one_or_none.return_value = None
        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[exec_result_1, exec_result_2, exec_result_3, exec_result_4])
        svc = EloService(mock_db)

        req = self._make_request(score_a=18, score_b=21)
        resp = await svc.record_match(req)

        ra = resp.data.team_a[0]
        rb = resp.data.team_b[0]
        assert rb.upset_bonus > 0, f"新人爆冷应有 bonus，但={rb.upset_bonus}"
        assert ra.upset_penalty > 0, f"输给定级新人应有 penalty，但={ra.upset_penalty}"

    @pytest.mark.asyncio
    async def test_singles_new_player_high_k(self, service: EloService):
        """新选手 K=40，稳定期选手 K=20。"""
        existing_rating_b = SimpleNamespace(
            user_id=2, sport_type="badminton",
            rating=Decimal("1500.00"), games=50, wins=25, losses=25,
            draws=0, highest_rating=Decimal("1600.00"), lowest_rating=Decimal("1400.00"),
        )
        exec_result_1 = MagicMock(); exec_result_1.scalar_one_or_none.return_value = None
        exec_result_2 = MagicMock(); exec_result_2.scalar_one_or_none.return_value = existing_rating_b
        exec_result_3 = MagicMock(); exec_result_3.scalar_one_or_none.return_value = None
        exec_result_4 = MagicMock(); exec_result_4.scalar_one_or_none.return_value = existing_rating_b
        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[exec_result_1, exec_result_2, exec_result_3, exec_result_4])
        svc = EloService(mock_db)

        req = self._make_request(score_a=21, score_b=15)
        resp = await svc.record_match(req)

        assert resp.data.team_a[0].k_factor == 40.0
        assert resp.data.team_b[0].k_factor == 20.0

    @pytest.mark.asyncio
    async def test_singles_db_write_called(self, service: EloService, mock_db: AsyncMock):
        """验证数据库写入被调用。"""
        resp = await service.record_match(self._make_request())
        assert mock_db.add.call_count >= 2, f"add 应至少 2 次，但={mock_db.add.call_count}"
        mock_db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_singles_rating_consistency(self, service: EloService):
        """rating_after == rating_before + delta。"""
        resp = await service.record_match(self._make_request())
        for result in resp.data.team_a + resp.data.team_b:
            assert abs(result.rating_after - (result.rating_before + result.delta)) < 0.001


# ── 双打测试 ──


class TestDoubles:
    """双打场景测试"""

    def _make_request(self, score_a=21, score_b=15) -> EloRecordRequest:
        return EloRecordRequest(
            event_id=1,
            battle_id=200,
            source_order=0,
            score_a=score_a,
            score_b=score_b,
            team_a=[1, 2],
            team_b=[3, 4],
            event_weight=1.0,
        )

    @pytest.mark.asyncio
    async def test_doubles_four_results(self, service: EloService):
        """双打返回 team_a 2 人 + team_b 2 人 = 4 条结果。"""
        resp = await service.record_match(self._make_request())
        assert len(resp.data.team_a) == 2
        assert len(resp.data.team_b) == 2

    @pytest.mark.asyncio
    async def test_doubles_rating_consistency(self, service: EloService):
        """所有队员 rating_after = rating_before + delta。"""
        resp = await service.record_match(self._make_request())
        for result in resp.data.team_a + resp.data.team_b:
            assert abs(result.rating_after - (result.rating_before + result.delta)) < 0.001

    @pytest.mark.asyncio
    async def test_doubles_db_write_called(self, service: EloService, mock_db: AsyncMock):
        """双打写入 4 条 match_record + 4 条新选手 = 8 次 add。"""
        resp = await service.record_match(self._make_request())
        assert mock_db.add.call_count >= 4, f"add 应至少 4 次，但={mock_db.add.call_count}"


# ── 边界场景 ──


class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_draw_score(self, service: EloService):
        """平局（分数相等）→ 双方 delta 接近 0。"""
        req = EloRecordRequest(
            event_id=1, battle_id=300, source_order=0,
            score_a=21, score_b=21,
            team_a=[1], team_b=[2], event_weight=1.0,
        )
        resp = await service.record_match(req)
        for result in resp.data.team_a + resp.data.team_b:
            assert abs(result.delta) < 0.1, f"平局 delta 应接近 0，但={result.delta}"

    @pytest.mark.asyncio
    async def test_team_size_mismatch(self, service: EloService):
        """双方人数不匹配 → ValueError。"""
        req = EloRecordRequest(
            event_id=1, battle_id=400, source_order=0,
            score_a=21, score_b=15,
            team_a=[1], team_b=[2, 3], event_weight=1.0,
        )
        with pytest.raises(ValueError, match="人数不匹配"):
            await service.record_match(req)

    @pytest.mark.asyncio
    async def test_existing_player_update(self, mock_db: AsyncMock):
        """已有选手 → 更新战绩而非新建。"""
        existing = SimpleNamespace(
            user_id=1, sport_type="badminton",
            rating=Decimal("1500.00"), games=10, wins=5, losses=5,
            draws=0, highest_rating=Decimal("1600.00"), lowest_rating=Decimal("1400.00"),
        )
        exec_result_1 = MagicMock()
        exec_result_1.scalar_one_or_none.return_value = existing
        exec_result_2 = MagicMock()
        exec_result_2.scalar_one_or_none.return_value = None
        exec_result_3 = MagicMock()
        exec_result_3.scalar_one_or_none.return_value = existing
        exec_result_4 = MagicMock()
        exec_result_4.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(side_effect=[exec_result_1, exec_result_2, exec_result_3, exec_result_4])

        svc = EloService(mock_db)
        req = EloRecordRequest(
            event_id=1, battle_id=500, source_order=0,
            score_a=21, score_b=15,
            team_a=[1], team_b=[2], event_weight=1.0,
        )
        await svc.record_match(req)

        assert existing.games == 11, f"games 应+1，但={existing.games}"
        assert existing.losses == 5, f"losses 不应增加（赢了），但={existing.losses}"
        assert existing.wins == 6, f"wins 应+1，但={existing.wins}"

    @pytest.mark.asyncio
    async def test_success_envelope(self, service: EloService):
        """响应包含 success=True 和 data.team_a/data.team_b。"""
        req = EloRecordRequest(
            event_id=1, battle_id=600, source_order=0,
            score_a=21, score_b=15,
            team_a=[1], team_b=[2], event_weight=1.0,
        )
        resp = await service.record_match(req)
        assert resp.success is True
        assert hasattr(resp.data, "team_a")
        assert hasattr(resp.data, "team_b")
        assert len(resp.data.team_a) == 1
        assert len(resp.data.team_b) == 1