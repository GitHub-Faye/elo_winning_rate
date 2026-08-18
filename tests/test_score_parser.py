"""测试 score_parser 模块"""
import pytest
from core.score_parser import parse_item_score, parse_item_score_games


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


class TestParseItemScoreGames:
    """测试 parse_item_score_games 函数"""

    def test_single_game(self):
        """单局返回单元素列表"""
        assert parse_item_score_games("21:11") == [(21, 11)]

    def test_two_games(self):
        """两局返回两元素列表"""
        assert parse_item_score_games("21:11|21:14") == [(21, 11), (21, 14)]

    def test_three_games(self):
        """三局返回三元素列表"""
        assert parse_item_score_games("21:11|18:21|12:21") == [
            (21, 11), (18, 21), (12, 21),
        ]

    def test_forfeit(self):
        """弃权局"""
        assert parse_item_score_games("0:21|0:21") == [(0, 21), (0, 21)]

    def test_none_input(self):
        """None 返回 None"""
        assert parse_item_score_games(None) is None

    def test_empty_string(self):
        """空字符串返回 None"""
        assert parse_item_score_games("") is None

    def test_whitespace_only(self):
        """纯空格返回 None"""
        assert parse_item_score_games("   ") is None

    def test_whitespace_handling(self):
        """带空格的输入"""
        assert parse_item_score_games(" 21:11 | 21:14 ") == [(21, 11), (21, 14)]

    def test_fullwidth_chars(self):
        """全角冒号和竖线"""
        assert parse_item_score_games("21：11｜21：14") == [(21, 11), (21, 14)]

    def test_invalid_format(self):
        """无效格式抛出 ValueError"""
        with pytest.raises(ValueError, match="无效的比分格式"):
            parse_item_score_games("21-11")

    def test_non_numeric(self):
        """非数字抛出 ValueError"""
        with pytest.raises(ValueError, match="比分必须是整数"):
            parse_item_score_games("abc:def")

    def test_negative_score(self):
        """负数抛出 ValueError"""
        with pytest.raises(ValueError, match="比分不能为负数"):
            parse_item_score_games("-1:21")

    def test_score_too_high(self):
        """比分超范围抛出 ValueError"""
        with pytest.raises(ValueError, match="比分超出合理范围"):
            parse_item_score_games("32:21")

    def test_empty_games_string(self):
        """有效格式但无局数据"""
        with pytest.raises(ValueError, match="比分字符串为空或无有效局"):
            parse_item_score_games("|")

    def test_matches_total_score(self):
        """逐局列表的总分与 parse_item_score 一致"""
        score_str = "21:11|18:21|12:21"
        total = parse_item_score(score_str)
        games = parse_item_score_games(score_str)
        assert sum(g[0] for g in games) == total[0]
        assert sum(g[1] for g in games) == total[1]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
