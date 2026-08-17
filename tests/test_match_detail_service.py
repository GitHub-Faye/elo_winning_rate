"""Tests for match_detail_service — mock AsyncSession 实现可测试性

覆盖：
  - 单打比赛无 card_code → 返回 2 人基础变化
  - 单打比赛有 card_code → 返回该选手的 analysis
  - 双打比赛无 card_code → 返回 4 人基础变化
  - 比赛不存在 → 返回 None
  - 段位变化（赛后跨越段位门槛）
  - 距下一段位差分计算
  - 已最高段 → points_to_next_tier 为 None
  - 地区排名上升/下降
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from services.match_detail_service import (
    _compute_region_rank,
    _get_next_tier,
    get_match_detail,
)

CARD_A = "110101199001011234"
CARD_B = "110101199202024567"
CARD_C = "110101199303036789"
CARD_D = "110101199404041111"


def _make_match_record(
    card_code: str,
    team_side: str,
    team_size: int,
    is_winner: int,
    rating_before: float,
    delta: float,
    rating_after: float,
    score_self: int,
    score_opponent: int,
    event_id: int = 100,
    battle_id: int = 200,
    played_at: datetime | None = None,
):
    return SimpleNamespace(
        id=1,
        event_id=event_id,
        battle_id=battle_id,
        source_order=0,
        card_code=card_code,
        team_side=team_side,
        team_size=team_size,
        is_winner=is_winner,
        rating_before=rating_before,
        delta=delta,
        rating_after=rating_after,
        expected=0.5,
        k_factor=20.0,
        weight_multiplier=1.0,
        margin_multiplier=1.0,
        base_delta=delta,
        clamped_delta=delta,
        upset_bonus=0,
        upset_penalty=0,
        opponent_card_code=CARD_B if card_code == CARD_A else CARD_A,
        opponent_partner_card_code=None,
        score_self=score_self,
        score_opponent=score_opponent,
        played_at=played_at or datetime(2026, 8, 17, 10, 0, 0),
    )


def _make_player_rating(card_code: str, rating: float, games: int, province: str = "山西省", city: str = "太原市"):
    return SimpleNamespace(
        card_code=card_code,
        rating=rating,
        games=games,
        province=province,
        city=city,
    )


# ── _get_next_tier 辅助函数测试 ──


class TestGetNextTier:
    def test_below_9段(self):
        assert _get_next_tier(1500.0, 2) == ("6段", 1600)
        assert _get_next_tier(1650.0, 2) == ("7段", 1700)
        assert _get_next_tier(1850.0, 2) == ("9段", 1900)

    def test_at_threshold(self):
        # 1500 → displayed=1500, < 1600 → 下一段 6段
        assert _get_next_tier(1500.0, 2) == ("6段", 1600)

    def test_already_9段(self):
        assert _get_next_tier(1950.0, 2) is None

    def test_rounding(self):
        # 1599.6 → displayed=1600, < 1700 → 下一段 7段
        assert _get_next_tier(1599.6, 2) == ("7段", 1700)

    def test_provisional_player(self):
        """定级中选手返回 None"""
        assert _get_next_tier(1500.0, 0) is None
        assert _get_next_tier(1800.0, 1) is None


# ── _compute_region_rank 辅助函数测试 ──


class TestComputeRegionRank:
    def test_basic_ranking(self):
        rows = [("A", 1700.0), ("B", 1600.0), ("C", 1500.0)]
        rank, total = _compute_region_rank(rows, "B", 1600.0)
        assert rank == 2
        assert total == 3

    def test_target_not_in_region(self):
        rows = [("A", 1700.0), ("B", 1600.0)]
        rank, total = _compute_region_rank(rows, "C", 1500.0)
        assert rank == 3
        assert total == 3

    def test_tie_breaking(self):
        rows = [("B", 1600.0), ("A", 1600.0)]
        rank, total = _compute_region_rank(rows, "A", 1600.0)
        # A 字典序更小，排第 1
        assert rank == 1
        assert total == 2


# ── get_match_detail 测试 ──


def _make_db_single_query(rows: list) -> AsyncMock:
    """创建 mock DB，execute 返回给定行。"""
    db = AsyncMock(spec=AsyncSession)
    ex = MagicMock()
    ex.scalars().all.return_value = rows
    db.execute = AsyncMock(return_value=ex)
    return db


def _make_db_multi_query(results: list) -> AsyncMock:
    """创建 mock DB，多次 execute 返回不同结果。"""
    db = AsyncMock(spec=AsyncSession)
    mocks = []
    for rows in results:
        ex = MagicMock()
        ex.scalars().all.return_value = rows
        mocks.append(ex)
    db.execute = AsyncMock(side_effect=mocks)
    return db


@pytest.mark.asyncio
async def test_singles_no_card_code():
    """单打比赛，无 card_code → 返回 2 人基础变化"""
    records = [
        _make_match_record(CARD_A, "A", 1, 1, 1560.0, 15.5, 1575.5, 21, 15),
        _make_match_record(CARD_B, "B", 1, 0, 1500.0, -15.5, 1484.5, 15, 21),
    ]
    db = _make_db_single_query(records)

    data = await get_match_detail(db, 200, "badminton")

    assert data is not None
    assert data.battle_id == 200
    assert data.event_id == 100
    assert data.match_type == "singles"
    assert data.score_a == 21
    assert data.score_b == 15
    assert len(data.players) == 2
    assert data.analysis is None

    # A 方赢了
    p_a = [p for p in data.players if p.card_code == CARD_A][0]
    assert p_a.is_winner is True
    assert p_a.delta == 15.5
    assert p_a.rating_before == 1560.0
    assert p_a.rating_after == 1575.5

    # B 方输了
    p_b = [p for p in data.players if p.card_code == CARD_B][0]
    assert p_b.is_winner is False
    assert p_b.delta == -15.5


@pytest.mark.asyncio
async def test_singles_with_card_code():
    """单打比赛，有 card_code → 返回该选手的 analysis"""
    records = [
        _make_match_record(CARD_A, "A", 1, 1, 1560.0, 15.5, 1575.5, 21, 15),
        _make_match_record(CARD_B, "B", 1, 0, 1500.0, -15.5, 1484.5, 15, 21),
    ]

    # mock 第二次查询：选手的 province/city/games（返回 tuple 模拟 Row）
    player_row = ("山西省", "太原市", 10)
    # mock 第三次查询：地区已定级选手
    region_rows = [
        SimpleNamespace(card_code=CARD_A, rating=1575.5),  # 赛后
        SimpleNamespace(card_code=CARD_B, rating=1484.5),
        SimpleNamespace(card_code=CARD_C, rating=1600.0),
    ]

    db = AsyncMock(spec=AsyncSession)
    ex1 = MagicMock()
    ex1.scalars().all.return_value = records
    ex2 = MagicMock()
    ex2.one_or_none.return_value = player_row
    ex3 = MagicMock()
    ex3.all.return_value = region_rows
    db.execute = AsyncMock(side_effect=[ex1, ex2, ex3])

    data = await get_match_detail(db, 200, "badminton", card_code=CARD_A)

    assert data is not None
    assert data.analysis is not None
    assert data.analysis.card_code == CARD_A
    assert data.analysis.delta == 15.5
    assert data.analysis.rating_before == 1560.0
    assert data.analysis.rating_after == 1575.5

    # 距下一段位：1575.5 → 下一段 6段(1600)，差 24.5
    assert data.analysis.points_to_next_tier == 24.5
    assert data.analysis.next_tier == "6段"

    # 地区排名
    assert data.analysis.region_rank_before is not None
    assert data.analysis.region_rank_after is not None
    assert data.analysis.region_total == 3
    assert data.analysis.region_rank_change is not None


@pytest.mark.asyncio
async def test_doubles_no_card_code():
    """双打比赛，无 card_code → 返回 4 人基础变化"""
    records = [
        _make_match_record(CARD_A, "A", 2, 1, 1560.0, 10.0, 1570.0, 21, 18),
        _make_match_record(CARD_C, "A", 2, 1, 1520.0, 10.0, 1530.0, 21, 18),
        _make_match_record(CARD_B, "B", 2, 0, 1500.0, -10.0, 1490.0, 18, 21),
        _make_match_record(CARD_D, "B", 2, 0, 1480.0, -10.0, 1470.0, 18, 21),
    ]
    db = _make_db_single_query(records)

    data = await get_match_detail(db, 200, "badminton")

    assert data is not None
    assert data.match_type == "doubles"
    assert len(data.players) == 4
    assert data.analysis is None

    winners = [p for p in data.players if p.is_winner]
    losers = [p for p in data.players if not p.is_winner]
    assert len(winners) == 2
    assert len(losers) == 2


@pytest.mark.asyncio
async def test_match_not_found():
    """比赛不存在 → 返回 None"""
    db = _make_db_single_query([])
    data = await get_match_detail(db, 99999, "badminton")
    assert data is None


@pytest.mark.asyncio
async def test_rank_change_after_match():
    """赛后段位跨越门槛"""
    # 赛前 1595 (5段)，赛后 1610 (6段)
    records = [
        _make_match_record(CARD_A, "A", 1, 1, 1595.0, 15.0, 1610.0, 21, 15),
        _make_match_record(CARD_B, "B", 1, 0, 1500.0, -15.0, 1485.0, 15, 21),
    ]

    player_row = ("山西省", "太原市", 10)
    region_rows = [
        SimpleNamespace(card_code=CARD_A, rating=1610.0),
        SimpleNamespace(card_code=CARD_B, rating=1485.0),
    ]

    db = AsyncMock(spec=AsyncSession)
    ex1 = MagicMock()
    ex1.scalars().all.return_value = records
    ex2 = MagicMock()
    ex2.one_or_none.return_value = player_row
    ex3 = MagicMock()
    ex3.all.return_value = region_rows
    db.execute = AsyncMock(side_effect=[ex1, ex2, ex3])

    data = await get_match_detail(db, 200, "badminton", card_code=CARD_A)

    assert data is not None
    a = data.players[0]
    assert a.rank_before == "5段"
    assert a.rank_after == "6段"

    # 距下一段位：1610 → 下一段 7段(1700)，差 90
    assert data.analysis.next_tier == "7段"
    assert data.analysis.points_to_next_tier == 90.0


@pytest.mark.asyncio
async def test_already_9段_no_next_tier():
    """已最高段 → points_to_next_tier 为 None"""
    records = [
        _make_match_record(CARD_A, "A", 1, 1, 1950.0, 10.0, 1960.0, 21, 15),
        _make_match_record(CARD_B, "B", 1, 0, 1500.0, -10.0, 1490.0, 15, 21),
    ]

    player_row = ("山西省", "太原市", 10)
    region_rows = [
        SimpleNamespace(card_code=CARD_A, rating=1960.0),
    ]

    db = AsyncMock(spec=AsyncSession)
    ex1 = MagicMock()
    ex1.scalars().all.return_value = records
    ex2 = MagicMock()
    ex2.one_or_none.return_value = player_row
    ex3 = MagicMock()
    ex3.all.return_value = region_rows
    db.execute = AsyncMock(side_effect=[ex1, ex2, ex3])

    data = await get_match_detail(db, 200, "badminton", card_code=CARD_A)

    assert data.analysis.next_tier is None
    assert data.analysis.points_to_next_tier is None


@pytest.mark.asyncio
async def test_region_rank_improvement():
    """赛后排名上升（delta 为正，排名数字变小）"""
    records = [
        _make_match_record(CARD_A, "A", 1, 1, 1595.0, 20.0, 1615.0, 21, 15),
        _make_match_record(CARD_B, "B", 1, 0, 1600.0, -20.0, 1580.0, 15, 21),
    ]

    player_row = ("山西省", "太原市", 10)
    # 赛前：B(1600) > A(1595) → A 排第 2
    # 赛后：A(1615) > B(1580) → A 排第 1
    region_rows = [
        SimpleNamespace(card_code=CARD_A, rating=1595.0),
        SimpleNamespace(card_code=CARD_B, rating=1600.0),
    ]

    db = AsyncMock(spec=AsyncSession)
    ex1 = MagicMock()
    ex1.scalars().all.return_value = records
    ex2 = MagicMock()
    ex2.one_or_none.return_value = player_row
    ex3 = MagicMock()
    ex3.all.return_value = region_rows
    db.execute = AsyncMock(side_effect=[ex1, ex2, ex3])

    data = await get_match_detail(db, 200, "badminton", card_code=CARD_A)

    assert data.analysis.region_rank_before == 2
    assert data.analysis.region_rank_after == 1
    assert data.analysis.region_rank_change == 1  # 上升 1 名
