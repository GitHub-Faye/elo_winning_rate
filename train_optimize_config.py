"""EloConfig 参数优化训练脚本

用 Optuna 贝叶斯优化搜索最优 EloConfig 参数，使预测胜率与真实结果对齐。

训练流程:
  1. 加载 training_data.json（由 extract_training_data.py 生成）
  2. 按 event_id + event_index 顺序重放比赛
  3. 每场比赛用当前参数计算预测胜率
  4. 用 log-loss 作为损失函数
  5. Optuna 搜索使 log-loss 最小的参数组合

用法:
    python train_optimize_config.py --trials 500
    python train_optimize_config.py --trials 1000 --output best_config.json
    python train_optimize_config.py --trials 200 --train-ratio 0.8
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from typing import Optional

import optuna
from optuna.trial import Trial

from elo_compute import (
    EloConfig,
    SideInput,
    TeamInput,
    MatchInput,
    compute_match_pair,
    compute_team_match,
)


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────


@dataclass
class PlayerInfo:
    card_code: str
    name: str


@dataclass
class TrainingMatch:
    event_id: int
    battle_id: int
    event_index: int
    battle_time: Optional[str]
    project_type: int  # 1=单打 2=双打
    team_a: list[PlayerInfo]
    team_b: list[PlayerInfo]
    score_a: int
    score_b: int
    winner_side: str  # "A" 或 "B"


@dataclass
class PlayerState:
    """选手的重放状态"""
    rating: float
    games: int
    wins: int
    losses: int


# ──────────────────────────────────────────────
# 损失函数
# ──────────────────────────────────────────────

_EPS = 1e-15


def log_loss(predicted: float, actual: float) -> float:
    """二元交叉熵损失"""
    p = max(_EPS, min(1.0 - _EPS, predicted))
    return -(actual * math.log(p) + (1.0 - actual) * math.log(1.0 - p))


def brier_score(predicted: float, actual: float) -> float:
    """Brier score（备选损失）"""
    return (predicted - actual) ** 2


# ──────────────────────────────────────────────
# 重放引擎
# ──────────────────────────────────────────────


def _team_rating(team: list[PlayerInfo], states: dict[str, PlayerState]) -> float:
    """取队伍最高 Elo 代表全队（与 prediction_service 一致）"""
    if not team:
        return 1500.0
    ratings = [states[p.card_code].rating for p in team]
    return max(ratings)


def replay_matches(
    matches: list[TrainingMatch],
    config: EloConfig,
    verbose: bool = False,
) -> dict:
    """按顺序重放所有比赛，返回统计信息。

    Returns:
        {
            "total_loss": float,
            "total_matches": int,
            "avg_log_loss": float,
            "avg_brier": float,
            "accuracy": float,  # 预测方向正确的比例
            "predictions": list[float],  # 每场预测值（用于进一步分析）
        }
    """
    states: dict[str, PlayerState] = {}
    total_loss = 0.0
    total_brier = 0.0
    correct = 0
    predictions = []

    for match in matches:
        # 获取/初始化双方选手状态
        for p in match.team_a + match.team_b:
            if p.card_code not in states:
                states[p.card_code] = PlayerState(
                    rating=config.initial_rating,
                    games=0, wins=0, losses=0,
                )

        # 队伍代表积分
        ra = _team_rating(match.team_a, states)
        rb = _team_rating(match.team_b, states)

        # 预测胜率
        expected_a = expected_score(ra, rb, config.elo_scale)
        predicted = max(0.05, min(0.95, expected_a))

        # 实际结果
        actual = 1.0 if match.winner_side == "A" else 0.0

        # 累计损失
        total_loss += log_loss(predicted, actual)
        total_brier += brier_score(predicted, actual)
        if (predicted >= 0.5 and actual == 1.0) or (predicted < 0.5 and actual == 0.0):
            correct += 1
        predictions.append(predicted)

        # ── 更新积分 ──
        _update_ratings(match, states, config)

    n = len(matches)
    return {
        "total_loss": total_loss,
        "total_matches": n,
        "avg_log_loss": total_loss / n if n else float("inf"),
        "avg_brier": total_brier / n if n else float("inf"),
        "accuracy": correct / n if n else 0.0,
        "predictions": predictions,
    }


def expected_score(rating_a: float, rating_b: float, scale: float) -> float:
    """标准 Elo 预期胜率"""
    return 1.0 / (1.0 + math.pow(10.0, (rating_b - rating_a) / scale))


def _update_ratings(
    match: TrainingMatch,
    states: dict[str, PlayerState],
    config: EloConfig,
) -> None:
    """用当前参数更新双方选手的积分"""
    s_a = 1.0 if match.winner_side == "A" else 0.0
    s_b = 1.0 - s_a

    if match.project_type == 1:
        # ── 单打 ──
        p_a = match.team_a[0]
        p_b = match.team_b[0]
        sa = states[p_a.card_code]
        sb = states[p_b.card_code]

        side_a = SideInput(
            rating=sa.rating, games=sa.games, team_size=1,
            actual_score=s_a, wins=sa.wins, losses=sa.losses,
        )
        side_b = SideInput(
            rating=sb.rating, games=sb.games, team_size=1,
            actual_score=s_b, wins=sb.wins, losses=sb.losses,
        )
        match_input = MatchInput(
            score_a=match.score_a, score_b=match.score_b, event_weight=1.0,
        )
        r_a, r_b = compute_match_pair(side_a, side_b, match_input, config)

        _apply_result(sa, r_a)
        _apply_result(sb, r_b)
    else:
        # ── 双打 ──
        team_a = TeamInput(players=tuple(
            SideInput(
                rating=states[p.card_code].rating,
                games=states[p.card_code].games,
                team_size=2, actual_score=s_a,
                wins=states[p.card_code].wins,
                losses=states[p.card_code].losses,
            )
            for p in match.team_a
        ))
        team_b = TeamInput(players=tuple(
            SideInput(
                rating=states[p.card_code].rating,
                games=states[p.card_code].games,
                team_size=2, actual_score=s_b,
                wins=states[p.card_code].wins,
                losses=states[p.card_code].losses,
            )
            for p in match.team_b
        ))
        match_input = MatchInput(
            score_a=match.score_a, score_b=match.score_b, event_weight=1.0,
        )
        results_a, results_b = compute_team_match(team_a, team_b, match_input, config)

        for p, r in zip(match.team_a, results_a):
            _apply_result(states[p.card_code], r)
        for p, r in zip(match.team_b, results_b):
            _apply_result(states[p.card_code], r)


def _apply_result(state: PlayerState, result) -> None:
    """将 EloResult 应用到选手状态"""
    state.rating = result.rating_after
    state.games = result.games_after
    state.wins = result.wins_after
    state.losses = result.losses_after


# ──────────────────────────────────────────────
# Optuna 目标函数
# ──────────────────────────────────────────────


def create_objective(
    train_matches: list[TrainingMatch],
    val_matches: list[TrainingMatch],
):
    """创建 Optuna 目标函数（闭包捕获训练/验证集）"""

    def objective(trial: Trial) -> float:
        # ── 定义搜索空间 ──
        config = EloConfig(
            initial_rating=1500.0,
            new_player_games=trial.suggest_int("new_player_games", 1, 5),
            new_player_k=trial.suggest_float("new_player_k", 20, 80, step=5),
            provisional_games=trial.suggest_int("provisional_games", 10, 60, step=5),
            provisional_k=trial.suggest_float("provisional_k", 15, 50, step=5),
            stable_k=trial.suggest_float("stable_k", 10, 40, step=5),
            elo_scale=trial.suggest_float("elo_scale", 200, 600, step=50),
            margin_weight=trial.suggest_float("margin_weight", 0.1, 1.0, step=0.1),
            min_margin_cap=trial.suggest_int("min_margin_cap", 15, 30, step=1),
            delta_cap=trial.suggest_float("delta_cap", 20, 80, step=5),
            upset_min_rating_gap=trial.suggest_float("upset_min_rating_gap", 50, 300, step=25),
            upset_bonus_per_100=trial.suggest_float("upset_bonus_per_100", 2, 15, step=1),
            upset_bonus_cap=trial.suggest_float("upset_bonus_cap", 10, 40, step=5),
            upset_loser_penalty_ratio=trial.suggest_float("upset_loser_penalty_ratio", 0.0, 0.5, step=0.05),
        )

        # ── 训练集评估 ──
        train_result = replay_matches(train_matches, config)
        trial.set_user_attr("train_log_loss", train_result["avg_log_loss"])
        trial.set_user_attr("train_accuracy", train_result["accuracy"])

        # ── 验证集评估 ──
        if val_matches:
            val_result = replay_matches(val_matches, config)
            trial.set_user_attr("val_log_loss", val_result["avg_log_loss"])
            trial.set_user_attr("val_accuracy", val_result["accuracy"])
            # 用验证集 loss 作为优化目标（防止过拟合）
            return val_result["avg_log_loss"]

        # 无验证集时用训练集
        return train_result["avg_log_loss"]

    return objective


# ──────────────────────────────────────────────
# 数据加载
# ──────────────────────────────────────────────


def load_training_data(path: str) -> list[TrainingMatch]:
    """从 JSON 文件加载训练数据"""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    matches = []
    for item in raw:
        team_a = [PlayerInfo(**p) for p in item["team_a"]]
        team_b = [PlayerInfo(**p) for p in item["team_b"]]
        matches.append(TrainingMatch(
            event_id=item["event_id"],
            battle_id=item["battle_id"],
            event_index=item["event_index"],
            battle_time=item.get("battle_time"),
            project_type=item.get("project_type", 1),
            team_a=team_a,
            team_b=team_b,
            score_a=item["score_a"],
            score_b=item["score_b"],
            winner_side=item["winner_side"],
        ))
    return matches


def split_data(
    matches: list[TrainingMatch],
    train_ratio: float = 0.8,
) -> tuple[list[TrainingMatch], list[TrainingMatch]]:
    """按时间顺序切分训练/验证集（不打乱，模拟真实场景）"""
    split_idx = int(len(matches) * train_ratio)
    return matches[:split_idx], matches[split_idx:]


# ──────────────────────────────────────────────
# 输出
# ──────────────────────────────────────────────


def print_baseline(train_matches, val_matches, default_config):
    """打印默认参数的基准指标"""
    print("\n" + "=" * 60)
    print("基准线（默认 EloConfig 参数）")
    print("=" * 60)

    train_result = replay_matches(train_matches, default_config)
    print(f"  训练集: log_loss={train_result['avg_log_loss']:.4f}, "
          f"accuracy={train_result['accuracy']:.2%}")

    if val_matches:
        val_result = replay_matches(val_matches, default_config)
        print(f"  验证集: log_loss={val_result['avg_log_loss']:.4f}, "
              f"accuracy={val_result['accuracy']:.2%}")

    print("=" * 60)


def print_best_config(study: optuna.Study):
    """打印最优参数"""
    best = study.best_trial
    print(f"\n{'=' * 60}")
    print(f"最优 trial #{best.number}")
    print(f"{'=' * 60}")
    print(f"  log_loss: {best.value:.4f}")
    print(f"  train_log_loss: {best.user_attrs.get('train_log_loss', 'N/A')}")
    print(f"  train_accuracy: {best.user_attrs.get('train_accuracy', 'N/A'):.2%}")
    print(f"  val_log_loss: {best.user_attrs.get('val_log_loss', 'N/A')}")
    print(f"  val_accuracy: {best.user_attrs.get('val_accuracy', 'N/A'):.2%}")
    print()
    print("  最优参数:")
    for k, v in best.params.items():
        print(f"    {k}: {v}")
    print("=" * 60)


def save_config(params: dict, path: str):
    """保存最优参数为 JSON"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2, ensure_ascii=False)
    print(f"\n最优参数已保存到 {path}")


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="EloConfig 参数优化训练")
    parser.add_argument(
        "--data", "-d",
        default="training_data.json",
        help="训练数据文件路径 (默认: training_data.json)",
    )
    parser.add_argument(
        "--trials", "-t",
        type=int,
        default=500,
        help="Optuna 搜索轮数 (默认: 500)",
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.8,
        help="训练集比例 (默认: 0.8)",
    )
    parser.add_argument(
        "--output", "-o",
        default="best_config.json",
        help="最优参数输出路径 (默认: best_config.json)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="随机种子 (默认: 42)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="不保存参数文件",
    )
    args = parser.parse_args()

    # ── 加载数据 ──
    print(f"加载训练数据: {args.data}")
    matches = load_training_data(args.data)
    print(f"  总场次: {len(matches)}")

    train_matches, val_matches = split_data(matches, args.train_ratio)
    print(f"  训练集: {len(train_matches)} 场")
    print(f"  验证集: {len(val_matches)} 场")

    # ── 基准线 ──
    default_config = EloConfig()
    print_baseline(train_matches, val_matches, default_config)

    # ── Optuna 搜索 ──
    print(f"\n开始 Optuna 搜索 ({args.trials} trials)...")
    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=args.seed),
        study_name="elo_config_optimization",
    )

    objective = create_objective(train_matches, val_matches)
    study.optimize(
        objective,
        n_trials=args.trials,
        show_progress_bar=True,
    )

    # ── 输出结果 ──
    print_best_config(study)

    # ── 参数重要性 ──
    try:
        importance = optuna.importance.get_param_importances(study)
        print(f"\n参数重要性排名:")
        for k, v in sorted(importance.items(), key=lambda x: -x[1]):
            print(f"  {k}: {v:.4f}")
    except Exception:
        pass

    # ── 保存 ──
    if not args.no_save:
        save_config(study.best_params, args.output)


if __name__ == "__main__":
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    main()
