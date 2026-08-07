"""Test for player-records service — mock AsyncSession 实现可测试性"""
from __future__ import annotations

from datetime import datetime

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from services.player_records_service import get_player_records

# 测试用身份证号
CARD_A = "110101199001011234"
CARD_B = "110101199202024567"


def _make_record(
    event_id: int = 1,
    battle_id: int = 1,
    card_code: str = CARD_A,
    team_size: int = 1,
    is_winner: int = 1,
    score_self: int = 21,
    score_opponent: int = 15,
    rating_before: float = 1500.0,
    delta: float = 10.0,
    opponent_card_code: str = CARD_B,
    played_at=None,
    rate_id: int = 1,
):
    """构造一条 EloMatchRecord（用 SimpleNamespace 模拟）。"""
    from types import SimpleNamespace
    return SimpleNamespace(
        id=rate_id,
        event_id=event_id,
        battle_id=battle_id,
        source_order=0,
        card_code=card_code,
        team_side="A",
        team_size=team_size,
        is_winner=is_winner,
        rating_before=rating_before,
        delta=delta,
        rating_after=rating_before + delta,
        expected=0.5,
        k_factor=32.0,
        weight_multiplier=1.0,
        margin_multiplier=1.0,
        base_delta=delta,
        clamped_delta=delta,
        upset_bonus=0.0,
        upset_penalty=0.0,
        opponent_card_code=opponent_card_code,
        opponent_partner_card_code=None,
        score_self=score_self,
        score_opponent=score_opponent,
        played_at=played_at,
    )


def _make_db(records: list) -> AsyncMock:
    """创建 mock DB，返回给定记录。

    execute 调用一次：该选手的全部比赛记录。
    """
    db = AsyncMock(spec=AsyncSession)
    e1 = MagicMock()
    e1.scalars().all.return_value = records
    db.execute = AsyncMock(return_value=e1)
    return db


@pytest.mark.asyncio
async def test_no_records():
    """无比赛记录 → 空列表，汇总统计全零"""
    db = _make_db([])
    data = await get_player_records(db, CARD_A)
    assert data.card_code == CARD_A
    assert data.records == []
    assert data.summary.total_matches == 0
    assert data.summary.wins == 0
    assert data.summary.losses == 0
    assert data.summary.win_rate is None
    assert data.summary.avg_score_self is None
    assert data.summary.avg_delta is None


@pytest.mark.asyncio
async def test_single_win():
    """一场单打胜利"""
    rec = _make_record(card_code=CARD_A, team_size=1, is_winner=1,
                       score_self=21, score_opponent=15,
                       rating_before=1500.0, delta=10.0)
    db = _make_db([rec])
    data = await get_player_records(db, CARD_A)
    assert data.summary.total_matches == 1
    assert data.summary.total_singles == 1
    assert data.summary.total_doubles == 0
    assert data.summary.wins == 1
    assert data.summary.losses == 0
    assert data.summary.win_rate == 1.0
    assert data.summary.avg_score_self == 21.0
    assert data.summary.avg_score_opponent == 15.0
    assert data.summary.avg_delta == 10.0

    r0 = data.records[0]
    assert r0.is_winner is True
    assert r0.score_self == 21
    assert r0.score_opponent == 15
    assert r0.rating_before == 1500.0
    assert r0.rating_after == 1510.0
    assert r0.delta == 10.0
    assert r0.opponent_card_code == CARD_B
    assert r0.opponent_partner_card_code is None


@pytest.mark.asyncio
async def test_mixed_singles_doubles_loss():
    """混单打+双打，含负场与双打对手搭档"""
    recs = [
        _make_record(card_code=CARD_A, team_size=1, is_winner=1,
                     score_self=21, score_opponent=18, rate_id=1),
        _make_record(card_code=CARD_A, team_size=2, is_winner=0,
                     score_self=15, score_opponent=21, rate_id=2,
                     opponent_card_code=CARD_B),
    ]
    # 双打时补搭档信息
    recs[1].opponent_partner_card_code = "110101198801018888"
    db = _make_db(recs)
    data = await get_player_records(db, CARD_A)

    # 应用层按 (time, id) 倒序：recs[1](id=2) 在前，recs[0](id=1) 在后
    #（PlayerRecord 不含 id，按 team_size 区分：双打 id=2 应在前）
    assert [r.team_size for r in data.records] == [2, 1]

    assert data.summary.total_matches == 2
    assert data.summary.total_singles == 1
    assert data.summary.total_doubles == 1
    assert data.summary.wins == 1
    assert data.summary.losses == 1
    assert data.summary.win_rate == 0.5

    doubles = [r for r in data.records if r.team_size == 2][0]
    assert doubles.is_winner is False
    assert doubles.opponent_partner_card_code == "110101198801018888"


@pytest.mark.asyncio
async def test_order_by_played_at_desc():
    """按时间倒序：最近的在前"""
    recs = [
        _make_record(played_at=datetime(2026, 1, 1), rate_id=1),
        _make_record(played_at=datetime(2026, 5, 1), rate_id=2),
        _make_record(played_at=datetime(2026, 3, 1), rate_id=3),
    ]
    db = _make_db(recs)
    data = await get_player_records(db, CARD_A)
    dates = [r.played_at for r in data.records]
    assert dates == [
        datetime(2026, 5, 1),
        datetime(2026, 3, 1),
        datetime(2026, 1, 1),
    ]


@pytest.mark.asyncio
async def test_null_played_at_last():
    """played_at 为 None 的记录排在最后"""
    recs = [
        _make_record(card_code=CARD_A, played_at=None, rate_id=1),
        _make_record(card_code=CARD_A, played_at=datetime(2026, 5, 1), rate_id=2),
    ]
    db = _make_db(recs)
    data = await get_player_records(db, CARD_A)
    assert data.records[0].played_at == datetime(2026, 5, 1)
    assert data.records[1].played_at is None
