"""
Elo 胜负预测：基于选手赛后关系图的胜率估算

仅用于赛前参考，不参与 Elo 加减分计算。

预测公式:
    最终胜率 = clamp(Elo 基础胜率 + 直接交手修正 + 间接关系修正, 0.05, 0.95)

所有关系数据来自真实比赛构建的选手胜负关系图 (relation graph)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────

MAX_INDIRECT_DEPTH = 5
"""最大间接关系搜索深度。"""


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
class RelationGraph:
    """选手胜负关系图。

    结构: {player_id: {opponent_id: {"wins": int, "losses": int, "total": int}}}
    """

    data: dict[str, dict[str, dict[str, int]]]

    def record(self, player_id: str, opponent_id: str) -> dict[str, int]:
        """查询两名选手的直接交手记录。"""
        return self.data.get(player_id, {}).get(
            opponent_id, {"wins": 0, "losses": 0, "total": 0}
        )

    def neighbors(self, player_id: str, max_neighbors: int = 24) -> list[tuple[str, dict[str, int]]]:
        """返回选手的所有对手（按交手次数降序）。"""
        items = [
            (oid, rec)
            for oid, rec in self.data.get(player_id, {}).items()
            if rec["total"] > 0
        ]
        items.sort(key=lambda x: (-x[1]["total"], -abs(_edge_signal(x[1]))))
        return items[:max_neighbors]


@dataclass(frozen=True)
class IndirectPath:
    """一条间接关系路径。"""

    path: tuple[str, ...]
    """路径上的选手 ID 序列，如 (A, X, Y, B)。"""
    signals: tuple[float, ...]
    """每条边的信号值。"""
    weight: float
    """路径权重（最弱边的权重）。"""
    depth: int
    """路径深度（边数）。"""
    signal: float
    """路径平均信号。"""
    score: float
    """路径排序分（用于保留最相关路径）。"""


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
    """Elo 基础胜率（未经修正）。"""
    elo_scale: float
    """该次预测使用的 Elo 敏感度。"""

    direct_record: dict[str, int]
    """A 对 B 的直接交手记录。"""
    direct_adjustment: float
    """直接交手修正值。"""

    indirect_paths: list[IndirectPath]
    """搜索到的间接路径列表。"""
    indirect_depth: int
    """最深的间接路径层数。"""
    indirect_signal: float
    """间接路径总信号。"""
    indirect_adjustment: float
    """间接关系修正值。"""


# ──────────────────────────────────────────────
# 纯函数：预期胜率
# ──────────────────────────────────────────────


def expected_score(rating_a: float, rating_b: float, scale: float = 400.0) -> float:
    """标准 Elo 预期胜率。

    E = 1 / (1 + 10 ^ ((rating_b - rating_a) / scale))
    """
    return 1.0 / (1.0 + math.pow(10.0, (rating_b - rating_a) / scale))


# ──────────────────────────────────────────────
# 纯函数：直接交手修正
# ──────────────────────────────────────────────


def record_rate(record: dict[str, int]) -> float:
    """直接交手胜率。

    公式: wins / total；无交手记录时返回 0.5（中立）。
    """
    return record["wins"] / record["total"] if record["total"] else 0.5


def direct_adjustment(record: dict[str, int]) -> float:
    """计算直接交手修正值。

    公式:
        直接胜率 = record_rate(record)
        修正 = (直接胜率 - 0.5) × min(0.3, 总场次 × 0.08)

    交手越多修正量越大，但上限为 0.3（约 12 场达到上限）。
    """
    total = record["total"]
    if total == 0:
        return 0.0
    rate = record_rate(record)
    return (rate - 0.5) * min(0.3, total * 0.08)


# ──────────────────────────────────────────────
# 纯函数：间接关系修正
# ──────────────────────────────────────────────


def _edge_signal(record: dict[str, int]) -> float:
    """单条边的信号值。

    公式: 边信号 = (边胜率 - 0.5) × 2
    取值范围约 -1 到 1。
    正数表示路径方向更支持起点，负数表示更支持终点。
    """
    return (record_rate(record) - 0.5) * 2.0


def _edge_weight(record: dict[str, int]) -> float:
    """单条边的权重。

    公式: edge_weight = min(8, sqrt(total))
    交手越多权重越高，上限 8。
    """
    return max(1.0, min(8.0, math.sqrt(record.get("total", 1) or 1)))


def _indirect_path_score(path_signals: list[float], weight: float, depth: int) -> float:
    """路径的排序分数（用于搜索剪枝）。"""
    return abs(_average(path_signals)) * weight / math.sqrt(depth or 1)


def build_indirect_paths(
    graph: RelationGraph,
    start_id: str,
    target_id: str,
    max_depth: int = MAX_INDIRECT_DEPTH,
    max_beam: int = 220,
    max_found: int = 60,
    max_neighbors: int = 24,
) -> list[IndirectPath]:
    """搜索两名选手之间的间接关系路径（beam-search）。

    例如 A -> X -> B  为 2 层路径（共同对手），最大 5 层。
    通过 beam-search 控制搜索宽度，避免性能问题。

    参数:
        graph: 选手胜负关系图。
        start_id: 起始选手 ID。
        target_id: 目标选手 ID。
        max_depth: 最大搜索深度，默认 5。
        max_beam: 每层保留的候选路径数，默认 220。
        max_found: 最多返回的路径数，默认 60。
        max_neighbors: 每个节点最多扩展的邻居数，默认 24。

    返回:
        按 score 降序排列的 IndirectPath 列表。
    """
    found: list[tuple[list[str], list[float], float, int]] = []
    frontier: list[tuple[str, list[str], list[float], float, int]] = [
        (start_id, [start_id], [], 8.0, 0)
    ]

    for depth in range(1, max_depth + 1):
        next_frontier: list[tuple[str, list[str], list[float], float, int]] = []

        for node_id, path, signals, weight, _ in frontier:
            for next_id, record in graph.neighbors(node_id, max_neighbors):
                if next_id in path:
                    continue
                if node_id == start_id and next_id == target_id:
                    continue

                edge_signal = _edge_signal(record)
                edge_w = _edge_weight(record)

                candidate_path = path + [next_id]
                candidate_signals = signals + [edge_signal]
                candidate_weight = min(weight, edge_w)

                if next_id == target_id and depth >= 2:
                    found.append((candidate_path, candidate_signals, candidate_weight, depth))
                elif depth < max_depth:
                    next_frontier.append(
                        (next_id, candidate_path, candidate_signals, candidate_weight, depth)
                    )

        next_frontier.sort(
            key=lambda x: _indirect_path_score(x[2], x[3], x[4]),
            reverse=True,
        )
        frontier = next_frontier[:max_beam]

        if not frontier:
            break

    paths = [
        _make_indirect_path(p, sigs, w, d)
        for p, sigs, w, d in found
        if abs(_average(sigs)) > 0.01
    ]
    paths.sort(key=lambda x: x.score, reverse=True)
    return paths[:max_found]


def _make_indirect_path(
    path: list[str],
    signals: list[float],
    weight: float,
    depth: int,
) -> IndirectPath:
    """从搜索中间结果构建 IndirectPath。"""
    signal = _average(signals)
    score = abs(signal) * weight / math.sqrt(depth or 1)
    return IndirectPath(
        path=tuple(path),
        signals=tuple(signals),
        weight=weight,
        depth=depth,
        signal=signal,
        score=score,
    )


def indirect_adjustment(
    paths: list[IndirectPath],
    max_adjustment: float = 0.18,
    adjustment_per_path: float = 0.012,
) -> tuple[float, float, int]:
    """计算间接关系修正值。

    流程:
        1. 每条路径计算有效权重 = path.weight / sqrt(path.depth)
        2. 加权平均得到间接信号
        3. 间接修正 = 间接信号 × min(max_adjustment, 路径数 × adjustment_per_path)

    参数:
        paths: 间接路径列表。
        max_adjustment: 间接修正的最大值，默认 0.18。
        adjustment_per_path: 每条路径对修正上限的贡献，默认 0.012。

    返回:
        (indirect_signal, indirect_adjustment, max_depth)
    """
    if not paths:
        return 0.0, 0.0, 0

    path_weights = [p.weight / math.sqrt(p.depth or 1) for p in paths]
    weight_total = sum(path_weights)

    if weight_total == 0:
        return 0.0, 0.0, 0

    signal = sum(p.signal * pw for p, pw in zip(paths, path_weights)) / weight_total
    adj = signal * min(max_adjustment, len(paths) * adjustment_per_path)
    max_depth = max(p.depth for p in paths)

    return signal, adj, max_depth


# ──────────────────────────────────────────────
# 编排函数：整合预测
# ──────────────────────────────────────────────


def predict_win_rate(
    player_a: PlayerRecord,
    player_b: PlayerRecord,
    graph: RelationGraph,
    elo_scale: float = 400.0,
    clamp_min: float = 0.05,
    clamp_max: float = 0.95,
) -> PredictionResult:
    """预测选手 A 对选手 B 的胜率。

    公式:
        最终胜率 = clamp(Elo 基础胜率 + 直接交手修正 + 间接关系修正, clamp_min, clamp_max)

    参数:
        player_a: 选手 A 的快照。
        player_b: 选手 B 的快照。
        graph: 选手胜负关系图。
        elo_scale: Elo 敏感度，默认 400。
        clamp_min: 预测胜率下限，默认 0.05。
        clamp_max: 预测胜率上限，默认 0.95。

    返回:
        PredictionResult 包含各部分修正的明细。
    """

    # ── ① Elo 基础胜率 ──
    base = expected_score(player_a.rating, player_b.rating, elo_scale)

    # ── ② 直接交手修正 ──
    direct_rec = graph.record(player_a.player_id, player_b.player_id)
    direct_adj = direct_adjustment(direct_rec)

    # ── ③ 间接关系修正 ──
    paths = build_indirect_paths(graph, player_a.player_id, player_b.player_id)
    indirect_sig, indirect_adj, indirect_max_depth = indirect_adjustment(paths)

    # ── ④ 合成最终胜率 ──
    prob_a = _clamp(base + direct_adj + indirect_adj, clamp_min, clamp_max)

    return PredictionResult(
        player_a=player_a,
        player_b=player_b,
        probability_a=prob_a,
        probability_b=1.0 - prob_a,
        elo_base_probability=base,
        elo_scale=elo_scale,
        direct_record=direct_rec,
        direct_adjustment=direct_adj,
        indirect_paths=paths,
        indirect_depth=indirect_max_depth,
        indirect_signal=indirect_sig,
        indirect_adjustment=indirect_adj,
    )


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────


def build_relation_graph(
    relations: dict[str, dict[str, dict[str, int]]],
) -> RelationGraph:
    """从原始关系字典构建 RelationGraph。"""
    return RelationGraph(data=relations)


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
