"""Tests for event rating service — mock AsyncSession 实现可测试性

覆盖：
  - 有效报名口径（is_del=0 + pay_status=1 + card_code 非空）
  - 已建档/未建档选手的积分与段位
  - 同一身份证多次报名 → 去重
  - 无报名人的赛事 → 空结果
  - 排序稳定（按身份证号）
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from services.event_rating_service import get_event_ratings

CARD_A = "110101199001011234"
CARD_B = "110101199202024567"
CARD_C = "110101199303036789"


def _make_row(card_code: str, rating: float, games: int, wins: int = 0, losses: int = 0):
    """构造一条 EloPlayerRating（SimpleNamespace）。"""
    return SimpleNamespace(
        card_code=card_code,
        rating=rating,
        games=games,
        wins=wins,
        losses=losses,
    )


def _make_db(applicant_rows: list, rating_rows: list) -> AsyncMock:
    """创建 mock DB：第一次 execute 返回报名行，第二次返回 rating 行。"""
    db = AsyncMock(spec=AsyncSession)

    async def fake_execute(stmt, params=None):
        ex = MagicMock()
        if params is not None:  # text() 查询报名人
            ex.fetchall.return_value = applicant_rows
        else:  # select 查询积分
            ex.scalars().all.return_value = rating_rows
        return ex

    db.execute = AsyncMock(side_effect=fake_execute)
    return db


@pytest.mark.asyncio
async def test_event_ratings_with_registered_players():
    """已建档报名人：返回积分/场次/段位/姓名"""
    applicants = [
        SimpleNamespace(card_code=CARD_A, name="张三"),
        SimpleNamespace(card_code=CARD_B, name="李四"),
    ]
    ratings = [
        _make_row(CARD_A, 1764.32, 30, 20, 10),
        _make_row(CARD_B, 1512.00, 8, 5, 3),
    ]
    db = _make_db(applicants, ratings)
    data = await get_event_ratings(db, 82)

    assert data.event_id == 82
    assert data.sport_type == "badminton"
    assert len(data.results) == 2

    r0 = data.results[0]
    assert r0.card_code == CARD_A
    assert r0.name == "张三"
    assert r0.rating == 1764.32
    assert r0.games == 30
    assert r0.rank == "7段"  # 1764 → 7段
    assert r0.is_provisional is False
    assert r0.is_new is False

    r1 = data.results[1]
    assert r1.card_code == CARD_B
    assert r1.rank == "5段"  # 1512 → 5段


@pytest.mark.asyncio
async def test_event_ratings_new_player_defaults():
    """报名但未建档选手 → 默认 1500 + 定级中 + is_new"""
    applicants = [SimpleNamespace(card_code=CARD_A, name="张三")]
    db = _make_db(applicants, [])
    data = await get_event_ratings(db, 82)

    assert len(data.results) == 1
    r = data.results[0]
    assert r.card_code == CARD_A
    assert r.name == "张三"
    assert r.rating == 1500.0
    assert r.games == 0
    assert r.rank == "定级中"
    assert r.is_provisional is True
    assert r.is_new is True


@pytest.mark.asyncio
async def test_event_ratings_dedupes_same_card():
    """同一身份证多次报名（多项目）→ 只保留一条"""
    applicants = [
        SimpleNamespace(card_code=CARD_A, name="张三"),
        SimpleNamespace(card_code=CARD_A, name="张三"),
        SimpleNamespace(card_code=CARD_B, name="李四"),
    ]
    ratings = [_make_row(CARD_A, 1600.0, 5)]
    db = _make_db(applicants, ratings)
    data = await get_event_ratings(db, 82)

    assert len(data.results) == 2
    cards = [r.card_code for r in data.results]
    assert cards == [CARD_A, CARD_B]  # 按身份证号排序


@pytest.mark.asyncio
async def test_event_ratings_empty_event():
    """赛事无有效报名人 → 空结果"""
    db = _make_db([], [])
    data = await get_event_ratings(db, 999)
    assert data.results == []


@pytest.mark.asyncio
async def test_event_ratings_sorted_by_card():
    """结果按身份证号稳定排序"""
    applicants = [
        SimpleNamespace(card_code=CARD_C, name="王五"),
        SimpleNamespace(card_code=CARD_A, name="张三"),
        SimpleNamespace(card_code=CARD_B, name="李四"),
    ]
    ratings = [_make_row(CARD_A, 1500.0, 3)]
    db = _make_db(applicants, ratings)
    data = await get_event_ratings(db, 82)

    assert [r.card_code for r in data.results] == [CARD_A, CARD_B, CARD_C]


@pytest.mark.asyncio
async def test_event_ratings_mixed_registered_and_new():
    """部分建档：建档返回真实段位，未建档返回定级中"""
    applicants = [
        SimpleNamespace(card_code=CARD_A, name="张三"),
        SimpleNamespace(card_code=CARD_B, name="李四"),
    ]
    ratings = [_make_row(CARD_A, 1650.0, 10)]
    db = _make_db(applicants, ratings)
    data = await get_event_ratings(db, 82)

    assert len(data.results) == 2
    assert data.results[0].card_code == CARD_A
    assert data.results[0].rank == "6段"  # 1650 → 6段
    assert data.results[0].is_new is False
    assert data.results[1].card_code == CARD_B
    assert data.results[1].rank == "定级中"
    assert data.results[1].is_new is True
