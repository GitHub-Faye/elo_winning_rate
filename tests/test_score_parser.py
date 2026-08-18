"""测试 score_parser 模块"""
import pytest
from core.score_parser import parse_item_score


class TestParseItemScore:
    """测试 parse_item_score 函数"""

    def test_single_game_basic(self):
        """测试单局比分"""
        assert parse_item_score("21:11") == (21, 11)

    def test_single_game_extended(self):
        """测试单局加分比分"""
        assert parse_item_score("31:27") == (31, 27)

    def test_multi_game_two(self):
        """测试两局比分"""
        assert parse_item_score("21:11|21:14") == (42, 25)

    def test_three_games(self):
        """测试三局比分"""
        assert parse_item_score("21:11|18:21|12:21") == (51, 53)

    def test_forfeit_game(self):
        """测试弃权"""
        assert parse_item_score("0:21|0:21") == (0, 42)

    def test_whitespace_handling(self):
        """测试空格处理"""
        assert parse_item_score(" 21:11 | 21:14 ") == (42, 25)

    def test_fullwidth_colon(self):
        """测试全角冒号"""
        assert parse_item_score("21：11") == (21, 11)

    def test_fullwidth_pipe(self):
        """测试全角竖线"""
        assert parse_item_score("21:11｜21:14") == (42, 25)

    def test_none_input(self):
        """测试 None 输入"""
        assert parse_item_score(None) == (None, None)

    def test_empty_string(self):
        """测试空字符串"""
        assert parse_item_score("") == (None, None)

    def test_whitespace_only(self):
        """测试只有空格"""
        assert parse_item_score("   ") == (None, None)

    def test_invalid_format_dash(self):
        """测试无效格式（横杠）"""
        with pytest.raises(ValueError, match="无效的比分格式"):
            parse_item_score("21-11")

    def test_non_numeric(self):
        """测试非数字"""
        with pytest.raises(ValueError, match="比分必须是整数"):
            parse_item_score("abc:def")

    def test_negative_score(self):
        """测试负数比分"""
        with pytest.raises(ValueError, match="比分不能为负数"):
            parse_item_score("-1:21")

    def test_score_too_high(self):
        """测试比分超出范围"""
        with pytest.raises(ValueError, match="比分超出合理范围"):
            parse_item_score("32:21")

    def test_total_zero(self):
        """测试总分为 0"""
        with pytest.raises(ValueError, match="总分为 0"):
            parse_item_score("0:0")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
