"""
Elo 胜负预测：基于 Elo 分的胜率估算

仅用于赛前参考，不参与 Elo 加减分计算。

预测公式:
    最终胜率 = clamp(1 / (1 + 10 ^ ((rating_b - rating_a) / scale)), 0.05, 0.95)
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class PlayerRecord:
    """选手在当前系统中的状态快照（预测只读，不修改）。"""

    player_id: str
    name: str
    rating: float
    """当前 Elo 分。"""
    games: int
    wins: int
    losses: int


@dataclass(frozen=True)
class PredictionResult:
    """胜负预测结果。"""

    player_a: PlayerRecord
    player_b: PlayerRecord

    probability_a: float
    """A 的最终预测胜率。"""
    probability_b: float
    """B 的最终预测胜率。"""

    elo_base_probability: float
    """Elo 基础胜率。"""
    elo_scale: float
    """该次预测使用的 Elo 敏感度。"""


# ──────────────────────────────────────────────
# 纯函数：预期胜率
# ──────────────────────────────────────────────


def expected_score(rating_a: float, rating_b: float, scale: float = 400.0) -> float:
    """标准 Elo 预期胜率。

    E = 1 / (1 + 10 ^ ((rating_b - rating_a) / scale))
    """
    return 1.0 / (1.0 + math.pow(10.0, (rating_b - rating_a) / scale))


# ──────────────────────────────────────────────
# 编排函数：整合预测
# ──────────────────────────────────────────────


def predict_win_rate(
    player_a: PlayerRecord,
    player_b: PlayerRecord,
    elo_scale: float = 400.0,
    clamp_min: float = 0.05,
    clamp_max: float = 0.95,
) -> PredictionResult:
    """预测选手 A 对选手 B 的胜率。

    公式:
        最终胜率 = clamp(1 / (1 + 10 ^ ((rating_b - rating_a) / scale)), clamp_min, clamp_max)

    参数:
        player_a: 选手 A 的快照。
        player_b: 选手 B 的快照。
        elo_scale: Elo 敏感度，默认 400。
        clamp_min: 预测胜率下限，默认 0.05。
        clamp_max: 预测胜率上限，默认 0.95。

    返回:
        PredictionResult 包含 Elo 基础胜率。
    """

    # ── ① Elo 基础胜率 ──
    base = expected_score(player_a.rating, player_b.rating, elo_scale)

    # ── ② 合成最终胜率 ──
    prob_a = _clamp(base, clamp_min, clamp_max)

    return PredictionResult(
        player_a=player_a,
        player_b=player_b,
        probability_a=prob_a,
        probability_b=1.0 - prob_a,
        elo_base_probability=base,
        elo_scale=elo_scale,
    )


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
