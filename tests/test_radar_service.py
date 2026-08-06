"""Tests for radar service — 六维雷达图纯函数（无 DB 依赖）+ async 链路

覆盖：
  - calc_offense_defense：发球权/接发权得分率 → 归一化攻守
  - calc_consecutive：连胜/连失分段
  - calc_anti_pressure：D/L/R/K/E 公式、权重平均
  - calc_field：换边前后场区落差
  - profile_player_by_card：身份证找不到 → ValueError
"""
from __future__ import annotations

from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from services.radar_service import (
    calc_anti_pressure,
    calc_consecutive,
    calc_field,
    calc_offense_defense,
    profile_player_by_card,
)

# 工具：构造一局事件序列
def _mk(my_serve: Optional[bool], is_my_score: bool, bt: int = 2, sides: bool = False) -> dict:
    return {"behavior_type": bt, "is_my_score": is_my_score, "my_serve": my_serve, "is_sides": sides}


class TestOffenseDefense:
    def test_serve_scoring_rate(self):
        """进攻=发球权得分率，接发=接发权得分率（归一化后）"""
        games = {1: [
            _mk(True, True),   # 我发球得分 → 进攻+
            _mk(True, False),  # 我发球丢分
            _mk(False, True),  # 我接发得分
            _mk(False, False), # 我接发丢分
        ]}
        ods = calc_offense_defense(games)
        # 进攻：发球2回合得1分 = 50%
        assert ods["offense"] == 50.0
        assert ods["offense_raw"] == 50.0
        # 接发：接发2回合得1分 = 50% → 归一化 50
        assert ods["receive_raw"] == 50.0
        assert ods["receive"] == 50.0

    def test_uneven_serve(self):
        """发球权不均衡时仍按各自回合数算得分率"""
        games = {1: [
            _mk(True, True), _mk(True, True), _mk(True, False),  # 发球3回合得2 = 66.7%
            _mk(False, False),                                    # 接发1回合丢 = 0%
        ]}
        ods = calc_offense_defense(games)
        assert ods["offense_raw"] > 60
        assert ods["offense"] > 60
        assert ods["receive_raw"] == 0.0
        assert ods["receive"] < 1

    def test_no_serve_data_defaults(self):
        """无发球数据 → 50 分默认"""
        games = {1: [{"behavior_type": 2, "is_my_score": True, "my_serve": None}]}
        ods = calc_offense_defense(games)
        assert ods["offense"] == 50.0
        assert ods["receive"] == 50.0


class TestConsecutive:
    def test_streak_segmentation(self):
        """连胜/连失分段正确切分"""
        games = {1: [
            _mk(None, True), _mk(None, True),   # 连胜2
            _mk(None, False), _mk(None, False), # 连失2
            _mk(None, True),                     # 连胜1
            _mk(None, False), _mk(None, False), _mk(None, False), # 连失3
        ]}
        c = calc_consecutive(games)
        assert c["avg_score"] == 1.5   # [2,1] → 3/2
        assert c["avg_lose"] == 2.5    # [2,3] → 5/2
        assert c["max_score"] == 2
        assert c["max_lose"] == 3


class TestAntiPressure:
    def test_comeback_win(self):
        """落后翻盘：D>0 R=1 → 加成分高"""
        # 模拟 0:2 落后后连拿 3 分以 5:2 赢下
        games = {1: [
            _mk(None, False), _mk(None, False),  # 0:2 落后 D=2
            _mk(None, True),  _mk(None, True),   # 追到 2:2
            _mk(None, True),  _mk(None, True),   # 打到 4:2
            _mk(None, True),                      # 5:2 胜
        ]}
        r = calc_anti_pressure(games)
        assert r["games"][0]["D"] == 2
        assert r["games"][0]["R"] == 1
        assert r["games"][0]["S"] >= 70  # 翻盘加成明显

    def test_leading_no_pressure(self):
        """全程领先（无逆风）→ R=0.5 基础分"""
        games = {1: [_mk(None, True)] * 5}
        r = calc_anti_pressure(games)
        assert r["games"][0]["D"] == 0
        assert r["games"][0]["R"] == 0.5
        assert 0 < r["games"][0]["S"] <= 100


class TestField:
    def test_stable_sides_high(self):
        """换边前后落差小 → 场区高分"""
        # 前半段 5:3，后半段 5:2 → 得分差0，失分差1 → 高分
        games = {1: [
            _mk(None, True), _mk(None, True), _mk(None, True),
            _mk(None, True), _mk(None, True),          # A段我方5
            _mk(None, False), _mk(None, False), _mk(None, False),  # A段对方3
            {"behavior_type": 3, "is_sides": True, "is_my_score": False, "my_serve": None},  # 换边
            _mk(None, True), _mk(None, True), _mk(None, True),
            _mk(None, True), _mk(None, True), _mk(None, True),       # B段我方5
            _mk(None, False), _mk(None, False), _mk(None, False),    # B段对方2
        ]}
        r = calc_field(games)
        fs = r["games"][0]["score"]
        assert fs > 70

    def test_unstable_sides_low(self):
        """换边前后落差大 → 场区低分（崩盘）"""
        games = {1: (
            [_mk(None, True)] * 10 +      # A段我方10分
            [_mk(None, False)] * 2 +      # A段对方得2
            [{"behavior_type": 3, "is_sides": True, "is_my_score": False, "my_serve": None}] +  # 换边
            [_mk(None, False)] * 5 +      # B段对方5分（我方0）
            [_mk(None, True)] * 2         # B段对方再得2（我方仍0）
        )}
        r = calc_field(games)
        fs = r["games"][0]["score"]
        assert fs < 60  # 得分落差大 → 低分


@pytest.mark.asyncio
async def test_profile_unknown_card():
    """身份证找不到选手 → ValueError"""
    db = AsyncMock(spec=AsyncSession)
    ex = MagicMock()
    ex.fetchone.return_value = None
    db.execute = AsyncMock(return_value=ex)
    with pytest.raises(ValueError):
        await profile_player_by_card(db, "999999999999999999")
