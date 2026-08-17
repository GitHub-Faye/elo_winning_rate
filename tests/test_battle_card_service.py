"""测试 battle_card_service 的通用方法"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession

from services.battle_card_service import (
    get_card_codes_by_battle_id,
    get_battles_by_card_code,
)


@pytest.mark.asyncio
async def test_get_card_codes_singles():
    """测试单体赛路径"""
    # Mock 数据库查询
    db = AsyncMock(spec=AsyncSession)

    # Mock battle 查询结果
    battle_row = MagicMock()
    battle_row._mapping = {
        "battle_id": 123,
        "event_id": 456,
        "project_type": 1,
        "player_one_id": 1001,
        "player_two_id": 1002,
        "player_one_user_ids": None,
        "player_two_user_ids": None,
        "player_one_name": "选手A",
        "player_two_name": "选手B",
        "player_one_score": 21,
        "player_two_score": 15,
        "battle_time": "2026-08-17 10:00:00",
    }

    # Mock stage_player 查询结果
    stage_row1 = MagicMock()
    stage_row1.id = 1001
    stage_row1._mapping = {"apply_id": 7001, "player_user_ids": "5001,5002", "player_names": "选手A,选手A2"}

    stage_row2 = MagicMock()
    stage_row2.id = 1002
    stage_row2._mapping = {"apply_id": 7002, "player_user_ids": "5003,5004", "player_names": "选手B,选手B2"}

    # Mock user_setting 查询结果
    user_row1 = MagicMock()
    user_row1._mapping = {"user_setting_id": 5001, "card_code": "110101199001011234", "name": "选手A"}

    user_row2 = MagicMock()
    user_row2._mapping = {"user_setting_id": 5002, "card_code": "110101199001011235", "name": "选手A2"}

    user_row3 = MagicMock()
    user_row3._mapping = {"user_setting_id": 5003, "card_code": "110101199001011236", "name": "选手B"}

    user_row4 = MagicMock()
    user_row4._mapping = {"user_setting_id": 5004, "card_code": "110101199001011237", "name": "选手B2"}

    # 设置 mock 返回值
    db.execute = AsyncMock(side_effect=[
        MagicMock(fetchone=MagicMock(return_value=battle_row)),
        MagicMock(fetchall=MagicMock(return_value=[stage_row1, stage_row2])),
        MagicMock(fetchall=MagicMock(return_value=[user_row1, user_row2])),
        MagicMock(fetchall=MagicMock(return_value=[user_row3, user_row4])),
    ])

    # 执行测试
    result = await get_card_codes_by_battle_id(db, 123)

    # 验证结果
    assert result is not None
    assert result["battle_id"] == 123
    assert result["project_type"] == 1
    assert len(result["team_a"]) == 2
    assert len(result["team_b"]) == 2
    assert result["is_valid"] is True


@pytest.mark.asyncio
async def test_get_card_codes_doubles():
    """测试团体赛路径"""
    # Mock 数据库查询
    db = AsyncMock(spec=AsyncSession)

    # Mock battle 查询结果
    battle_row = MagicMock()
    battle_row._mapping = {
        "battle_id": 456,
        "event_id": 789,
        "project_type": 2,
        "player_one_id": None,
        "player_two_id": None,
        "player_one_user_ids": "5001,5002",
        "player_two_user_ids": "5003,5004",
        "player_one_name": "队伍A",
        "player_two_name": "队伍B",
        "player_one_score": 21,
        "player_two_score": 18,
        "battle_time": "2026-08-17 11:00:00",
    }

    # Mock user_setting 查询结果
    user_row1 = MagicMock()
    user_row1._mapping = {"user_setting_id": 5001, "card_code": "110101199001011234", "name": "选手A1"}

    user_row2 = MagicMock()
    user_row2._mapping = {"user_setting_id": 5002, "card_code": "110101199001011235", "name": "选手A2"}

    user_row3 = MagicMock()
    user_row3._mapping = {"user_setting_id": 5003, "card_code": "110101199001011236", "name": "选手B1"}

    user_row4 = MagicMock()
    user_row4._mapping = {"user_setting_id": 5004, "card_code": "110101199001011237", "name": "选手B2"}

    # 设置 mock 返回值
    db.execute = AsyncMock(side_effect=[
        MagicMock(fetchone=MagicMock(return_value=battle_row)),
        MagicMock(fetchall=MagicMock(return_value=[user_row1, user_row2])),
        MagicMock(fetchall=MagicMock(return_value=[user_row3, user_row4])),
    ])

    # 执行测试
    result = await get_card_codes_by_battle_id(db, 456)

    # 验证结果
    assert result is not None
    assert result["battle_id"] == 456
    assert result["project_type"] == 2
    assert len(result["team_a"]) == 2
    assert len(result["team_b"]) == 2
    assert result["is_valid"] is True


@pytest.mark.asyncio
async def test_get_card_codes_not_found():
    """测试比赛不存在的情况"""
    db = AsyncMock(spec=AsyncSession)

    # Mock 返回空结果
    db.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))

    result = await get_card_codes_by_battle_id(db, 999999)

    assert result is None


@pytest.mark.asyncio
async def test_get_battles_by_card_code():
    """测试根据身份证号查询对阵"""
    db = AsyncMock(spec=AsyncSession)

    # Mock user_setting 查询结果
    user_row = MagicMock()
    user_row._mapping = {
        "user_setting_id": 5001,
        "event_id": 123,
        "name": "测试选手",
    }

    # Mock battle 查询结果
    battle_row1 = MagicMock()
    battle_row1._mapping = {
        "battle_id": 1001,
        "event_id": 123,
        "player_one_name": "选手A",
        "player_two_name": "选手B",
        "player_one_user_ids": "5001,5002",
        "player_two_user_ids": "5003,5004",
        "player_one_score": 21,
        "player_two_score": 15,
        "battle_time": "2026-08-17 10:00:00",
        "project_type": 2,
    }

    battle_row2 = MagicMock()
    battle_row2._mapping = {
        "battle_id": 1002,
        "event_id": 123,
        "player_one_name": "选手C",
        "player_two_name": "选手A",
        "player_one_user_ids": "5005,5006",
        "player_two_user_ids": "5001,5002",
        "player_one_score": 18,
        "player_two_score": 21,
        "battle_time": "2026-08-17 11:00:00",
        "project_type": 2,
    }

    # 设置 mock 返回值
    db.execute = AsyncMock(side_effect=[
        MagicMock(fetchone=MagicMock(return_value=user_row)),
        MagicMock(fetchall=MagicMock(return_value=[battle_row1, battle_row2])),
    ])

    # 执行测试
    result = await get_battles_by_card_code(db, "110101199001011234")

    # 验证结果
    assert len(result) == 2
    assert result[0]["battle_id"] == 1001
    assert result[1]["battle_id"] == 1002


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
