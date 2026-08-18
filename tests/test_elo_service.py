"""Tests for EloService — 通过 mock AsyncSession 实现可测试性"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas import EloRecordRequest
from services.elo_service import EloService
from services.battle_card_service import get_card_codes_by_battle_id

# 测试用身份证号
CARD_A = "110101199001011234"
CARD_B = "110101199202024567"
CARD_C = "110101199303036789"
CARD_D = "110101199404041122"


@pytest.fixture
def mock_db() -> AsyncMock:
    """创建一个 mock 的 AsyncSession（所有选手默认新选手）。"""
    return _make_mock_db_all_new()


@pytest.fixture
def service(mock_db: AsyncMock) -> EloService:
    return EloService(mock_db)


def _make_mock_db_all_new() -> AsyncMock:
    """创建一个 mock DB：所有用户都无记录（scalars().all() → []）。"""
    db = AsyncMock(spec=AsyncSession)
    db.add = MagicMock()
    db.flush = AsyncMock()

    exec_result = MagicMock()
    exec_result.scalars().all.return_value = []
    db.execute = AsyncMock(return_value=exec_result)
    return db


def _make_battle_info(
    battle_id: int = 100,
    event_id: int = 1,
    team_a: list[str] | None = None,
    team_b: list[str] | None = None,
    score_a: int = 21,
    score_b: int = 15,
    item_score: str | None = "21:15",
) -> dict:
    """构造 battle_card_service 返回的 mock 数据。"""
    if team_a is None:
        team_a = [CARD_A]
    if team_b is None:
        team_b = [CARD_B]
    return {
        "battle_id": battle_id,
        "event_id": event_id,
        "project_type": 1 if len(team_a) == 1 else 2,
        "team_a": team_a,
        "team_b": team_b,
        "team_a_names": ["选手A"],
        "team_b_names": ["选手B"],
        "score_a": score_a,
        "score_b": score_b,
        "item_score": item_score,
        "battle_time": datetime(2024, 1, 1),
        "is_valid": True,
        "missing_count": 0,
    }


def _mock_singles_db(
    team_a_existing=None,
    team_b_existing=None,
) -> AsyncMock:
    """创建单打场景的 mock DB。

    execute 调用顺序（共 4 次）：
        1. _load_player_states([CARD_A]): .scalars().all() → [team_a_existing] or []
        2. _load_player_states([CARD_B]): .scalars().all() → [team_b_existing] or []
        3. _upsert_rating(card_a): .scalar_one_or_none() → team_a_existing (or None)
        4. _upsert_rating(card_b): .scalar_one_or_none() → team_b_existing (or None)
    """
    db = AsyncMock(spec=AsyncSession)
    db.add = MagicMock()
    db.flush = AsyncMock()

    # 第1次 execute: _load_player_states(team_a)
    e1 = MagicMock()
    e1.scalars().all.return_value = [team_a_existing] if team_a_existing else []

    # 第2次 execute: _load_player_states(team_b)
    e2 = MagicMock()
    e2.scalars().all.return_value = [team_b_existing] if team_b_existing else []

    # 第3次 execute: _upsert_rating(card_a)
    e3 = MagicMock()
    e3.scalar_one_or_none.return_value = team_a_existing

    # 第4次 execute: _upsert_rating(card_b)
    e4 = MagicMock()
    e4.scalar_one_or_none.return_value = team_b_existing

    db.execute = AsyncMock(side_effect=[e1, e2, e3, e4])
    return db


def _existing_rating(card_code: str, rating: Decimal, games: int, wins: int, losses: int):
    """构造一条已存在的 EloPlayerRating（SimpleNamespace）。"""
    return SimpleNamespace(
        card_code=card_code, sport_type="badminton",
        rating=rating, games=games, wins=wins, losses=losses,
        draws=0, highest_rating=Decimal("1800.00"), lowest_rating=Decimal("1500.00"),
    )


# ── 单打测试 ──


class TestSingles:
    """单打场景测试"""

    def _make_request(
        self,
        battle_id: int = 100,
        event_weight: float = 1.0,
    ) -> EloRecordRequest:
        return EloRecordRequest(
            battle_id=battle_id,
            event_weight=event_weight,
        )

    @pytest.mark.asyncio
    async def test_singles_winner_gains_rating(self, mock_db: AsyncMock):
        """胜者加分，败者减分。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = _make_battle_info(
                score_a=21, score_b=15, item_score="21:15"
            )
            # Mock item_score 查询
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:15", "battle_time": datetime(2024, 1, 1)}
            )
            # Mock _load_player_states
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            # Mock _upsert_rating
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            # Mock _fetch_region
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states, e_upsert, e_upsert, e_region, e_region
            ])

            service = EloService(mock_db)
            resp = await service.record_match(self._make_request(battle_id=100))

            assert resp.success is True
            assert len(resp.data.team_a) == 1
            assert len(resp.data.team_b) == 1

        ra = resp.data.team_a[0]
        rb = resp.data.team_b[0]

        assert ra.card_code == CARD_A
        assert rb.card_code == CARD_B
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
    async def test_singles_upset_bonus(self):
        """定级期新人爆冷胜高段位 → 触发越级加分 bonus。"""
        existing_rating_b = _existing_rating(CARD_B, Decimal("1700.00"), 50, 30, 20)

        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = _make_battle_info(
                score_a=21, score_b=18, item_score="21:18"
            )
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:18", "battle_time": datetime(2024, 1, 1)}
            )
            # Mock _load_player_states + _upsert_rating
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_states_existing = MagicMock()
            e_states_existing.scalars().all.return_value = [existing_rating_b]
            e_upsert_none = MagicMock()
            e_upsert_none.scalar_one_or_none.return_value = None
            e_upsert_existing = MagicMock()
            e_upsert_existing.scalar_one_or_none.return_value = existing_rating_b
            # Mock _fetch_region
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db = _mock_singles_db()
            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states_existing, e_upsert_none, e_upsert_existing, e_region
            ])

            svc = EloService(mock_db)
            req = self._make_request(battle_id=100)
            resp = await svc.record_match(req)

        ra = resp.data.team_a[0]
        assert ra.upset_bonus > 0, f"新人爆冷应有 bonus，但={ra.upset_bonus}"
        assert ra.delta > ra.clamped_delta, (
            f"delta={ra.delta} 应大于 clamped_delta={ra.clamped_delta}（含 bonus）"
        )

    @pytest.mark.asyncio
    async def test_singles_upset_penalty(self):
        """高段位输给定级新人 → 被越级扣分 penalty。"""
        existing_rating_a = _existing_rating(CARD_A, Decimal("1700.00"), 50, 30, 20)

        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = _make_battle_info(
                score_a=18, score_b=21, item_score="18:21"
            )
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "18:21", "battle_time": datetime(2024, 1, 1)}
            )
            e_states_existing = MagicMock()
            e_states_existing.scalars().all.return_value = [existing_rating_a]
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert_existing = MagicMock()
            e_upsert_existing.scalar_one_or_none.return_value = existing_rating_a
            e_upsert_none = MagicMock()
            e_upsert_none.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db = _mock_singles_db()
            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states_existing, e_states, e_upsert_existing, e_upsert_none, e_region
            ])

            svc = EloService(mock_db)
            req = self._make_request(battle_id=100)
            resp = await svc.record_match(req)

        ra = resp.data.team_a[0]
        rb = resp.data.team_b[0]
        assert rb.upset_bonus > 0, f"新人爆冷应有 bonus，但={rb.upset_bonus}"
        assert ra.upset_penalty > 0, f"输给定级新人应有 penalty，但={ra.upset_penalty}"

    @pytest.mark.asyncio
    async def test_singles_new_player_high_k(self):
        """新选手 K=80，稳定期选手 K=15。"""
        existing_rating_b = _existing_rating(CARD_B, Decimal("1500.00"), 80, 40, 40)

        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = _make_battle_info(
                score_a=21, score_b=15, item_score="21:15"
            )
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:15", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_states_existing = MagicMock()
            e_states_existing.scalars().all.return_value = [existing_rating_b]
            e_upsert_none = MagicMock()
            e_upsert_none.scalar_one_or_none.return_value = None
            e_upsert_existing = MagicMock()
            e_upsert_existing.scalar_one_or_none.return_value = existing_rating_b
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db = _mock_singles_db()
            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states_existing, e_upsert_none, e_upsert_existing, e_region
            ])

            svc = EloService(mock_db)
            req = self._make_request(battle_id=100)
            resp = await svc.record_match(req)

        assert resp.data.team_a[0].k_factor == 80.0  # new_player_k (games=0)
        assert resp.data.team_b[0].k_factor == 15.0  # stable_k (games=50)

    @pytest.mark.asyncio
    async def test_singles_db_write_called(self, mock_db: AsyncMock):
        """验证数据库写入被调用。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = _make_battle_info()
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:15", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states, e_upsert, e_upsert, e_region, e_region
            ])

            service = EloService(mock_db)
            resp = await service.record_match(self._make_request())
            assert mock_db.add.call_count >= 2, f"add 应至少 2 次，但={mock_db.add.call_count}"
            mock_db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_singles_rating_consistency(self, mock_db: AsyncMock):
        """rating_after == rating_before + delta。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = _make_battle_info()
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:15", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states, e_upsert, e_upsert, e_region, e_region
            ])

            service = EloService(mock_db)
            resp = await service.record_match(self._make_request())
            for result in resp.data.team_a + resp.data.team_b:
                assert abs(result.rating_after - (result.rating_before + result.delta)) < 0.001


