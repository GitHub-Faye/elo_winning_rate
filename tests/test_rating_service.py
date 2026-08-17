"""Tests for rating service — mock AsyncSession 实现可测试性

覆盖：
  - 单查/批量查询积分 → 段位
  - 新选手（无 rating 记录）→ 默认 1500 + 定级中
  - 段位边界（1900=9段 → 1200=2段，1199=1段）
  - 批量部分缺失（未注册选手）→ 返回 null
  - 空列表 / 重复身份证
  - 地区排名（province/city 筛选 + 排名计算）
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas import RatingQueryRequest
from services.rating_service import (
    get_badminton_rank,
    get_player_ratings,
    get_player_ratings_by_card,
    get_player_ratings_with_ranking,
)

CARD_A = "110101199001011234"
CARD_B = "110101199202024567"
CARD_C = "110101199303036789"


# ── 段位映射纯函数 ──


class TestBadmintonRank:
    """get_badminton_rank 段位映射"""

    def test_rank_thresholds(self):
        """各门槛对应段位（四舍五入后比较，.5 进位）"""
        assert get_badminton_rank(1900.0, 2) == "9段"
        assert get_badminton_rank(1899.5, 2) == "9段"  # 进位到 1900
        assert get_badminton_rank(1899.49, 2) == "8段"
        assert get_badminton_rank(1800.0, 2) == "8段"
        assert get_badminton_rank(1799.49, 2) == "7段"
        assert get_badminton_rank(1700.0, 2) == "7段"
        assert get_badminton_rank(1699.49, 2) == "6段"
        assert get_badminton_rank(1600.0, 2) == "6段"
        assert get_badminton_rank(1599.49, 2) == "5段"
        assert get_badminton_rank(1500.0, 2) == "5段"
        assert get_badminton_rank(1499.49, 2) == "4段"
        assert get_badminton_rank(1400.0, 2) == "4段"
        assert get_badminton_rank(1399.49, 2) == "3段"
        assert get_badminton_rank(1300.0, 2) == "3段"
        assert get_badminton_rank(1299.49, 2) == "2段"
        assert get_badminton_rank(1200.0, 2) == "2段"
        assert get_badminton_rank(1199.49, 2) == "1段"
        assert get_badminton_rank(100.0, 2) == "1段"

    def test_rank_below_2_games_is_provisional(self):
        """场次 < 2 → 定级中（不看分数）"""
        assert get_badminton_rank(3000.0, 0) == "定级中"
        assert get_badminton_rank(1900.0, 1) == "定级中"
        assert get_badminton_rank(1500.0, 2) == "5段"

    def test_rank_float_rounding(self):
        """分数四舍五入后取段位（int(x + 0.5)）"""
        assert get_badminton_rank(1899.5, 2) == "9段"  # → 1900
        assert get_badminton_rank(1799.4, 2) == "7段"  # → 1799


# ── 服务层（mock DB） ──


def _make_db(rows: list) -> AsyncMock:
    """创建 mock DB，execute 返回给定 rating 行。"""
    db = AsyncMock(spec=AsyncSession)
    ex = MagicMock()
    ex.scalars().all.return_value = rows
    db.execute = AsyncMock(return_value=ex)
    return db


def _make_row(card_code: str, rating: float, games: int, wins: int = 0, losses: int = 0):
    """构造一条 EloPlayerRating（SimpleNamespace）。"""
    from types import SimpleNamespace
    return SimpleNamespace(
        card_code=card_code,
        rating=rating,
        games=games,
        wins=wins,
        losses=losses,
    )


@pytest.mark.asyncio
async def test_get_ratings_existing_players():
    """已建档选手：返回积分/场次/段位"""
    rows = [
        _make_row(CARD_A, 1764.32, 30, 20, 10),
        _make_row(CARD_B, 1512.00, 8, 5, 3),
    ]
    db = _make_db(rows)
    data = await get_player_ratings(db, [CARD_A, CARD_B], "badminton")

    assert data.results[0].card_code == CARD_A
    assert data.results[0].rating == 1764.32
    assert data.results[0].games == 30
    assert data.results[0].wins == 20
    assert data.results[0].losses == 10
    assert data.results[0].rank == "7段"  # 1764 → 7段
    assert data.results[0].is_provisional is False

    assert data.results[1].card_code == CARD_B
    assert data.results[1].rank == "5段"  # 1512 → 5段


@pytest.mark.asyncio
async def test_get_ratings_new_player_defaults():
    """无 rating 记录的新选手 → 默认 1500 + 定级中"""
    db = _make_db([])
    data = await get_player_ratings(db, [CARD_A], "badminton")

    assert len(data.results) == 1
    assert data.results[0].card_code == CARD_A
    assert data.results[0].rating == 1500.0
    assert data.results[0].games == 0
    assert data.results[0].rank == "定级中"
    assert data.results[0].is_provisional is True
    assert data.results[0].is_new is True


@pytest.mark.asyncio
async def test_get_ratings_batch_partial_missing():
    """批量查询：部分选手未建档 → 按默认值返回（1500 + 定级中）"""
    rows = [_make_row(CARD_A, 1600.0, 5)]
    db = _make_db(rows)
    data = await get_player_ratings(db, [CARD_A, CARD_B], "badminton")

    assert len(data.results) == 2
    assert data.results[0].card_code == CARD_A
    assert data.results[0].rank == "6段"
    assert data.results[1].card_code == CARD_B
    assert data.results[1].rating == 1500.0
    assert data.results[1].rank == "定级中"
    assert data.results[1].is_new is True


@pytest.mark.asyncio
async def test_get_ratings_empty_list():
    """空列表 → 空结果"""
    db = _make_db([])
    data = await get_player_ratings(db, [], "badminton")
    assert data.results == []


@pytest.mark.asyncio
async def test_get_ratings_dedupes_duplicates():
    """重复身份证去重（用 set 查询）"""
    db = _make_db([_make_row(CARD_A, 1500.0, 3)])
    data = await get_player_ratings(db, [CARD_A, CARD_A], "badminton")
    assert len(data.results) == 1


@pytest.mark.asyncio
async def test_get_player_ratings_by_card_single():
    """按单身份证号查询 → 复用批量逻辑"""
    rows = [_make_row(CARD_A, 1650.0, 10)]
    db = _make_db(rows)
    result = await get_player_ratings_by_card(db, CARD_A, "badminton")
    assert result.card_code == CARD_A
    assert result.rating == 1650.0
    assert result.rank == "6段"  # 1650 → 6段


@pytest.mark.asyncio
async def test_query_request_validation():
    """请求体校验：非空、去重、上限"""
    with pytest.raises(Exception):
        RatingQueryRequest(card_codes=[])
    # 去重是静默行为，不是报错
    req = RatingQueryRequest(card_codes=[CARD_A, CARD_A])
    assert len(req.card_codes) == 1
    with pytest.raises(Exception):
        RatingQueryRequest(card_codes=[CARD_A] * 60)  # 超过上限报错


# ── 地区排名测试 ──


def _make_db_with_region(player_rows: list, region_rows: list) -> AsyncMock:
    """创建 mock DB，第一次 execute 返回 player_rows，第二次返回 region_rows。

    get_player_ratings_with_ranking 执行两次查询：
    1. 查询选手积分（scalars().all()）
    2. 查询地区已定级选手（.all()）
    """
    db = AsyncMock(spec=AsyncSession)

    # 第一次调用：查询选手积分
    ex1 = MagicMock()
    ex1.scalars().all.return_value = player_rows

    # 第二次调用：查询地区选手
    ex2 = MagicMock()
    ex2.all.return_value = region_rows

    db.execute = AsyncMock(side_effect=[ex1, ex2])
    return db


def _make_region_row(card_code: str, rating: float):
    """构造一条地区排名查询结果（SimpleNamespace）。"""
    from types import SimpleNamespace
    return SimpleNamespace(card_code=card_code, rating=rating)


@pytest.mark.asyncio
async def test_get_ratings_with_province_ranking():
    """省份筛选：返回正确的排名和总人数"""
    # 查询目标选手
    player_rows = [_make_row(CARD_A, 1650.0, 10, 7, 3)]
    # 地区已定级选手（3 人，CARD_A 排第 2）
    region_rows = [
        _make_region_row(CARD_B, 1700.0),  # 第 1 名
        _make_region_row(CARD_A, 1650.0),  # 第 2 名
        _make_region_row(CARD_C, 1500.0),  # 第 3 名
    ]
    db = _make_db_with_region(player_rows, region_rows)

    data = await get_player_ratings_with_ranking(
        db, [CARD_A], "badminton", province="山西省",
    )

    assert data.province == "山西省"
    assert data.city is None
    assert data.results[0].region_rank == 2
    assert data.results[0].region_total == 3


@pytest.mark.asyncio
async def test_get_ratings_with_city_ranking():
    """城市筛选优先于省份"""
    player_rows = [_make_row(CARD_A, 1650.0, 10)]
    region_rows = [
        _make_region_row(CARD_A, 1650.0),
        _make_region_row(CARD_B, 1500.0),
    ]
    db = _make_db_with_region(player_rows, region_rows)

    data = await get_player_ratings_with_ranking(
        db, [CARD_A], "badminton", province="山西省", city="太原市",
    )

    # city 优先，province 被忽略
    assert data.province == "山西省"
    assert data.city == "太原市"
    assert data.results[0].region_rank == 1
    assert data.results[0].region_total == 2


@pytest.mark.asyncio
async def test_get_ratings_no_region_filter():
    """无地区筛选时 rank/total 为 None"""
    player_rows = [_make_row(CARD_A, 1650.0, 10)]
    db = _make_db(player_rows)  # 只需一次查询

    data = await get_player_ratings_with_ranking(
        db, [CARD_A], "badminton",
    )

    assert data.province is None
    assert data.city is None
    assert data.results[0].region_rank is None
    assert data.results[0].region_total is None


@pytest.mark.asyncio
async def test_get_ratings_provisional_player_not_ranked():
    """定级中选手（games < 2）不参与排名，但返回 region_total"""
    player_rows = [_make_row(CARD_A, 1500.0, 1)]  # 只有 1 场，定级中
    region_rows = [
        _make_region_row(CARD_B, 1700.0),
        _make_region_row(CARD_C, 1500.0),
    ]
    db = _make_db_with_region(player_rows, region_rows)

    data = await get_player_ratings_with_ranking(
        db, [CARD_A], "badminton", province="山西省",
    )

    assert data.results[0].region_rank is None  # 定级中不参与排名
    assert data.results[0].region_total == 2


@pytest.mark.asyncio
async def test_get_ratings_new_player_not_in_region():
    """新选手（无记录）不参与排名，但返回 region_total"""
    player_rows = []  # 无记录
    region_rows = [
        _make_region_row(CARD_B, 1700.0),
    ]
    db = _make_db_with_region(player_rows, region_rows)

    data = await get_player_ratings_with_ranking(
        db, [CARD_A], "badminton", province="山西省",
    )

    assert data.results[0].is_new is True
    assert data.results[0].region_rank is None
    assert data.results[0].region_total == 1


@pytest.mark.asyncio
async def test_get_ratings_region_empty():
    """地区无已定级选手时 total=0"""
    player_rows = [_make_row(CARD_A, 1650.0, 10)]
    region_rows = []  # 地区无已定级选手
    db = _make_db_with_region(player_rows, region_rows)

    data = await get_player_ratings_with_ranking(
        db, [CARD_A], "badminton", province="山西省",
    )

    assert data.results[0].region_rank is None
    assert data.results[0].region_total == 0


@pytest.mark.asyncio
async def test_get_ratings_tie_breaking():
    """同分按 card_code 升序排序（lexicographic）"""
    player_rows = [_make_row(CARD_A, 1650.0, 10)]
    # 两个选手同分，CARD_A 字典序更小 → 排名更高
    region_rows = [
        _make_region_row(CARD_A, 1650.0),  # card_code 更小，排第 1
        _make_region_row(CARD_B, 1650.0),  # card_code 更大，排第 2
    ]
    db = _make_db_with_region(player_rows, region_rows)

    data = await get_player_ratings_with_ranking(
        db, [CARD_A], "badminton", province="山西省",
    )

    assert data.results[0].region_rank == 1  # CARD_A 字典序更小，排名更高
    assert data.results[0].region_total == 2


@pytest.mark.asyncio
async def test_rating_query_request_with_region():
    """POST 请求体支持 region 参数"""
    req = RatingQueryRequest(
        card_codes=[CARD_A],
        sport_type="badminton",
        province="山西省",
        city="太原市",
    )
    assert req.sport_type == "badminton"
    assert req.province == "山西省"
    assert req.city == "太原市"
