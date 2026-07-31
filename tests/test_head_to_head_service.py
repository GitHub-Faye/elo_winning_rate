"""Tests for head-to-head service — mock AsyncSession 实现可测试性"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from services.head_to_head_service import get_head_to_head

# 测试用身份证号
CARD_A = "110101199001011234"
CARD_B = "110101199202024567"


def _make_record(
    event_id: int = 1,
    battle_id: int = 1,
    card_code: str = CARD_A,
    team_side: str = "A",
    team_size: int = 1,
    is_winner: int = 1,
    score_self: int = 21,
    score_opponent: int = 15,
    opponent_card_code: str = CARD_B,
    played_at=None,
):
    """构造一条 EloMatchRecord（用 SimpleNamespace 模拟）。"""
    from types import SimpleNamespace
    return SimpleNamespace(
        id=None,
        event_id=event_id,
        battle_id=battle_id,
        source_order=0,
        card_code=card_code,
        team_side=team_side,
        team_size=team_size,
        is_winner=is_winner,
        rating_before=1500.0,
        delta=10.0,
        rating_after=1510.0,
        expected=0.5,
        k_factor=32.0,
        weight_multiplier=1.0,
        margin_multiplier=1.0,
        base_delta=10.0,
        clamped_delta=10.0,
        upset_bonus=0.0,
        upset_penalty=0.0,
        opponent_card_code=opponent_card_code,
        opponent_partner_card_code=None,
        score_self=score_self,
        score_opponent=score_opponent,
        played_at=played_at,
    )


def _make_db(head_to_head_records: list) -> AsyncMock:
    """创建 mock DB，返回给定的交手记录。

    execute 调用顺序：
      1. player_a 的比赛记录查询 → records_a
      2. player_b 的比赛记录查询 → records_b
    """
    db = AsyncMock(spec=AsyncSession)

    e1 = MagicMock()
    e2 = MagicMock()

    # 按 card_code 分配记录
    records_a = [r for r in head_to_head_records if r.card_code == CARD_A]
    records_b = [r for r in head_to_head_records if r.card_code == CARD_B]

    e1.scalars().all.return_value = records_a
    e2.scalars().all.return_value = records_b

    db.execute = AsyncMock(side_effect=[e1, e2])
    return db


@pytest.mark.asyncio
async def test_no_matches():
    """两人没有交手记录 → total_matches=0"""
    db = _make_db([])
    data = await get_head_to_head(db, CARD_A, CARD_B)
    assert data.total_matches == 0
    assert data.a_wins == 0
    assert data.b_wins == 0
    assert data.records == []


@pytest.mark.asyncio
async def test_one_match_a_wins():
    """一场比赛，A 胜"""
    rec_a = _make_record(card_code=CARD_A, team_side="A", is_winner=1,
                         score_self=21, score_opponent=15)
    rec_b = _make_record(card_code=CARD_B, team_side="B", is_winner=0,
                         score_self=15, score_opponent=21)
    db = _make_db([rec_a, rec_b])
    data = await get_head_to_head(db, CARD_A, CARD_B)
    assert data.total_matches == 1
    assert data.a_wins == 1
    assert data.b_wins == 0
    assert len(data.records) == 1
    assert data.records[0].score_a == 21
    assert data.records[0].score_b == 15
    assert data.records[0].winner_card == CARD_A


@pytest.mark.asyncio
async def test_one_match_b_wins():
    """一场比赛，B 胜"""
    rec_a = _make_record(card_code=CARD_A, team_side="A", is_winner=0,
                         score_self=14, score_opponent=21)
    rec_b = _make_record(card_code=CARD_B, team_side="B", is_winner=1,
                         score_self=21, score_opponent=14)
    db = _make_db([rec_a, rec_b])
    data = await get_head_to_head(db, CARD_A, CARD_B)
    assert data.total_matches == 1
    assert data.a_wins == 0
    assert data.b_wins == 1
    assert data.records[0].winner_card == CARD_B


@pytest.mark.asyncio
async def test_two_matches():
    """两场比赛，各赢一场"""
    recs = [
        # 第一场 CARD_A(A) 胜
        _make_record(event_id=1, battle_id=1, card_code=CARD_A, team_side="A",
                     is_winner=1, score_self=21, score_opponent=18),
        _make_record(event_id=1, battle_id=1, card_code=CARD_B, team_side="B",
                     is_winner=0, score_self=18, score_opponent=21),
        # 第二场 CARD_A(B) 负 → CARD_B(A) 胜
        _make_record(event_id=1, battle_id=2, card_code=CARD_A, team_side="B",
                     is_winner=0, score_self=20, score_opponent=22),
        _make_record(event_id=1, battle_id=2, card_code=CARD_B, team_side="A",
                     is_winner=1, score_self=22, score_opponent=20),
    ]
    db = _make_db(recs)
    data = await get_head_to_head(db, CARD_A, CARD_B)
    assert data.total_matches == 2
    assert data.a_wins == 1
    assert data.b_wins == 1

    # 第二场 CARD_A 在 B 方且输了，比分应该翻转
    match2 = [r for r in data.records if r.battle_id == 2][0]
    assert match2.score_a == 22  # A 方视角：对手得分 → score_a
    assert match2.score_b == 20
    assert match2.winner_card == CARD_B


@pytest.mark.asyncio
async def test_doubles_match():
    """双打比赛交手记录"""
    rec_a = _make_record(event_id=4, battle_id=1, card_code=CARD_A, team_side="A",
                         team_size=2, is_winner=1, score_self=22, score_opponent=20)
    rec_b = _make_record(event_id=4, battle_id=1, card_code=CARD_B, team_side="B",
                         team_size=2, is_winner=0, score_self=20, score_opponent=22)
    db = _make_db([rec_a, rec_b])
    data = await get_head_to_head(db, CARD_A, CARD_B)
    assert data.total_matches == 1
    assert data.records[0].team_size == 2
    assert data.records[0].winner_card == CARD_A


@pytest.mark.asyncio
async def test_same_team_ignored():
    """同一方（未发生对位）的记录应被忽略"""
    rec_a = _make_record(card_code=CARD_A, team_side="A")
    rec_b = _make_record(card_code=CARD_B, team_side="A")  # 同一方
    db = _make_db([rec_a, rec_b])
    data = await get_head_to_head(db, CARD_A, CARD_B)
    assert data.total_matches == 0


@pytest.mark.asyncio
async def test_multiple_events():
    """跨赛事的交手记录"""
    recs = [
        # 赛事1
        _make_record(event_id=1, battle_id=1, card_code=CARD_A, team_side="A",
                     is_winner=1, score_self=21, score_opponent=15),
        _make_record(event_id=1, battle_id=1, card_code=CARD_B, team_side="B",
                     is_winner=0, score_self=15, score_opponent=21),
        # 赛事2
        _make_record(event_id=2, battle_id=3, card_code=CARD_A, team_side="A",
                     is_winner=0, score_self=10, score_opponent=21),
        _make_record(event_id=2, battle_id=3, card_code=CARD_B, team_side="B",
                     is_winner=1, score_self=21, score_opponent=10),
    ]
    db = _make_db(recs)
    data = await get_head_to_head(db, CARD_A, CARD_B)
    assert data.total_matches == 2
    assert data.a_wins == 1
    assert data.b_wins == 1
    assert len(data.records) == 2
    # 按 battle_id 排序（实际返回按遍历顺序）
    event_ids = {r.event_id for r in data.records}
    assert event_ids == {1, 2}


@pytest.mark.asyncio
async def test_three_matches_one_side():
    """A 赢 3 场"""
    recs = []
    for battle_id in range(1, 4):
        recs.append(_make_record(event_id=1, battle_id=battle_id,
                                 card_code=CARD_A, team_side="A", is_winner=1,
                                 score_self=21, score_opponent=15))
        recs.append(_make_record(event_id=1, battle_id=battle_id,
                                 card_code=CARD_B, team_side="B", is_winner=0,
                                 score_self=15, score_opponent=21))
    db = _make_db(recs)
    data = await get_head_to_head(db, CARD_A, CARD_B)
    assert data.total_matches == 3
    assert data.a_wins == 3
    assert data.b_wins == 0
