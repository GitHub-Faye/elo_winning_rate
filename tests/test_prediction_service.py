"""Tests for PredictionService — mock AsyncSession 实现可测试性"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas import PredictionRequest
from services.prediction_service import (
    PlayerRatingSnapshot,
    PredictionService,
    _build_result,
)


@pytest.fixture
def mock_db() -> AsyncMock:
    """创建全空 DB 的 mock（无 rating 记录、无比赛记录）。"""
    db = AsyncMock(spec=AsyncSession)

    # _load_player_ratings: scalars().all() → []
    ex1 = MagicMock()
    ex1.scalars().all.return_value = []
    # _build_relation_graph_async: 两个查询，都返回空
    ex2 = MagicMock()
    ex2.scalars().all.return_value = []
    ex3 = MagicMock()
    ex3.scalars().all.return_value = []

    db.execute = AsyncMock(side_effect=[ex1, ex2, ex3])
    return db


@pytest.fixture
def service(mock_db: AsyncMock) -> PredictionService:
    return PredictionService(mock_db)


class TestSingles:
    """单打预测测试"""

    def _make_request(self, team_a=(1,), team_b=(2,)) -> PredictionRequest:
        return PredictionRequest(team_a=list(team_a), team_b=list(team_b))

    @pytest.mark.asyncio
    async def test_singles_returns_two_results(self, service: PredictionService):
        """单打返回 team_a 1 人 + team_b 1 人。"""
        resp = await service.predict(self._make_request())
        assert resp.success is True
        assert len(resp.data.team_a.players) == 1
        assert len(resp.data.team_b.players) == 1

    @pytest.mark.asyncio
    async def test_singles_probability_sum_one(self, service: PredictionService):
        """双方胜率之和接近 1。"""
        resp = await service.predict(self._make_request())
        pa = resp.data.team_a.players[0].probability
        pb = resp.data.team_b.players[0].probability
        assert abs(pa + pb - 1.0) < 0.001, f"pa={pa}, pb={pb}"

    @pytest.mark.asyncio
    async def test_singles_same_rating(self, service: PredictionService):
        """同分选手预测胜率接近 0.5。"""
        resp = await service.predict(self._make_request())
        p = resp.data.team_a.players[0].probability
        assert 0.3 < p < 0.7, f"同分选手应接近 0.5，但={p}"

    @pytest.mark.asyncio
    async def test_singles_response_fields(self, service: PredictionService):
        """响应包含所有必要字段。"""
        resp = await service.predict(self._make_request())
        p = resp.data.team_a.players[0]
        assert p.user_id == 1
        assert p.rating == 1500.0
        assert p.games == 0
        assert p.wins == 0
        assert p.losses == 0
        assert 0 <= p.probability <= 1
        assert 0 <= p.elo_base_probability <= 1
        assert isinstance(p.direct_adjustment, float)
        assert isinstance(p.indirect_adjustment, float)


class TestDoubles:
    """双打预测测试"""

    def _make_request(self) -> PredictionRequest:
        return PredictionRequest(team_a=[1, 2], team_b=[3, 4])

    @pytest.mark.asyncio
    async def test_doubles_returns_four_results(self, service: PredictionService):
        """双打返回 2+2=4 条预测结果。"""
        resp = await service.predict(self._make_request())
        assert len(resp.data.team_a.players) == 2
        assert len(resp.data.team_b.players) == 2

    @pytest.mark.asyncio
    async def test_doubles_match_type(self, service: PredictionService):
        """match_type 为 doubles。"""
        resp = await service.predict(self._make_request())
        assert resp.data.match_type == "doubles"


class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_team_size_mismatch(self, service: PredictionService):
        """双方人数不匹配 → ValueError。"""
        req = PredictionRequest(team_a=[1], team_b=[2, 3])
        with pytest.raises(ValueError, match="人数不匹配"):
            await service.predict(req)

    @pytest.mark.asyncio
    async def test_success_envelope(self, service: PredictionService):
        """响应包含 success=True 和 data。"""
        req = PredictionRequest(team_a=[1], team_b=[2])
        resp = await service.predict(req)
        assert resp.success is True
        assert resp.data is not None
        assert hasattr(resp.data, "team_a")
        assert hasattr(resp.data, "team_b")

    def test_unsupported_team_size(self):
        """人数不在 1-2 之间（Pydantic max_length 校验）。"""
        with pytest.raises(ValidationError, match="at most 2"):
            PredictionRequest(team_a=[1, 2, 3], team_b=[4, 5, 6])

    def test_duplicate_id_in_team_a_rejected(self):
        """Team A 中重复 ID → ValidationError。"""
        with pytest.raises(ValidationError, match="重复"):
            PredictionRequest(team_a=[1, 1], team_b=[2, 3])

    def test_duplicate_id_in_team_b_rejected(self):
        """Team B 中重复 ID → ValidationError。"""
        with pytest.raises(ValidationError, match="重复"):
            PredictionRequest(team_a=[1, 2], team_b=[3, 3])

    def test_overlap_across_teams_rejected(self):
        """双方有相同选手 → ValidationError。"""
        with pytest.raises(ValidationError, match="相同"):
            PredictionRequest(team_a=[1], team_b=[1])


class TestRelationGraph:
    """用预置比赛记录验证关系图构建"""

    def _make_record(
        self,
        user_id: int = 1,
        opponent_user_id: int = 2,
        is_winner: int = 1,
        rating_before: float = 1500.0,
        delta: float = 10.0,
    ):
        """构造一条简单的 EloMatchRecord。"""
        from types import SimpleNamespace
        return SimpleNamespace(
            user_id=user_id,
            opponent_user_id=opponent_user_id,
            is_winner=is_winner,
            event_id=1,
            battle_id=1,
            source_order=0,
            team_side="A",
            team_size=1,
            rating_before=rating_before,
            delta=delta,
            rating_after=rating_before + delta,
            score_self=21,
            score_opponent=15,
        )

    def _make_db_with_records(
        self,
        records1: list,
        records2: list | None = None,
    ) -> AsyncMock:
        """创建含比赛记录的 mock DB。

        execute 调用顺序:
          1. _load_player_ratings: scalars().all() → []
          2. _build_relation_graph_async 第1层: scalars().all() → records1
          3. _build_relation_graph_async 第2层: scalars().all() → records2 or []
        """
        db = AsyncMock(spec=AsyncSession)

        e1 = MagicMock()
        e1.scalars().all.return_value = []

        e2 = MagicMock()
        e2.scalars().all.return_value = records1

        e3 = MagicMock()
        e3.scalars().all.return_value = records2 or []

        db.execute = AsyncMock(side_effect=[e1, e2, e3])
        return db

    @pytest.mark.asyncio
    async def test_singles_with_direct_record(self):
        """有直接交手记录 → direct_record_total > 0。"""
        rec = self._make_record(user_id=1, opponent_user_id=2, is_winner=1)
        db = self._make_db_with_records([rec])
        svc = PredictionService(db)

        req = PredictionRequest(team_a=[1], team_b=[2])
        resp = await svc.predict(req)

        p1 = resp.data.team_a.players[0]
        assert p1.direct_record_total >= 1, f"应有交手记录，但 total={p1.direct_record_total}"
        if p1.direct_record_total > 0:
            assert p1.direct_record_wins >= 1 or p1.direct_record_losses >= 1


class TestBuildResult:
    """_build_result 纯函数的独立测试"""

    def test_build_result_is_a(self):
        """is_a=True 时使用队友字段。"""
        from types import SimpleNamespace

        result = SimpleNamespace(
            probability_a=0.75,
            probability_b=0.25,
            elo_base_probability=0.6,
            direct_adjustment=0.08,
            indirect_adjustment=0.07,
            direct_record={"wins": 3, "losses": 1, "total": 4},
        )
        snapshot = PlayerRatingSnapshot(rating=1600.0, games=20, wins=12, losses=8)
        pr = _build_result(1, snapshot, result, is_a=True)

        assert pr.user_id == 1
        assert pr.probability == 0.75
        assert pr.elo_base_probability == 0.6
        assert pr.direct_record_wins == 3
        assert pr.direct_record_losses == 1
        assert pr.direct_record_total == 4

    def test_build_result_is_b(self):
        """is_a=False 时翻转 probability 和交手记录。"""
        from types import SimpleNamespace

        result = SimpleNamespace(
            probability_a=0.75,
            probability_b=0.25,
            elo_base_probability=0.6,
            direct_adjustment=0.08,
            indirect_adjustment=0.07,
            direct_record={"wins": 3, "losses": 1, "total": 4},
        )
        snapshot = PlayerRatingSnapshot(rating=1500.0, games=10, wins=5, losses=5)
        pr = _build_result(2, snapshot, result, is_a=False)

        assert pr.user_id == 2
        assert pr.probability == 0.25
        assert abs(pr.elo_base_probability - 0.4) < 0.001
        # direct_record flipped: B's wins = A's losses
        assert pr.direct_record_wins == 1
        assert pr.direct_record_losses == 3
