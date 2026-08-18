"""item_score 比分解析器

解析 motion_event_layout_stage_battle.item_score 字段，
将 "21:11|18:21" 格式的比分字符串转换为 (score_a, score_b) 总分。

支持格式:
- 单局: "31:27" → (31, 27)
- 多局: "21:11|21:14" → (42, 25)
- 三局: "21:11|18:21|12:21" → (51, 53)
- 弃权: "0:21|0:21" → (0, 42)
"""
from __future__ import annotations

from typing import Optional, Tuple


# 羽毛球单局最高分（正常 21 分，加分上限 30 分）
MAX_GAME_SCORE = 31


def parse_item_score(
    item_score: Optional[str],
    strict: bool = True,
) -> Tuple[Optional[int], Optional[int]]:
    """解析 item_score 字符串，返回 (score_a, score_b) 总分。

    Args:
        item_score: 比分字符串，格式为 "A:B" 或 "A:B|A:B|..."
        strict: 是否严格校验比分范围（0-MAX_GAME_SCORE）

    Returns:
        (score_a, score_b) 总分，或 (None, None) 如果输入为空

    Raises:
        ValueError: 格式异常或比分超出合理范围
    """
    if item_score is None or (isinstance(item_score, str) and not item_score.strip()):
        return None, None

    total_a = 0
    total_b = 0

    # 支持全角字符（中文输入法）
    normalized = item_score.replace("：", ":").replace("｜", "|")

    # 按 | 分割各局
    games = normalized.split("|")

    for game in games:
        game = game.strip()
        if not game:
            continue

        # 按 : 分割每局比分
        parts = game.split(":")
        if len(parts) != 2:
            raise ValueError(f"无效的比分格式: '{game}'（应为 'A:B' 格式）")

        try:
            a = int(parts[0].strip())
            b = int(parts[1].strip())
        except ValueError:
            raise ValueError(f"比分必须是整数: '{game}'")

        # 验证比分非负
        if a < 0 or b < 0:
            raise ValueError(f"比分不能为负数: '{game}'")

        # 严格模式下验证比分范围
        if strict and (a > MAX_GAME_SCORE or b > MAX_GAME_SCORE):
            raise ValueError(
                f"比分超出合理范围 (0-{MAX_GAME_SCORE}): '{game}'"
            )

        total_a += a
        total_b += b

    # 验证总分不为 0
    if total_a == 0 and total_b == 0:
        raise ValueError(f"总分为 0，可能是无效数据: '{item_score}'")

    return total_a, total_b