# ── 双打测试 ──


class TestDoubles:
    """双打场景测试"""

    def _make_request(self, battle_id: int = 200) -> EloRecordRequest:
        return EloRecordRequest(
            battle_id=battle_id,
            event_weight=1.0,
        )

    @pytest.mark.asyncio
    async def test_doubles_four_results(self, mock_db: AsyncMock):
        """双打返回 team_a 2 人 + team_b 2 人 = 4 条结果。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = _make_battle_info(
                team_a=[CARD_A, CARD_B], team_b=[CARD_C, CARD_D],
                score_a=21, score_b=15, item_score="21:15",
            )
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:15", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states, e_states, e_states,
                e_upsert, e_upsert, e_upsert, e_upsert,
                e_region, e_region, e_region, e_region,
            ])

            service = EloService(mock_db)
            resp = await service.record_match(self._make_request())
            assert len(resp.data.team_a) == 2
            assert len(resp.data.team_b) == 2

    @pytest.mark.asyncio
    async def test_doubles_rating_consistency(self, mock_db: AsyncMock):
        """所有队员 rating_after = rating_before + delta。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = _make_battle_info(
                team_a=[CARD_A, CARD_B], team_b=[CARD_C, CARD_D],
                score_a=21, score_b=15, item_score="21:15",
            )
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:15", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states, e_states, e_states,
                e_upsert, e_upsert, e_upsert, e_upsert,
                e_region, e_region, e_region, e_region,
            ])

            service = EloService(mock_db)
            resp = await service.record_match(self._make_request())
            for result in resp.data.team_a + resp.data.team_b:
                assert abs(result.rating_after - (result.rating_before + result.delta)) < 0.001

    @pytest.mark.asyncio
    async def test_doubles_db_write_called(self, mock_db: AsyncMock):
        """双打写入 4 条 match_record + 4 条新选手 = 8 次 add。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = _make_battle_info(
                team_a=[CARD_A, CARD_B], team_b=[CARD_C, CARD_D],
                score_a=21, score_b=15, item_score="21:15",
            )
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:15", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states, e_states, e_states,
                e_upsert, e_upsert, e_upsert, e_upsert,
                e_region, e_region, e_region, e_region,
            ])

            service = EloService(mock_db)
            resp = await service.record_match(self._make_request())
            assert mock_db.add.call_count >= 4, f"add 应至少 4 次，但={mock_db.add.call_count}"

    @pytest.mark.asyncio
    async def test_doubles_opponent_partner_card(self, mock_db: AsyncMock):
        """双打时 opponent_partner_card_code 有值。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = _make_battle_info(
                team_a=[CARD_A, CARD_B], team_b=[CARD_C, CARD_D],
                score_a=21, score_b=15, item_score="21:15",
            )
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:15", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states, e_states, e_states,
                e_upsert, e_upsert, e_upsert, e_upsert,
                e_region, e_region, e_region, e_region,
            ])

            service = EloService(mock_db)
            resp = await service.record_match(self._make_request())
            a0 = resp.data.team_a[0]
            assert a0.opponent_card_code == CARD_C
            assert a0.opponent_partner_card_code == CARD_D


