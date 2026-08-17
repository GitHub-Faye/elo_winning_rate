# ELO 积分系统

## 数据源

### 原始数据表

| 表名 | 作用 | 关键字段 |
|------|------|----------|
| `motion_event_layout_stage_battle` | 对阵表（比赛结果） | `battle_id`, `event_id`, `player_one_score`, `player_two_score`, `player_one_user_ids`, `player_two_user_ids`, `project_type`, `battle_time`, `status` |
| `motion_event_apply_user_setting` | 报名人表（身份对齐） | `user_setting_id`, `event_id`, `card_code`, `name` |

### 数据对齐流程

```
对阵表 player_one_user_ids (user_setting_id)
        ↓ JOIN (event_id + user_setting_id)
报名人表 card_code (身份证号)
        ↓
    ELO 计算
```

**关键过滤条件：**
- `status = 2`（比赛已完成）
- `is_empty = 0`（非空场）
- `player_one_score != player_two_score`（有胜负）
- `card_code REGEXP '^[0-9]{17}[0-9Xx]$'`（有效身份证号）

### ELO 计算使用的数据库字段

**从 `motion_event_layout_stage_battle` 提取：**

| 字段 | 用途 |
|------|------|
| `event_id` | 赛事 ID |
| `battle_id` | 对阵 ID |
| `event_index` | 赛事内场序号（排序用） |
| `battle_time` | 比赛时间（决定重放顺序） |
| `player_one_score` / `player_two_score` | 比分（用于 margin 计算） |
| `project_type` | 1=单打, 2=双打 |
| `player_one_user_ids` / `player_two_user_ids` | user_setting_id（定位选手） |

**从 `motion_event_apply_user_setting` 提取：**

| 字段 | 用途 |
|------|------|
| `user_setting_id` | 主键（与对阵表对齐） |
| `card_code` | 身份证号（ELO 系统的选手唯一标识） |

### 输出表（ELO 结果）

**`elo_match_record`（每场比赛每人一条）：**

| 字段 | 来源/计算 |
|------|-----------|
| `rating_before` | 从 `elo_player_rating` 读取当前分 |
| `delta` | ELO 公式计算结果 |
| `rating_after` | `rating_before + delta` |
| `expected` | `compute_expected(A.rating, B.rating)` |
| `k_factor` | `compute_k_factor(games)` — 新手80/临时20/稳定15 |
| `margin_multiplier` | `compute_margin(score_a, score_b)` |
| `weight_multiplier` | 固定 1.0 |
| `base_delta` | `K × weight × margin × (S - E)` |
| `upset_bonus` | 越级加分（分差≥200 时触发） |
| `upset_penalty` | 被越级扣分 |

**`elo_player_rating`（每个选手当前状态）：**

| 字段 | 说明 |
|------|------|
| `card_code` + `sport_type` | 联合主键 |
| `rating` | 当前 ELO 分 |
| `games/wins/losses/draws` | 战绩统计 |
| `highest_rating/lowest_rating` | 历史极值 |
| `province` | 归属省份（来自 motion_user.address_province） |
| `city` | 归属城市（来自 motion_user.address_city） |

### ELO 公式参数（best_config.json）

```
初始分: 1500.0
K值: 新手(≤2场)=80, 临时(≤15场)=20, 稳定=15
ELO scale: 200
delta_cap: ±65
越级触发: 分差≥200
越级加分: 每100分差+10, 上限40
越级扣分: bonus × 15%
```
