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
    _view_for_side,
)

# 测试用身份证号
CARD_A = "110101199001011234"
CARD_B = "110101199202024567"
CARD_C = "110101199303036789"
CARD_D = "110101199404041122"


@pytest.fixture
def mock_db() -> AsyncMock:
    """创建全空 DB 的 mock（无 rating 记录）。"""
    db = AsyncMock(spec=AsyncSession)

    # _load_player_ratings: scalars().all() → []
    ex1 = MagicMock()
    ex1.scalars().all.return_value = []

    db.execute = AsyncMock(return_value=ex1)
    return db


@pytest.fixture
def service(mock_db: AsyncMock) -> PredictionService:
    return PredictionService(mock_db)


class TestSingles:
    """单打预测测试"""

    def _make_request(self, team_a=(CARD_A,), team_b=(CARD_B,)) -> PredictionRequest:
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
        assert p.card_code == CARD_A
        assert p.rating == 1500.0
        assert p.games == 0
        assert p.wins == 0
        assert p.losses == 0
        assert 0 <= p.probability <= 1
        assert 0 <= p.elo_base_probability <= 1


class TestDoubles:
    """双打预测测试"""

    def _make_request(self) -> PredictionRequest:
        return PredictionRequest(team_a=[CARD_A, CARD_B], team_b=[CARD_C, CARD_D])

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

    @pytest.mark.asyncio
    async def test_doubles_probability_invariants(self, service: PredictionService):
        """双打双方概率之和 ≈ 1（取各队最高 Elo 者预测，结果复制给全队）。"""
        resp = await service.predict(self._make_request())
        pa = [p.probability for p in resp.data.team_a.players]
        pb = [p.probability for p in resp.data.team_b.players]
        for a_prob, b_prob in zip(pa, pb):
            assert abs(a_prob + b_prob - 1.0) < 0.001, f"a={a_prob}, b={b_prob}"


class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_team_size_mismatch(self, service: PredictionService):
        """双方人数不匹配 → ValueError。"""
        req = PredictionRequest(team_a=[CARD_A], team_b=[CARD_B, CARD_C])
        with pytest.raises(ValueError, match="人数不匹配"):
            await service.predict(req)

    @pytest.mark.asyncio
    async def test_success_envelope(self, service: PredictionService):
        """响应包含 success=True 和 data。"""
        req = PredictionRequest(team_a=[CARD_A], team_b=[CARD_B])
        resp = await service.predict(req)
        assert resp.success is True
        assert resp.data is not None
        assert hasattr(resp.data, "team_a")
        assert hasattr(resp.data, "team_b")

    def test_unsupported_team_size(self):
        """人数不在 1-2 之间（Pydantic max_length 校验）。"""
        with pytest.raises(ValidationError, match="at most 2"):
            PredictionRequest(team_a=[CARD_A, CARD_B, CARD_C], team_b=[CARD_D, CARD_A, CARD_B])

    def test_duplicate_id_in_team_a_rejected(self):
        """Team A 中重复身份证 → ValidationError。"""
        with pytest.raises(ValidationError, match="重复"):
            PredictionRequest(team_a=[CARD_A, CARD_A], team_b=[CARD_B, CARD_C])

    def test_duplicate_id_in_team_b_rejected(self):
        """Team B 中重复身份证 → ValidationError。"""
        with pytest.raises(ValidationError, match="重复"):
            PredictionRequest(team_a=[CARD_A, CARD_B], team_b=[CARD_C, CARD_C])

    def test_overlap_across_teams_rejected(self):
        """双方有相同选手 → ValidationError。"""
        with pytest.raises(ValidationError, match="相同"):
            PredictionRequest(team_a=[CARD_A], team_b=[CARD_A])


class TestBuildResult:
    """_build_result 和 _view_for_side 纯函数的独立测试"""

    def test_view_for_side_is_a(self):
        """is_a=True 时 _view_for_side 直接读取 A 方字段。"""
        from types import SimpleNamespace

        result = SimpleNamespace(
            probability_a=0.75,
            probability_b=0.25,
            elo_base_probability=0.6,
        )
        v = _view_for_side(result, is_a=True)
        assert v.probability == 0.75
        assert v.elo_base_probability == 0.6

    def test_view_for_side_is_b(self):
        """is_a=False 时 _view_for_side 翻转字段。"""
        from types import SimpleNamespace

        result = SimpleNamespace(
            probability_a=0.75,
            probability_b=0.25,
            elo_base_probability=0.6,
        )
        v = _view_for_side(result, is_a=False)
        assert v.probability == 0.25
        assert abs(v.elo_base_probability - 0.4) < 0.001

    def test_build_result_proxies_view(self):
        """_build_result 使用 _view_for_side 的结果构建 PlayerPredictionResult。"""
        from types import SimpleNamespace

        result = SimpleNamespace(
            probability_a=0.75,
            probability_b=0.25,
            elo_base_probability=0.6,
        )
        snapshot = PlayerRatingSnapshot(rating=1600.0, games=20, wins=12, losses=8)
        pr = _build_result(CARD_A, snapshot, result, is_a=True)

        assert pr.card_code == CARD_A
        assert pr.rating == 1600.0
        assert pr.probability == 0.75
        assert pr.elo_base_probability == 0.6

        pr_b = _build_result(CARD_B, snapshot, result, is_a=False)
        assert pr_b.probability == 0.25
