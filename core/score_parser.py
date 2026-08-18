"""item_score 比分解析器

解析 motion_event_layout_stage_battle.item_score 字段，
将 "21:11|18:21" 格式的比分字符串转换为总分或逐局列表。

支持格式:
- 单局: "31:27" → (31, 27)
- 多局: "21:11|21:14" → (42, 25)
- 三局: "21:11|18:21|12:21" → (51, 53)
- 弃权: "0:21|0:21" → (0, 42)
"""
from __future__ import annotations

from typing import Optional, Tuple


def parse_item_score(
    item_score: Optional[str],
) -> Tuple[Optional[int], Optional[int]]:
    """解析 item_score 字符串，返回 (score_a, score_b) 总分。

    Args:
        item_score: 比分字符串，格式为 "A:B" 或 "A:B|A:B|..."

    Returns:
        (score_a, score_b) 总分，或 (None, None) 如果输入为空

    Raises:
        ValueError: 格式异常或比分为负数
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

        total_a += a
        total_b += b

    # 验证总分不为 0
    if total_a == 0 and total_b == 0:
        raise ValueError(f"总分为 0，可能是无效数据: '{item_score}'")

    return total_a, total_b


def parse_item_score_games(
    item_score: Optional[str],
) -> Optional[list[tuple[int, int]]]:
    """解析 item_score 字符串，返回逐局比分列表。

    与 parse_item_score 的区别：返回每一局的独立比分，而非总分。
    用于逐局 Elo 计算场景——每局独立计算 Elo 变化，再取均值。

    Args:
        item_score: 比分字符串，格式为 "A:B" 或 "A:B|A:B|..."

    Returns:
        [(game_a, game_b), ...] 逐局比分列表，或 None 如果输入为空

    Raises:
        ValueError: 格式异常或比分为负数

    示例:
        >>> parse_item_score_games("21:11|21:14")
        [(21, 11), (21, 14)]
        >>> parse_item_score_games("35:11")
        [(35, 11)]
    """
    if item_score is None or (isinstance(item_score, str) and not item_score.strip()):
        return None

    games: list[tuple[int, int]] = []

    # 支持全角字符（中文输入法）
    normalized = item_score.replace("：", ":").replace("｜", "|")

    # 按 | 分割各局
    raw_games = normalized.split("|")

    for game in raw_games:
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

        games.append((a, b))

    # 验证至少有一局
    if not games:
        raise ValueError(f"比分字符串为空或无有效局: '{item_score}'")

    return games
