"""
Elo 场景化加减分计算函数

按文档 docs/07_Elo场景化设计.md 的设计实现。
输入为 11 个情景最小原子的数值，输出为选手的 Elo 加减分结果。

用法示例:
    >>> from elo_compute import compute_elo, EloConfig, SideInput, MatchInput
    >>> config = EloConfig()
    >>> side_a = SideInput(rating=1500.0, games=1, team_size=1, actual_score=1.0)
    >>> side_b = SideInput(rating=1700.0, games=50, team_size=1, actual_score=0.0)
    >>> match = MatchInput(score_a=21, score_b=15, event_weight=1.0)
    >>> result = compute_elo(side_a, side_b, match, config)
    >>> result.delta
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# ──────────────────────────────────────────────
# 数据结构：输入
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class EloConfig:
    """可调参数集，覆盖所有情景因子的数值控制。"""

    # ── 初始值 ──
    initial_rating: float = 1500.0

    # ── K 值（赛龄阶段） ──
    new_player_games: int = 2
    new_player_k: float = 40.0
    provisional_games: int = 30
    provisional_k: float = 28.0
    stable_k: float = 20.0

    # ── 预期胜率 ──
    elo_scale: float = 400.0

    # ── 分差倍率 M_margin ──
    margin_weight: float = 0.5
    min_margin_cap: int = 21

    # ── 赛事权重 M_weight ──
    match_weight: float = 1.0

    # ── 封顶 cap ──
    delta_cap: float = 40.0

    # ── 越级加分 bonus ──
    upset_min_rating_gap: float = 150.0
    upset_bonus_per_100: float = 6.0
    upset_bonus_cap: float = 24.0

    # ── 被越级扣分 penalty ──
    upset_loser_penalty_ratio: float = 0.25


@dataclass(frozen=True)
class SideInput:
    """一方的输入原子。"""

    rating: float
    """当前 Elo 分。"""

    games: int
    """已赛总场次。"""

    team_size: int
    """队内人数：1 = 单打，2 = 双打。"""

    actual_score: float
    """实际胜负：1.0 = 胜，0.0 = 负，0.5 = 平。"""

    wins: int = 0
    """已获胜场次（仅用于输出统计）。"""

    losses: int = 0
    """已负场次（仅用于输出统计）。"""


@dataclass(frozen=True)
class TeamInput:
    """一方的队伍输入。

    单打: 传入 1 名队员，双打: 传入 2 名队员。
    预期胜率使用队伍平均 Elo 计算，delta 各自独立更新。
    """

    players: tuple[SideInput, ...]
    """队员列表（1 人 = 单打，2 人 = 双打）。"""

    @property
    def rating(self) -> float:
        """队伍平均 Elo。"""
        if not self.players:
            return 0.0
        return sum(p.rating for p in self.players) / len(self.players)

    @property
    def team_size(self) -> int:
        """队内人数。"""
        return len(self.players)

    @property
    def actual_score(self) -> float:
        """队伍实际胜负（所有队员共享同一胜负结果）。"""
        return self.players[0].actual_score if self.players else 0.0


@dataclass(frozen=True)
class MatchInput:
    """比赛的输入原子。"""

    score_a: int
    """本方得分。"""

    score_b: int
    """对方得分。"""

    event_weight: float = 1.0
    """赛事权重（CSV 中的 AQ 列值）。"""


# ──────────────────────────────────────────────
# 数据结构：输出
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class FactorBreakdown:
    """因子分解明细，用于调试和分析。"""

    expected: float
    """预期胜率 E。"""

    margin_multiplier: float
    """分差倍率 M_margin。"""

    weight_multiplier: float
    """赛事权重 M_weight。"""

    k_factor: float
    """K 值。"""

    base_delta: float
    """clamp 前的普通变化。"""

    clamped_delta: float
    """clamp 后的普通变化。"""

    upset_bonus: float
    """越级加分 bonus。"""

    upset_penalty: float
    """被越级扣分 penalty。"""


@dataclass(frozen=True)
class EloResult:
    """一场 Elo 计算的结果。"""

    delta: float
    """最终 Elo 变化量（正 = 加分，负 = 减分）。"""

    rating_after: float
    """更新后的 Elo 分。"""

    games_after: int
    """更新后的场次。"""

    wins_after: int
    """更新后的胜场。"""

    losses_after: int
    """更新后的负场。"""

    breakdown: FactorBreakdown
    """因子分解明细。"""


# ──────────────────────────────────────────────
# 纯函数：每一类因子角色
# ──────────────────────────────────────────────


def expected_score(rating_a: float, rating_b: float, scale: float) -> float:
    """① 预期胜率 E。

    公式: E = 1 / (1 + 10 ^ ((rating_b - rating_a) / scale))
    """
    return 1.0 / (1.0 + math.pow(10.0, (rating_b - rating_a) / scale))


def compute_expected(self_rating: float, opponent_rating: float, config: EloConfig) -> float:
    """队伍预期胜率（已处理单打/双打的平均逻辑）。"""
    return expected_score(self_rating, opponent_rating, config.elo_scale)


def compute_margin(score_for: int, score_against: int, config: EloConfig) -> float:
    """③ 分差倍率 M_margin。

    公式: diff = abs(score_for - score_against)
          cap = max(score_for, score_against, min_margin_cap, 1)
          M = 1 + min(diff, cap) / cap × margin_weight
    """
    diff = abs(score_for - score_against)
    cap = max(score_for, score_against, config.min_margin_cap, 1)
    return 1.0 + min(diff, cap) / cap * config.margin_weight


def compute_weight(match_weight: float, event_weight: float) -> float:
    """④ 赛事权重 M_weight。

    公式: M_weight = match_weight × event_weight
    """
    return match_weight * event_weight


def compute_k_factor(games: int, config: EloConfig) -> float:
    """⑤ K 值（按赛龄阶段选择）。

    定级期 (< new_player_games): K = new_player_k
    观察期 (< provisional_games): K = provisional_k
    稳定期 (>= provisional_games): K = stable_k
    """
    if games < config.new_player_games:
        return config.new_player_k
    if games < config.provisional_games:
        return config.provisional_k
    return config.stable_k


def compute_base_delta(
    k: float,
    weight: float,
    margin: float,
    actual: float,
    expected: float,
) -> float:
    """⑥ 普通变化（clamp 前）。

    公式: base = K × M_weight × M_margin × (S - E)
    """
    return k * weight * margin * (actual - expected)


def clamp_delta(value: float, cap: float) -> float:
    """⑥ clamp 封顶。

    公式: clamp(value, -cap, +cap)
    """
    return max(-cap, min(cap, value))


def compute_upset_bonus(
    games: int,
    actual: float,
    team_rating: float,
    opponent_rating: float,
    weight: float,
    config: EloConfig,
) -> float:
    """⑦ 越级加分 bonus。

    触发条件:
      - 选手处于定级期 (games < new_player_games)
      - 本方获胜 (actual > 0.5)
      - 对手 Elo - 本方 Elo >= upset_min_rating_gap

    公式: bonus = min(upset_bonus_cap, gap / 100 × upset_bonus_per_100) × weight
    """
    if actual <= 0.5 or games >= config.new_player_games:
        return 0.0
    gap = opponent_rating - team_rating
    if gap < config.upset_min_rating_gap:
        return 0.0
    return min(config.upset_bonus_cap, gap / 100.0 * config.upset_bonus_per_100) * weight


def compute_upset_penalty(
    actual: float,
    opponent_bonus: float,
    team_size: int,
    config: EloConfig,
) -> float:
    """⑧ 被越级扣分 penalty。

    触发条件:
      - 本方输了 (actual < 0.5)
      - 对手获得了越级加分 (opponent_bonus > 0)

    公式: penalty = opponent_bonus × upset_loser_penalty_ratio / team_size
    """
    if actual >= 0.5 or opponent_bonus <= 0.0 or config.upset_loser_penalty_ratio <= 0.0 or team_size <= 0:
        return 0.0
    return opponent_bonus * config.upset_loser_penalty_ratio / team_size


# ──────────────────────────────────────────────
# 编排函数：组装所有因子
# ──────────────────────────────────────────────


def compute_elo(
    side_a: SideInput,
    side_b: SideInput,
    match: MatchInput,
    config: EloConfig | None = None,
) -> EloResult:
    """计算一场比赛对 side_a 选手的 Elo 影响。

    注意：本函数只计算单方的结果。因缺少对手的 bonus 信息，
    penalty（被越级扣分）只能按对手 bonus = 0 处理。
    如需同时精确计算双方的 delta（含互相 penalty），
    请使用 compute_match_pair()。

    参数:
        side_a: 本方（想要计算其 Elo 变化的一方）的输入。
        side_b: 对方的输入。
        match: 比赛输入。
        config: 可调参数集，为 None 时使用默认值。

    返回:
        EloResult 包含最终 delta 和因子分解明细。

    公式流程:
        ① E = expected_score(self_rating, opponent_rating, scale)
        ② S = actual_score
        ③ M_margin = compute_margin(score_for, score_against, config)
        ④ M_weight = compute_weight(match_weight, event_weight)
        ⑤ K = compute_k_factor(games, config)
        ⑥ base = K × M_weight × M_margin × (S - E)
           base_clamped = clamp(base, -delta_cap, +delta_cap)
        ⑦ bonus = compute_upset_bonus(...)
        ⑧ penalty = compute_upset_penalty(...)
        ⑨ delta = base_clamped + bonus - penalty
        ⑩ 更新选手状态
    """

    if config is None:
        config = EloConfig()

    # ── ① 预期胜率 E ──
    e = compute_expected(side_a.rating, side_b.rating, config)

    # ── ② 实际胜负 S（由调用方传入 actual_score） ──
    s = side_a.actual_score

    # ── ③ 分差倍率 M_margin ──
    m_margin = compute_margin(match.score_a, match.score_b, config)

    # ── ④ 赛事权重 M_weight ──
    m_weight = compute_weight(config.match_weight, match.event_weight)

    # ── ⑤ K 值 ──
    k = compute_k_factor(side_a.games, config)

    # ── ⑥ 普通变化 + clamp ──
    base = compute_base_delta(k, m_weight, m_margin, s, e)
    base_clamped = clamp_delta(base, config.delta_cap)

    # ── ⑦ 越级加分 bonus ──
    bonus = compute_upset_bonus(
        games=side_a.games,
        actual=s,
        team_rating=side_a.rating,
        opponent_rating=side_b.rating,
        weight=m_weight,
        config=config,
    )

    # ── ⑧ 被越级扣分 penalty ──
    # 单方计算时无法知道对手 bonus，默认当对手 bonus = 0（即无越级发生）。
    # 如需精确 penalty，请使用 compute_match_pair() 或 compute_team_match()。
    penalty = compute_upset_penalty(
        actual=s,
        opponent_bonus=0.0,
        team_size=side_a.team_size,
        config=config,
    )

    # ── ⑨ 最终变化 ──
    delta = base_clamped + bonus - penalty

    # ── ⑩ 更新选手状态 ──
    rating_after = side_a.rating + delta
    games_after = side_a.games + 1
    wins_after = side_a.wins + (1 if s > 0.5 else 0)
    losses_after = side_a.losses + (1 if s < 0.5 else 0)

    breakdown = FactorBreakdown(
        expected=e,
        margin_multiplier=m_margin,
        weight_multiplier=m_weight,
        k_factor=k,
        base_delta=base,
        clamped_delta=base_clamped,
        upset_bonus=bonus,
        upset_penalty=penalty,
    )

    return EloResult(
        delta=delta,
        rating_after=rating_after,
        games_after=games_after,
        wins_after=wins_after,
        losses_after=losses_after,
        breakdown=breakdown,
    )


def compute_match_pair(
    side_a: SideInput,
    side_b: SideInput,
    match: MatchInput,
    config: EloConfig | None = None,
) -> tuple[EloResult, EloResult]:
    """同时计算双方在一场比赛中的 Elo 变化。

    与 compute_elo() 的区别：compute_elo 只算单方且 penalty 不准；
    本函数同时算双方，能拿到互相的 bonus 信息，penalty 精确。
    对于单打场景，推荐直接使用本函数。

    参数:
        side_a: A 方的输入。
        side_b: B 方的输入。
        match: 比赛输入。
        config: 可调参数集。

    返回:
        (result_a, result_b)
    """
    if config is None:
        config = EloConfig()

    # ── 双方共用的中间值 ──
    e_a = compute_expected(side_a.rating, side_b.rating, config)
    e_b = 1.0 - e_a
    s_a = side_a.actual_score
    s_b = side_b.actual_score
    m_margin = compute_margin(match.score_a, match.score_b, config)
    m_weight = compute_weight(config.match_weight, match.event_weight)
    k_a = compute_k_factor(side_a.games, config)
    k_b = compute_k_factor(side_b.games, config)

    # ── 双方普通变化 ──
    base_a = compute_base_delta(k_a, m_weight, m_margin, s_a, e_a)
    base_b = compute_base_delta(k_b, m_weight, m_margin, s_b, e_b)
    clamped_a = clamp_delta(base_a, config.delta_cap)
    clamped_b = clamp_delta(base_b, config.delta_cap)

    # ── 双方越级加分（此时互相独立） ──
    bonus_a = compute_upset_bonus(
        games=side_a.games, actual=s_a,
        team_rating=side_a.rating, opponent_rating=side_b.rating,
        weight=m_weight, config=config,
    )
    bonus_b = compute_upset_bonus(
        games=side_b.games, actual=s_b,
        team_rating=side_b.rating, opponent_rating=side_a.rating,
        weight=m_weight, config=config,
    )

    # ── 双方被越级扣分（依赖对方的 bonus） ──
    penalty_a = compute_upset_penalty(
        actual=s_a, opponent_bonus=bonus_b,
        team_size=side_a.team_size, config=config,
    )
    penalty_b = compute_upset_penalty(
        actual=s_b, opponent_bonus=bonus_a,
        team_size=side_b.team_size, config=config,
    )

    # ── 最终变化 ──
    delta_a = clamped_a + bonus_a - penalty_a
    delta_b = clamped_b + bonus_b - penalty_b

    # ── 构建结果 ──
    r_a = EloResult(
        delta=delta_a,
        rating_after=side_a.rating + delta_a,
        games_after=side_a.games + 1,
        wins_after=side_a.wins + (1 if s_a > 0.5 else 0),
        losses_after=side_a.losses + (1 if s_a < 0.5 else 0),
        breakdown=FactorBreakdown(
            expected=e_a,
            margin_multiplier=m_margin,
            weight_multiplier=m_weight,
            k_factor=k_a,
            base_delta=base_a,
            clamped_delta=clamped_a,
            upset_bonus=bonus_a,
            upset_penalty=penalty_a,
        ),
    )
    r_b = EloResult(
        delta=delta_b,
        rating_after=side_b.rating + delta_b,
        games_after=side_b.games + 1,
        wins_after=side_b.wins + (1 if s_b > 0.5 else 0),
        losses_after=side_b.losses + (1 if s_b < 0.5 else 0),
        breakdown=FactorBreakdown(
            expected=e_b,
            margin_multiplier=m_margin,
            weight_multiplier=m_weight,
            k_factor=k_b,
            base_delta=base_b,
            clamped_delta=clamped_b,
            upset_bonus=bonus_b,
            upset_penalty=penalty_b,
        ),
    )

    return r_a, r_b


# ──────────────────────────────────────────────
# 队伍级别编排：双打支持
# ──────────────────────────────────────────────


def compute_team_match(
    team_a: TeamInput,
    team_b: TeamInput,
    match: MatchInput,
    config: EloConfig | None = None,
) -> tuple[list[EloResult], list[EloResult]]:
    """计算一场比赛（单打或双打）对双方所有队员的 Elo 影响。

    与原项目 elo_core_reference.py 中 rate_matches 的双打逻辑一致：
    - 队伍评分 = 队员 Elo 的平均值
    - 预期胜率基于队伍平均分计算
    - delta 对每人独立计算（每人的 K 值、bonus 各自判定）
    - penalty = 对手 bonus 总和 × ratio / 本方人数
    - 每人独立更新自己的 Elo / 场次 / 胜负

    参数:
        team_a: 甲方队伍（1 人 = 单打，2 人 = 双打）。
        team_b: 乙方队伍。
        match: 比赛输入。
        config: 可调参数集。

    返回:
        (results_a, results_b) — 每个队员对应的 EloResult 列表。
        对于单打，和 compute_match_pair() 等价，但对双方队员各自独立更新。
    """

    if config is None:
        config = EloConfig()

    team_a_rating = team_a.rating
    team_b_rating = team_b.rating

    # ── 双方共用的中间值 ──
    e_a = compute_expected(team_a_rating, team_b_rating, config)
    e_b = 1.0 - e_a
    s_a = team_a.actual_score
    s_b = team_b.actual_score
    m_margin = compute_margin(match.score_a, match.score_b, config)
    m_weight = compute_weight(config.match_weight, match.event_weight)

    # ── 对每个队员独立计算（含 K 值、bonus 判定） ──
    calc_a: list[dict] = []
    for player in team_a.players:
        k = compute_k_factor(player.games, config)
        base = compute_base_delta(k, m_weight, m_margin, s_a, e_a)
        clamped = clamp_delta(base, config.delta_cap)
        bonus = compute_upset_bonus(
            games=player.games, actual=s_a,
            team_rating=team_a_rating, opponent_rating=team_b_rating,
            weight=m_weight, config=config,
        )
        calc_a.append({"player": player, "k": k, "delta": clamped, "bonus": bonus})

    calc_b: list[dict] = []
    for player in team_b.players:
        k = compute_k_factor(player.games, config)
        base = compute_base_delta(k, m_weight, m_margin, s_b, e_b)
        clamped = clamp_delta(base, config.delta_cap)
        bonus = compute_upset_bonus(
            games=player.games, actual=s_b,
            team_rating=team_b_rating, opponent_rating=team_a_rating,
            weight=m_weight, config=config,
        )
        calc_b.append({"player": player, "k": k, "delta": clamped, "bonus": bonus})

    # ── penalty: 对手 bonus 总和 / 本方人数 ──
    team_b_bonus_sum = sum(item["bonus"] for item in calc_b)
    team_a_bonus_sum = sum(item["bonus"] for item in calc_a)

    team_a_penalty_total = compute_upset_penalty(
        actual=s_a, opponent_bonus=team_b_bonus_sum,
        team_size=team_a.team_size, config=config,
    )
    team_b_penalty_total = compute_upset_penalty(
        actual=s_b, opponent_bonus=team_a_bonus_sum,
        team_size=team_b.team_size, config=config,
    )

    # ── 分配 penalty 到每个队员（与原项目一致的平分逻辑） ──
    penalty_a_share = team_a_penalty_total / team_a.team_size if team_a.team_size else 0.0
    penalty_b_share = team_b_penalty_total / team_b.team_size if team_b.team_size else 0.0

    results_a = []
    for item in calc_a:
        item["delta"] -= penalty_a_share
        item["_penalty_share"] = penalty_a_share
        item["_m_margin"] = m_margin
        item["_m_weight"] = m_weight
        results_a.append(_make_team_result(item, e_a))

    results_b = []
    for item in calc_b:
        item["delta"] -= penalty_b_share
        item["_penalty_share"] = penalty_b_share
        item["_m_margin"] = m_margin
        item["_m_weight"] = m_weight
        results_b.append(_make_team_result(item, e_b))

    return results_a, results_b


def _make_team_result(item: dict, e: float) -> EloResult:
    """构建单个队员的 EloResult。"""
    p: SideInput = item["player"]
    delta_final = item["delta"]
    penalty_share = item["_penalty_share"]
    clamped = delta_final - item["bonus"] + penalty_share

    return EloResult(
        delta=delta_final,
        rating_after=p.rating + delta_final,
        games_after=p.games + 1,
        wins_after=p.wins + (1 if p.actual_score > 0.5 else 0),
        losses_after=p.losses + (1 if p.actual_score < 0.5 else 0),
        breakdown=FactorBreakdown(
            expected=e,
            margin_multiplier=item["_m_margin"],
            weight_multiplier=item["_m_weight"],
            k_factor=item["k"],
            base_delta=clamped,
            clamped_delta=clamped,
            upset_bonus=item["bonus"],
            upset_penalty=penalty_share,
        ),
    )
