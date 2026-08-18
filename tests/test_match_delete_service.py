"""Tests for match_delete service — mock AsyncSession 实现可测试性

覆盖数据一致性语义:
  - 比赛不存在 -> deleted=False(路由映射 404),无任何执行
  - 单打:两选手均为最新之一场 -> 该场删除 + 两选手积分回滚,rating 退回赛前
  - 双打:四人同场 -> 四人各自回滚
  - 部分选手非最新一场(其个人最大 id 在别场) -> 该选手积分不动
  - 无全局 max_id(选手历史记录晚于本场) -> 视为最新,回滚
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from services.match_delete_service import delete_match

CARD_A = "110101199001011234"
CARD_B = "110101199202024567"
CARD_C = "110101199303036789"
CARD_D = "110101199404041122"


def _rec(
    rid: int,
    card_code: str,
    team_side: str = "A",
    team_size: int = 1,
    is_winner: int = 1,
    rating_before: float = 1500.0,
    delta: float = 10.0,
    rating_after: float = 1510.0,
):
    """构造一条 EloMatchRecord(SimpleNamespace)。"""
    return SimpleNamespace(
        id=rid, event_id=1, battle_id=100, source_order=0,
        card_code=card_code, team_side=team_side, team_size=team_size,
        is_winner=is_winner,
        rating_before=Decimal(str(rating_before)),
        delta=Decimal(str(delta)),
        rating_after=Decimal(str(rating_after)),
        opponent_card_code="x", opponent_partner_card_code=None,
    )


def _rec_id(
    rid: int = 0,
    card_code: str = CARD_A,
    **kwargs,
):
    """构造一条记录(支持 id 关键字,转发剩余关键字给 _rec)。"""
    kwargs.pop("id", None)  # 若调用方传了 id=,取为 rid(首个位置已绑定,忽略)
    return _rec(rid, card_code, **kwargs)


def _rating(
    card_code: str,
    rating: float,
    games: int,
    wins: int,
    losses: int,
):  # -> SimpleNamespace
    """构造一条 EloPlayerRating(SimpleNamespace)。"""
    return SimpleNamespace(
        card_code=card_code, sport_type="badminton",
        rating=Decimal(str(rating)), games=games, wins=wins, losses=losses,
        draws=0, highest_rating=Decimal("1600.00"), lowest_rating=Decimal("1390.00"),
    )


def _make_db(
    match_rows: list,
    player_ratings: list,
    global_max: list | None = None,
) -> AsyncMock:
    """创建 mock DB。

    delete_match 内部的 execute 调用顺序:
      1. 查该场比赛记录         -> match_rows
      2. 查各选手历史最大 id    -> global_max (注入)
      3. 逐选手(仅最新者)查 rating -> 对应 player_rating
      ...
      N. execute(delete)        -> MagicMock
    """
    db = AsyncMock(spec=AsyncSession)

    # e1: 查该场
    e1 = MagicMock()
    e1.scalars().all.return_value = match_rows

    # global max id 查询(第 2 次 execute)。返回各选手全部记录 id(含本场)。
    if global_max is None:
        global_max = [(r.card_code, r.id) for r in match_rows]
    e2 = MagicMock()
    e2.all.return_value = global_max

    # 与 delete_match 一致:只有本场为个人最新场的选手才会触发 rating 查询。
    # 按 dedupe 顺序(e1 返回顺序 + 首次出现)生成每个最新选手的 rating 查询事件。
    codes_order = list(dict.fromkeys(r.card_code for r in match_rows))
    max_id_in_match = {c: max(r.id for r in match_rows if r.card_code == c) for c in codes_order}
    global_max_map: dict[str, int] = {}
    for c, rid in global_max:
        if global_max_map.get(c, 0) < rid:
            global_max_map[c] = rid
    latest_codes = [c for c in codes_order if global_max_map.get(c, 0) == max_id_in_match[c]]
    rating_map = {r.card_code: r for r in player_ratings}

    calls = [e1, e2]
    for c in latest_codes:
        er = MagicMock()
        er.scalar_one_or_none.return_value = rating_map.get(c)
        calls.append(er)

    # 最后 delete 的 execute
    edel = MagicMock()
    calls.append(edel)

    db.execute = AsyncMock(side_effect=calls)
    db.commit = AsyncMock()
    return db


# ── 比赛不存在 ──


@pytest.mark.asyncio
async def test_delete_nonexistent_match():
    """比赛不存在 -> deleted=False,无任何执行(由路由映射 404)。"""
    db = AsyncMock(spec=AsyncSession)
    ex = MagicMock()
    ex.scalars().all.return_value = []
    db.execute = AsyncMock(return_value=ex)
    db.commit = AsyncMock()

    resp = await delete_match(db, 999)
    assert resp.data.deleted is False
    assert resp.data.total_records_deleted == 0
    assert resp.data.players_affected == []
    db.commit.assert_not_awaited()  # 无删除/回滚,不应提交


# ── 单打:全员最新一场,完整回滚 ──


@pytest.mark.asyncio
async def test_delete_singles_both_latest():
    """单打两人均为最新一场 -> 记录删除 + 两选手积分回滚。"""
    match_rows = [
        _rec(rid=10, card_code=CARD_A, team_side="A", is_winner=1,
             rating_before=1528.0, delta=14.5, rating_after=1542.5),
        _rec(rid=11, card_code=CARD_B, team_side="B", is_winner=0,
             rating_before=1512.0, delta=-14.5, rating_after=1497.5),
    ]
    ratings = [
        _rating(CARD_A, 1542.5, 9, 5, 4),
        _rating(CARD_B, 1497.5, 6, 2, 4),
    ]
    # global max:本场即最新(id 10/11 分别是 A/B 最大)
    global_max = [(CARD_A, 10), (CARD_B, 11), (CARD_A, 8), (CARD_B, 9)]
    db = _make_db(match_rows, ratings, global_max)
    db.add = MagicMock()

    resp = await delete_match(db, 100)

    assert resp.data.deleted is True
    assert resp.data.match_type == "singles"
    assert resp.data.total_records_deleted == 2
    assert resp.notice is None  # 无未回滚选手

    # 两选手均回滚
    assert len(resp.data.players_affected) == 2
    a = next(r for r in resp.data.players_affected if r.card_code == CARD_A)
    b = next(r for r in resp.data.players_affected if r.card_code == CARD_B)
    assert a.rollback is not None
    assert b.rollback is not None
    # 退回赛前
    a_rating = next(x for x in ratings if x.card_code == CARD_A)
    assert a_rating.rating == Decimal("1528.00")
    assert a_rating.games == 8
    assert a_rating.wins == 4
    b_rating = next(x for x in ratings if x.card_code == CARD_B)
    assert b_rating.rating == Decimal("1512.00")
    assert b_rating.games == 5
    assert b_rating.losses == 3
    # 响应里的回滚值对应
    assert a.rollback.rating_after == a_rating.rating
    assert b.rollback.rating_after == b_rating.rating
    assert a.rollback.is_latest_match is True


# ── 双打:四人同场全部最新,各自回滚 ──


@pytest.mark.asyncio
async def test_delete_doubles_all_latest():
    """双打四人各自回滚,记录删除全部 4 条。"""
    match_rows = [
        _rec(20, CARD_A, "A", 2, 1, 1528.00, 11.79, 1539.79),
        _rec(21, CARD_C, "A", 2, 1, 1545.00, 11.79, 1556.79),
        _rec(22, CARD_B, "B", 2, 0, 1485.00, -11.79, 1473.21),
        _rec(23, CARD_D, "B", 2, 0, 1439.00, -11.79, 1427.21),
    ]
    ratings = [
        _rating(CARD_A, 1539.79, 9, 5, 4),
        _rating(CARD_C, 1556.79, 6, 5, 1),
        _rating(CARD_B, 1473.21, 6, 2, 4),
        _rating(CARD_D, 1427.21, 4, 0, 4),
    ]
    global_max = [(r.card_code, r.id) for r in match_rows]  # 全部最新
    db = _make_db(match_rows, ratings, global_max)
    db.add = MagicMock()

    resp = await delete_match(db, 100)

    assert resp.data.match_type == "doubles"
    assert resp.data.total_records_deleted == 4
    assert resp.data.deleted is True
    assert len(resp.data.players_affected) == 4
    # 四人全部回滚
    assert all(r.rollback is not None for r in resp.data.players_affected)
    a = next(r for r in resp.data.players_affected if r.card_code == CARD_A)
    assert a.rollback.rating_after == 1528.00
    a_row = next(x for x in ratings if x.card_code == CARD_A)
    assert a_row.wins == 4  # 退回赛前 wins(=本场胜,减 1)
    d = next(x for x in ratings if x.card_code == CARD_D)
    assert d.losses == 3


# ── 部分选手非最新一场:积分不动 ──


@pytest.mark.asyncio
async def test_delete_singles_one_not_latest():
    """A 本场非其最新一场 -> A 积分不动,B 完整回滚。"""
    match_rows = [
        # A 在本场 id=5,但其个人最大 id=8(别场),故 A 非最新
        _rec(5, CARD_A, team_side="A", is_winner=1,
             rating_before=1500.0, delta=10.0, rating_after=1510.0),
        _rec(6, CARD_B, team_side="B", is_winner=0,
             rating_before=1500.0, delta=-10.0, rating_after=1490.0),
    ]
    ratings = [
        _rating(CARD_A, 1540.0, 9, 5, 4),   # 当前积分非本场结果(之后又打了)
        _rating(CARD_B, 1490.0, 6, 2, 4),
    ]
    # A 的最大 id 是 8(在别场),B 的最大 id 是 6(本场)
    global_max = [(CARD_A, 8), (CARD_A, 5), (CARD_B, 6)]
    db = _make_db(match_rows, ratings, global_max)
    db.add = MagicMock()

    resp = await delete_match(db, 100)

    # 记录照删
    assert resp.data.deleted is True
    assert resp.data.total_records_deleted == 2
    # A 未回滚,B 回滚
    a = next(r for r in resp.data.players_affected if r.card_code == CARD_A)
    b = next(r for r in resp.data.players_affected if r.card_code == CARD_B)
    assert a.rollback is None
    assert b.rollback is not None
    # A 积分完全未动
    a_rating = next(x for x in ratings if x.card_code == CARD_A)
    assert a_rating.rating == Decimal("1540.00")
    assert a_rating.games == 9
    # 有 notice 提示未回滚选手
    assert resp.notice is not None


# ── commit 被调用(事务生效) ──


@pytest.mark.asyncio
async def test_delete_commits():
    """删除+回滚后应提交一笔事务。"""
    match_rows = [
        _rec(10, CARD_A, "A", 1, 1, 1528.00, 14.5, 1542.5),
        _rec(11, CARD_B, "B", 1, 0, 1512.00, -14.5, 1497.5),
    ]
    ratings = [_rating(CARD_A, 1542.5, 9, 5, 4), _rating(CARD_B, 1497.5, 6, 2, 4)]
    global_max = [(CARD_A, 10), (CARD_B, 11)]
    db = _make_db(match_rows, ratings, global_max)
    db.add = MagicMock()

    await delete_match(db, 100)
    db.commit.assert_awaited_once()
