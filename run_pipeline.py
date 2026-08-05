#!/usr/bin/env python
"""EloConfig 自动化训练流水线

一条命令完成: 提取数据 → Optuna 搜索 → 更新 EloConfig

用法:
    python run_pipeline.py                    # 默认 500 trials
    python run_pipeline.py --trials 2000      # 指定轮数
    python run_pipeline.py --dry-run          # 只跑流水线，不写入 elo_compute.py
    python run_pipeline.py --verbose          # 打印详细日志
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).parent


def step_extract(limit: int, output: Path, verbose: bool) -> int:
    """Step 1: 从数据库提取训练数据"""
    from extract_training_data import extract_training_matches, print_stats
    import json as _json

    print("\n" + "=" * 60)
    print("STEP 1/3  提取训练数据")
    print("=" * 60)

    t0 = time.time()
    matches = asyncio.run(extract_training_matches(limit=limit))
    elapsed = time.time() - t0

    print_stats(matches)

    # 保存 JSON
    data = []
    for m in matches:
        data.append({
            "event_id": m.event_id,
            "battle_id": m.battle_id,
            "event_index": m.event_index,
            "battle_time": m.battle_time,
            "project_type": m.project_type,
            "team_a": [{"card_code": p.card_code, "name": p.name} for p in m.team_a],
            "team_b": [{"card_code": p.card_code, "name": p.name} for p in m.team_b],
            "score_a": m.score_a,
            "score_b": m.score_b,
            "winner_side": m.winner_side,
        })
    with open(output, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"  保存到 {output} ({elapsed:.1f}s)")
    return len(matches)


def step_train(data_path: Path, trials: int, output: Path, verbose: bool) -> dict:
    """Step 2: Optuna 训练"""
    import optuna
    from optuna.trial import Trial
    from train_optimize_config import (
        load_training_data, split_data, create_objective,
        replay_matches, EloConfig, print_baseline,
    )

    if not verbose:
        optuna.logging.set_verbosity(optuna.logging.WARNING)

    print("\n" + "=" * 60)
    print(f"STEP 2/3  Optuna 训练 ({trials} trials)")
    print("=" * 60)

    # 加载
    matches = load_training_data(str(data_path))
    train_matches, val_matches = split_data(matches)
    print(f"  训练集: {len(train_matches)}  验证集: {len(val_matches)}")

    # 基准线
    print_baseline(train_matches, val_matches, EloConfig())

    # 搜索
    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=42),
        study_name="elo_pipeline",
    )
    objective = create_objective(train_matches, val_matches)

    t0 = time.time()
    study.optimize(objective, n_trials=trials, show_progress_bar=True)
    elapsed = time.time() - t0

    best = study.best_trial
    print(f"\n  最优 trial #{best.number} ({elapsed:.1f}s)")
    print(f"  val_log_loss: {best.value:.4f}")
    print(f"  train_log_loss: {best.user_attrs.get('train_log_loss', 'N/A'):.4f}")
    print(f"  val_accuracy: {best.user_attrs.get('val_accuracy', 0):.2%}")

    # 保存参数
    with open(output, "w", encoding="utf-8") as f:
        json.dump(best.params, f, indent=2)
    print(f"  参数保存到 {output}")

    return best.params


def step_update_config(params: dict, dry_run: bool, verbose: bool) -> None:
    """Step 3: 写入 EloConfig"""
    config_path = ROOT / "elo_compute.py"

    print("\n" + "=" * 60)
    print("STEP 3/3  更新 EloConfig")
    print("=" * 60)

    if dry_run:
        print("  [DRY RUN] 跳过写入")
        _print_config_diff(params)
        return

    content = config_path.read_text(encoding="utf-8")

    # 逐行替换默认值
    for key, value in params.items():
        content = _replace_config_value(content, key, value)

    config_path.write_text(content, encoding="utf-8")
    print(f"  已更新 {config_path.name}")

    # 验证
    _print_config_diff(params)


def _replace_config_value(content: str, key: str, value) -> str:
    """替换 EloConfig 中某个字段的默认值"""
    import re
    # 匹配 "    key: type = old_value"
    if isinstance(value, float):
        # 清理浮点数精度
        clean_val = round(value, 4)
        pattern = rf'^(    {key}: \w+ = )\S+'
        replacement = rf'\g<1>{clean_val}'
    elif isinstance(value, int):
        pattern = rf'^(    {key}: \w+ = )\S+'
        replacement = rf'\g<1>{value}'
    else:
        return content

    new_content = re.sub(pattern, replacement, content, count=1, flags=re.MULTILINE)
    if new_content == content:
        print(f"  [WARN] 未找到 {key}，跳过")
    return new_content


def _print_config_diff(params: dict) -> None:
    """打印新旧参数对比"""
    from elo_compute import EloConfig
    old = EloConfig.__dataclass_fields__

    print(f"\n  {'参数':<30} {'旧值':>10} {'新值':>10} {'变化':>10}")
    print("  " + "-" * 60)
    for key, new_val in params.items():
        old_val = old[key].default
        changed = "✓" if old_val != new_val else ""
        print(f"  {key:<30} {old_val:>10} {new_val:>10} {changed:>10}")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="EloConfig 自动化训练流水线")
    parser.add_argument("--trials", "-t", type=int, default=500, help="搜索轮数 (默认 500)")
    parser.add_argument("--limit", "-l", type=int, default=100000, help="最大提取场次 (默认 100000)")
    parser.add_argument("--dry-run", action="store_true", help="只训练，不写入 elo_compute.py")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    parser.add_argument("--skip-extract", action="store_true", help="跳过提取，复用已有 training_data.json")
    args = parser.parse_args()

    data_path = ROOT / "training_data.json"
    config_output = ROOT / "best_config.json"

    t_total = time.time()

    print("╔══════════════════════════════════════════════════════════╗")
    print("║        EloConfig 自动化训练流水线                        ║")
    print("╚══════════════════════════════════════════════════════════╝")

    # Step 1
    if args.skip_extract and data_path.exists():
        print("\n  [SKIP] 复用已有训练数据")
    else:
        step_extract(args.limit, data_path, args.verbose)

    # Step 2
    params = step_train(data_path, args.trials, config_output, args.verbose)

    # Step 3
    step_update_config(params, args.dry_run, args.verbose)

    elapsed_total = time.time() - t_total
    print(f"\n{'=' * 60}")
    print(f"  全部完成！总耗时 {elapsed_total:.1f}s")
    if args.dry_run:
        print("  [DRY RUN] 未修改 elo_compute.py，使用 --no-dry-run 写入")
    print("=" * 60)


if __name__ == "__main__":
    main()