# ── 边界场景 ──


class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_draw_score(self, mock_db: AsyncMock):
        """平局（分数相等）→ 双方 delta 接近 0。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = _make_battle_info(
                score_a=21, score_b=21, item_score="21:21"
            )
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:21", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states, e_upsert, e_upsert, e_region, e_region
            ])

            service = EloService(mock_db)
            req = EloRecordRequest(battle_id=300, event_weight=1.0)
            resp = await service.record_match(req)
            for result in resp.data.team_a + resp.data.team_b:
                assert abs(result.delta) < 0.1, f"平局 delta 应接近 0，但={result.delta}"

    @pytest.mark.asyncio
    async def test_team_size_mismatch(self, mock_db: AsyncMock):
        """双方人数不匹配 → ValueError。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = _make_battle_info(
                team_a=[CARD_A], team_b=[CARD_B, CARD_C],
            )
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": None, "battle_time": datetime(2024, 1, 1)}
            )
            mock_db.execute = AsyncMock(side_effect=[e_battle])

            service = EloService(mock_db)
            req = EloRecordRequest(battle_id=400, event_weight=1.0)
            with pytest.raises(ValueError, match="队伍人数不匹配"):
                await service.record_match(req)

    @pytest.mark.asyncio
    async def test_battle_not_found(self, mock_db: AsyncMock):
        """比赛不存在 → ValueError。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = None

            service = EloService(mock_db)
            req = EloRecordRequest(battle_id=999999, event_weight=1.0)
            with pytest.raises(ValueError, match="比赛不存在"):
                await service.record_match(req)

    @pytest.mark.asyncio
    async def test_success_envelope(self, mock_db: AsyncMock):
        """响应包含 success=True 和 data.team_a/data.team_b。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = _make_battle_info()
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:15", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states, e_upsert, e_upsert, e_region, e_region
            ])

            service = EloService(mock_db)
            req = EloRecordRequest(battle_id=600, event_weight=1.0)
            resp = await service.record_match(req)
            assert resp.success is True
            assert hasattr(resp.data, "team_a")
            assert hasattr(resp.data, "team_b")
            assert len(resp.data.team_a) == 1
            assert len(resp.data.team_b) == 1


