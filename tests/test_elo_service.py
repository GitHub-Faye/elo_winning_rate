"""Tests for EloService — 通过 mock AsyncSession 实现可测试性"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas import EloRecordRequest, PlayerInput
from services import EloService


@pytest.fixture
def mock_db() -> AsyncMock:
    """创建一个 mock 的 AsyncSession。"""
    db = AsyncMock(spec=AsyncSession)
    db.add = MagicMock()
    db.flush = AsyncMock()

    # execute 返回的是同步 Result 对象（scalar_one_or_none 是同步方法）
    # 所以用 MagicMock 而非 AsyncMock
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

    def _make_singles_request(
        self,
        score_a: int = 21,
        score_b: int = 15,
        rating_a: float = 1500.0,
        rating_b: float = 1500.0,
        games_a: int = 5,
        games_b: int = 5,
    ) -> EloRecordRequest:
        return EloRecordRequest(
            event_id=1,
            battle_id=100,
            source_order=0,
            score_a=score_a,
            score_b=score_b,
            players_a=[
                PlayerInput(user_id=1, rating=rating_a, games=games_a, wins=2, losses=3),
            ],
            players_b=[
                PlayerInput(user_id=2, rating=rating_b, games=games_b, wins=3, losses=2),
            ],
            event_weight=1.0,
        )

    @pytest.mark.asyncio
    async def test_singles_winner_gains_rating(self, service: EloService):
        """胜者加分，败者减分。"""
        req = self._make_singles_request(score_a=21, score_b=15)
        resp = await service.record_match(req)

        assert resp.battle_id == 100
        assert resp.team_size == 1
        assert len(resp.records) == 2

        # 找到 A 方（胜者）
        rec_a = next(r for r in resp.records if r.team_side == "A")
        rec_b = next(r for r in resp.records if r.team_side == "B")

        assert rec_a.is_winner is True
        assert rec_b.is_winner is False
        assert rec_a.delta > 0, f"胜者应加分，但 delta={rec_a.delta}"
        assert rec_b.delta < 0, f"败者应减分，但 delta={rec_b.delta}"
        assert abs(rec_a.delta + rec_b.delta) < 1, (
            f"双方 delta 应近似对称，但 {rec_a.delta} vs {rec_b.delta}"
        )

    @pytest.mark.asyncio
    async def test_singles_upset_bonus(self, service: EloService):
        """定级期新人爆冷胜高段位 → 触发越级加分 bonus。"""
        req = self._make_singles_request(
            score_a=21, score_b=18,
            rating_a=1500.0, rating_b=1700.0,  # A 比 B 低 200
            games_a=0, games_b=50,  # A 是新人
        )
        resp = await service.record_match(req)

        rec_a = next(r for r in resp.records if r.team_side == "A")
        assert rec_a.upset_bonus > 0, f"新人爆冷应有 bonus，但={rec_a.upset_bonus}"
        assert rec_a.delta > rec_a.clamped_delta, (
            f"delta={rec_a.delta} 应大于 clamped_delta={rec_a.clamped_delta}（含 bonus）"
        )

    @pytest.mark.asyncio
    async def test_singles_upset_penalty(self, service: EloService):
        """高段位输给定级新人 → 被越级扣分 penalty。"""
        req = self._make_singles_request(
            score_a=18, score_b=21,  # A 输了
            rating_a=1700.0, rating_b=1500.0,  # A 比 B 高 200
            games_a=50, games_b=0,  # B 是新人
        )
        resp = await service.record_match(req)

        rec_a = next(r for r in resp.records if r.team_side == "A")
        rec_b = next(r for r in resp.records if r.team_side == "B")

        # B 是新人在赢，获得 bonus；A 高段位输了，A 的 penalty 取决于 B 的 bonus
        assert rec_b.upset_bonus > 0, f"新人爆冷应有 bonus，但={rec_b.upset_bonus}"
        assert rec_a.upset_penalty > 0, f"输给定级新人应有 penalty，但={rec_a.upset_penalty}"

    @pytest.mark.asyncio
    async def test_singles_new_player_high_k(self, service: EloService):
        """新选手 K=40，稳定期选手 K=20。"""
        req = self._make_singles_request(
            score_a=21, score_b=15,
            rating_a=1500.0, rating_b=1500.0,
            games_a=0, games_b=50,  # A 新人，B 稳定
        )
        resp = await service.record_match(req)

        rec_a = next(r for r in resp.records if r.team_side == "A")
        rec_b = next(r for r in resp.records if r.team_side == "B")

        assert rec_a.k_factor == 40.0, f"新人 K=40，但={rec_a.k_factor}"
        assert rec_b.k_factor == 20.0, f"稳定期 K=20，但={rec_b.k_factor}"

    @pytest.mark.asyncio
    async def test_singles_db_write_called(self, service: EloService, mock_db: AsyncMock):
        """验证数据库写入被调用。"""
        req = self._make_singles_request()
        await service.record_match(req)

        # db.add 应被调用 2 次（elo_match_record × 2）+ 2 次（EloPlayerRating 新选手 × 2）
        # 但新选手检查时 mock 返回 None，所以是 2 + 2 = 4 次
        assert mock_db.add.call_count >= 4, f"add 应被调用至少 4 次，但={mock_db.add.call_count}"
        mock_db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_singles_rating_consistency(self, service: EloService):
        """rating_after == rating_before + delta。"""
        req = self._make_singles_request()
        resp = await service.record_match(req)

        for rec in resp.records:
            assert abs(rec.rating_after - (rec.rating_before + rec.delta)) < 0.001, (
                f"rating_after={rec.rating_after} != "
                f"rating_before={rec.rating_before} + delta={rec.delta}"
            )


# ── 双打测试 ──


class TestDoubles:
    """双打场景测试"""

    def _make_doubles_request(
        self,
        score_a: int = 21,
        score_b: int = 15,
    ) -> EloRecordRequest:
        return EloRecordRequest(
            event_id=1,
            battle_id=200,
            source_order=0,
            score_a=score_a,
            score_b=score_b,
            players_a=[
                PlayerInput(user_id=1, rating=1500.0, games=10, wins=5, losses=5),
                PlayerInput(user_id=2, rating=1600.0, games=20, wins=12, losses=8),
            ],
            players_b=[
                PlayerInput(user_id=3, rating=1550.0, games=15, wins=8, losses=7),
                PlayerInput(user_id=4, rating=1450.0, games=5, wins=2, losses=3),
            ],
            event_weight=1.0,
        )

    @pytest.mark.asyncio
    async def test_doubles_four_records(self, service: EloService):
        """双打返回 4 条记录。"""
        req = self._make_doubles_request()
        resp = await service.record_match(req)

        assert resp.team_size == 2
        assert len(resp.records) == 4

    @pytest.mark.asyncio
    async def test_doubles_winner_side(self, service: EloService):
        """胜方所有队员 is_winner=True。"""
        req = self._make_doubles_request(score_a=21, score_b=15)
        resp = await service.record_match(req)

        for rec in resp.records:
            if rec.team_side == "A":
                assert rec.is_winner is True, f"A 方队员 {rec.user_id} 应标记为胜"
            else:
                assert rec.is_winner is False, f"B 方队员 {rec.user_id} 应标记为负"

    @pytest.mark.asyncio
    async def test_doubles_rating_consistency(self, service: EloService):
        """所有队员 rating_after = rating_before + delta。"""
        req = self._make_doubles_request()
        resp = await service.record_match(req)

        for rec in resp.records:
            assert abs(rec.rating_after - (rec.rating_before + rec.delta)) < 0.001, (
                f"User {rec.user_id}: {rec.rating_after} != "
                f"{rec.rating_before} + {rec.delta}"
            )

    @pytest.mark.asyncio
    async def test_doubles_db_write_called(self, service: EloService, mock_db: AsyncMock):
        """双打写入 4 条 match_record + 4 条新选手 = 8 次 add。"""
        req = self._make_doubles_request()
        await service.record_match(req)

        assert mock_db.add.call_count >= 8, f"add 应被调用至少 8 次，但={mock_db.add.call_count}"


# ── 边界场景 ──


class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_draw_score(self, service: EloService):
        """平局（分数相等）→ 双方 delta 为 0 或接近 0。"""
        req = EloRecordRequest(
            event_id=1,
            battle_id=300,
            source_order=0,
            score_a=21,
            score_b=21,
            players_a=[
                PlayerInput(user_id=1, rating=1500.0, games=10, wins=5, losses=5),
            ],
            players_b=[
                PlayerInput(user_id=2, rating=1500.0, games=10, wins=5, losses=5),
            ],
            event_weight=1.0,
        )
        resp = await service.record_match(req)

        for rec in resp.records:
            assert abs(rec.delta) < 0.1, f"平局 delta 应接近 0，但={rec.delta}"

    @pytest.mark.asyncio
    async def test_team_size_mismatch(self, service: EloService):
        """双方人数不匹配 → ValueError。"""
        req = EloRecordRequest(
            event_id=1,
            battle_id=400,
            source_order=0,
            score_a=21,
            score_b=15,
            players_a=[
                PlayerInput(user_id=1, rating=1500.0, games=0, wins=0, losses=0),
            ],
            players_b=[
                PlayerInput(user_id=2, rating=1500.0, games=0, wins=0, losses=0),
                PlayerInput(user_id=3, rating=1500.0, games=0, wins=0, losses=0),
            ],
            event_weight=1.0,
        )
        with pytest.raises(ValueError, match="人数不匹配"):
            await service.record_match(req)

    @pytest.mark.asyncio
    async def test_existing_player_update(self, service: EloService, mock_db: AsyncMock):
        """已有选手 → 更新战绩而非新建。"""
        # 用普通对象而非 MagicMock — 避免属性访问产生嵌套 mock
        from types import SimpleNamespace

        existing_rating = SimpleNamespace(
            user_id=1,
            sport_type="badminton",
            rating=Decimal("1500.00"),
            games=10,
            wins=5,
            losses=5,
            draws=0,
            highest_rating=Decimal("1600.00"),
            lowest_rating=Decimal("1400.00"),
        )

        # 第一次 execute(user_id=1) → 已有用户，第二次 execute(user_id=2) → 新选手
        exec_result_1 = MagicMock()
        exec_result_1.scalar_one_or_none.return_value = existing_rating
        exec_result_2 = MagicMock()
        exec_result_2.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(side_effect=[exec_result_1, exec_result_2])

        req = EloRecordRequest(
            event_id=1,
            battle_id=500,
            source_order=0,
            score_a=21,
            score_b=15,
            players_a=[
                PlayerInput(user_id=1, rating=1500.0, games=10, wins=5, losses=5),
            ],
            players_b=[
                PlayerInput(user_id=2, rating=1500.0, games=0, wins=0, losses=0),
            ],
            event_weight=1.0,
        )
        resp = await service.record_match(req)

        # 用户 1 已有记录，games +1，wins +1（赢了本场）
        assert existing_rating.games == 11, f"games 应+1，但={existing_rating.games}"
        assert existing_rating.losses == 5, f"losses 不应增加（赢了），但={existing_rating.losses}"
        assert existing_rating.wins == 6, f"wins 应+1，但={existing_rating.wins}"