# ── 多局 Elo 均值测试 ──


class TestMultiGameEloAverage:
    """多局比赛逐局 Elo 计算并取均值的测试"""

    def _make_multi_game_battle(
        self,
        item_score: str,
        team_a: list[str] | None = None,
        team_b: list[str] | None = None,
    ) -> dict:
        """构造多局比赛的 battle info mock。"""
        if team_a is None:
            team_a = [CARD_A]
        if team_b is None:
            team_b = [CARD_B]
        # 计算总分用于 score_a/score_b（局数）
        from core.score_parser import parse_item_score
        total_a, total_b = parse_item_score(item_score)
        return {
            "battle_id": 500,
            "event_id": 1,
            "project_type": 1 if len(team_a) == 1 else 2,
            "team_a": team_a,
            "team_b": team_b,
            "team_a_names": ["选手A"],
            "team_b_names": ["选手B"],
            "score_a": total_a,
            "score_b": total_b,
            "item_score": item_score,
            "battle_time": datetime(2024, 1, 1),
            "is_valid": True,
            "missing_count": 0,
        }

    @pytest.mark.asyncio
    async def test_multi_game_averages_deltas(self):
        """三局比赛（A赢1局B赢2局）→ delta 为各局均值。"""
        from elo_compute import (
            EloConfig,
            MatchInput,
            SideInput,
            compute_match_pair,
        )

        config = EloConfig()

        # 各局独立计算（注意：s_a/s_b 是胜负值，每局不同）
        # Game 1: 21:11 → A胜 s_a=1.0, s_b=0.0
        r_a1, r_b1 = compute_match_pair(
            SideInput(rating=1500.0, games=0, team_size=1, actual_score=1.0, wins=0, losses=0),
            SideInput(rating=1500.0, games=0, team_size=1, actual_score=0.0, wins=0, losses=0),
            MatchInput(score_a=21, score_b=11, event_weight=1.0), config,
        )
        # Game 2: 18:21 → B胜 s_a=0.0, s_b=1.0
        r_a2, r_b2 = compute_match_pair(
            SideInput(rating=1500.0, games=0, team_size=1, actual_score=0.0, wins=0, losses=0),
            SideInput(rating=1500.0, games=0, team_size=1, actual_score=1.0, wins=0, losses=0),
            MatchInput(score_a=18, score_b=21, event_weight=1.0), config,
        )
        # Game 3: 12:21 → B胜 s_a=0.0, s_b=1.0
        r_a3, r_b3 = compute_match_pair(
            SideInput(rating=1500.0, games=0, team_size=1, actual_score=0.0, wins=0, losses=0),
            SideInput(rating=1500.0, games=0, team_size=1, actual_score=1.0, wins=0, losses=0),
            MatchInput(score_a=12, score_b=21, event_weight=1.0), config,
        )

        expected_avg_delta_a = (r_a1.delta + r_a2.delta + r_a3.delta) / 3
        expected_avg_delta_b = (r_b1.delta + r_b2.delta + r_b3.delta) / 3

        # Mock DB
        mock_db = _mock_singles_db()
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = self._make_multi_game_battle("21:11|18:21|12:21")
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:11|18:21|12:21", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states, e_upsert, e_upsert, e_region, e_region,
            ])

            service = EloService(mock_db)
            req = EloRecordRequest(battle_id=500, event_weight=1.0)
            resp = await service.record_match(req)

        ra = resp.data.team_a[0]
        rb = resp.data.team_b[0]

        assert abs(ra.delta - expected_avg_delta_a) < 0.01, (
            f"A 的 delta 应为各局均值: 期望 {expected_avg_delta_a:.4f}, 实际 {ra.delta:.4f}"
        )
        assert abs(rb.delta - expected_avg_delta_b) < 0.01, (
            f"B 的 delta 应为各局均值: 期望 {expected_avg_delta_b:.4f}, 实际 {rb.delta:.4f}"
        )
        # rating_after == rating_before + delta
        assert abs(ra.rating_after - (ra.rating_before + ra.delta)) < 0.01
        assert abs(rb.rating_after - (rb.rating_before + rb.delta)) < 0.01

    @pytest.mark.asyncio
    async def test_multi_game_winner_based_on_game_wins(self):
        """三局比赛 A赢1局B赢2局 → B 是胜者（按赢的局数，非总分）。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            # A总分51 > B总分53？不，A总分51 < B总分53
            # 但胜负按局数：A赢1局(21:11)，B赢2局(18:21, 12:21) → B胜
            mock_get_cards.return_value = self._make_multi_game_battle("21:11|18:21|12:21")
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:11|18:21|12:21", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db = _mock_singles_db()
            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states, e_upsert, e_upsert, e_region, e_region,
            ])

            service = EloService(mock_db)
            resp = await service.record_match(EloRecordRequest(battle_id=500, event_weight=1.0))

        # B 赢了 2 局 > A 赢了 1 局 → B 胜，A 负
        ra = resp.data.team_a[0]
        rb = resp.data.team_b[0]
        assert ra.delta < 0, f"A 局数较少应减分, delta={ra.delta}"
        assert rb.delta > 0, f"B 局数较多应加分, delta={rb.delta}"

    @pytest.mark.asyncio
    async def test_winner_by_game_wins_not_total_score(self):
        """A总分低于B但赢了更多局 → A是胜者（按局数判定，非总分）。

        "21:19|21:19|5:21" → A赢2局(21:19,21:19)，B赢1局(5:21)
        A总分=47 < B总分=59，但A赢了2局 → A胜
        """
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = self._make_multi_game_battle("21:19|21:19|5:21")
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:19|21:19|5:21", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db = _mock_singles_db()
            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states, e_upsert, e_upsert, e_region, e_region,
            ])

            service = EloService(mock_db)
            resp = await service.record_match(EloRecordRequest(battle_id=500, event_weight=1.0))

        # A 赢了 2 局 > B 赢了 1 局 → A 胜（尽管 A 总分更低）
        ra = resp.data.team_a[0]
        rb = resp.data.team_b[0]
        assert ra.delta > 0, f"A 赢了更多局应加分, delta={ra.delta}"
        assert rb.delta < 0, f"B 赢了更少局应减分, delta={rb.delta}"

    @pytest.mark.asyncio
    async def test_single_game_identical_to_before(self):
        """单局比赛行为与改动前完全一致。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            mock_get_cards.return_value = self._make_multi_game_battle("21:15")
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:15", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db = _mock_singles_db()
            mock_db.execute = AsyncMock(side_effect=[
                e_battle, e_states, e_states, e_upsert, e_upsert, e_region, e_region,
            ])

            service = EloService(mock_db)
            resp = await service.record_match(EloRecordRequest(battle_id=500, event_weight=1.0))

        ra = resp.data.team_a[0]
        rb = resp.data.team_b[0]
        # 单局时均值 = 唯一一局的值
        assert ra.delta > 0
        assert rb.delta < 0
        assert abs(ra.delta + rb.delta) < 1, (
            f"单局双方 delta 应近似对称: {ra.delta} vs {rb.delta}"
        )

    @pytest.mark.asyncio
    async def test_multi_game_doubles_averages(self):
        """双打多局比赛 → 四人 delta 各自为均值，胜负按局数。"""
        with patch("services.battle_card_service.get_card_codes_by_battle_id") as mock_get_cards:
            # A赢1局(21:11)，B赢2局(18:21, 12:21) → B胜
            mock_get_cards.return_value = self._make_multi_game_battle(
                "21:11|18:21|12:21",
                team_a=[CARD_A, CARD_B],
                team_b=[CARD_C, CARD_D],
            )
            e_battle = MagicMock()
            e_battle.fetchone.return_value = SimpleNamespace(
                _mapping={"item_score": "21:11|18:21|12:21", "battle_time": datetime(2024, 1, 1)}
            )
            e_states = MagicMock()
            e_states.scalars().all.return_value = []
            e_upsert = MagicMock()
            e_upsert.scalar_one_or_none.return_value = None
            e_region = MagicMock()
            e_region.first.return_value = None

            mock_db = AsyncMock(spec=AsyncSession)
            mock_db.add = MagicMock()
            mock_db.flush = AsyncMock()

            # _load_player_states x2, _upsert_rating x4, _fetch_region x4
            mock_db.execute = AsyncMock(side_effect=[
                e_states, e_states, e_states, e_states,  # load states
                e_upsert, e_upsert, e_upsert, e_upsert,  # upsert ratings
                e_region, e_region, e_region, e_region,  # fetch regions
            ])

            service = EloService(mock_db)
            resp = await service.record_match(EloRecordRequest(battle_id=500, event_weight=1.0))

        assert len(resp.data.team_a) == 2
        assert len(resp.data.team_b) == 2

        # B方赢了2局 > A方赢了1局 → B方应加分，A方应减分
        for r in resp.data.team_a:
            assert r.delta < 0, f"A方 {r.card_code} 应减分, delta={r.delta}"
        for r in resp.data.team_b:
            assert r.delta > 0, f"B方 {r.card_code} 应加分, delta={r.delta}"

        # 每人 rating_after == rating_before + delta
        for r in resp.data.team_a + resp.data.team_b:
            assert abs(r.rating_after - (r.rating_before + r.delta)) < 0.01
