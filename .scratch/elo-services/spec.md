## Problem Statement

作为羽毛球赛事组织者，我需要一个 Elo 评分系统来计算选手的加减分、预测比赛胜率。目前已经有了核心算法（`elo_compute.py` 和 `winning_rate.py`）和数据库设计（`docs/db_analysis.md`），但缺少将它们串联起来的 API 服务层和数据库持久化层。选手的 Elo 分目前没有被记录，历史比赛数据也没有被回放。

## Solution

在 FastAPI 项目中新增两个核心服务：

1. **加减分服务** — 提交比赛结果 → 计算 Elo 变化 → 持久化到数据库
2. **胜率预测服务** — 给定双方选手 → 返回胜率预测

两者共享同一个数据库后端（`elo_player_rating` 表存储选手当前 Elo 分，`elo_match_record` 表记录每场比赛的 Elo 变化日志）。API 统一为单打/双打兼容的接口，根据选手人数自动判断比赛类型。

## User Stories

1. 作为系统用户，我希望能提交一场比赛的结果（比分、双方选手），系统自动计算 Elo 变化并更新选手的 Elo 分，以便赛后立即看到新的排名。

2. 作为系统用户，我希望能查看一场比赛的具体 Elo 变化明细（预期胜率、K 值、分差倍率、越级加分等因子分解），以便理解为什么加减这么多分。

3. 作为系统用户，我希望能对两名选手进行赛前胜率预测，输入双方选手 ID 即可获得预测胜率及修正明细，以便赛前参考。

4. 作为系统用户，我能在双打场景下提交比赛结果（每方 2 人），系统自动计算双打 Elo 变化（取队伍平均分、各自独立计算 delta、平分 penalty），以便支持双打赛制的 Elo 管理。

5. 作为系统用户，我能在双打场景下进行赛前胜率预测，系统自动在两两之间计算预测胜率后取平均值，以便双打赛前也能获得参考。

6. 作为系统用户，我希望能通过 Alembic 迁移自动创建 `elo_player_rating` 和 `elo_match_record` 两张表，以便数据库结构变更可追溯。

7. 作为开发者，我希望加减分服务能通过依赖注入 AsyncSession 来测试，以便在测试中 mock 数据库交互。

8. 作为开发者，我希望胜率预测服务能通过依赖注入 AsyncSession 来测试，以便在测试中 mock 数据库交互。

## Implementation Decisions

### 模块结构

```
core/
├── __init__.py
├── database.py        # 已有：数据库连接
├── models.py          # 新增：SQLModel 模型定义
└── schemas.py         # 新增：Pydantic 请求/响应模型

routers/
├── __init__.py
├── elo.py             # 加减分路由
└── prediction.py      # 胜率预测路由

services/
├── __init__.py
├── elo_service.py     # 加减分业务逻辑
└── prediction_service.py # 胜率预测业务逻辑
```

### 数据库模型 (`core/models.py`)

两张表，使用 SQLModel 定义，继承 `SQLModel` 基类，通过 Alembic 迁移生成。

**`elo_player_rating`** — 选手 Elo 分段位分。字段：`user_id` (BIGINT, PK), `rating`, `games`, `wins`, `losses`, `highest_rating`, `lowest_rating`, `created_at`, `updated_at`。唯一索引 `uk_user_id`。

**`elo_match_record`** — 比赛 Elo 变化日志。字段：`event_id`, `battle_id`, `played_at`, `winner_user_id`, `loser_user_id`, `team_size`(1/2), `winner_partner_id`, `loser_partner_id`, `score_winner`, `score_loser`, `rating_before_winner`, `rating_before_loser`, `delta_winner`, `delta_loser`, `rating_after_winner`, `rating_after_loser`, `expected_winner`, `k_factor_winner`, `k_factor_loser`, `margin_multiplier`, `upset_bonus_winner`, `created_at`。

### API 接口

**`POST /api/v1/elo/record`** — 记录比赛结果

请求体：
```json
{
  "event_id": 1,
  "battle_id": 100,
  "played_at": "2024-01-01T00:00:00",
  "score_a": 21,
  "score_b": 15,
  "team_a": [1001, 1002],
  "team_b": [2001],
  "event_weight": 1.0
}
```

响应体：
```json
{
  "success": true,
  "data": {
    "team_a": [
      {"user_id": 1001, "delta": 5.2, "rating_after": 1505.2, "games_after": 11, "wins_after": 6, "losses_after": 5}
    ],
    "team_b": [
      {"user_id": 2001, "delta": -5.2, "rating_after": 1494.8, "games_after": 31, "wins_after": 20, "losses_after": 11}
    ]
  }
}
```

**`POST /api/v1/prediction`** — 胜率预测

请求体：
```json
{
  "team_a": [1001, 1002],
  "team_b": [2001]
}
```

响应体：
```json
{
  "success": true,
  "data": {
    "team_a": {"user_ids": [1001, 1002], "probability": 0.68},
    "team_b": {"user_ids": [2001], "probability": 0.32},
    "elo_base": 0.62,
    "direct_adjustment": 0.03,
    "indirect_adjustment": 0.03,
    "detail": [
      {"player_a": 1001, "player_b": 2001, "probability_a": 0.70},
      {"player_a": 1002, "player_b": 2001, "probability_a": 0.66}
    ]
  }
}
```

### 单打/双打自动判断逻辑

- `team_a` 和 `team_b` 都只有 1 个元素 → 单打，调用 `compute_match_pair()`
- `team_a` 和 `team_b` 都有 2 个元素 → 双打，调用 `compute_team_match()`
- 其他情况 → 返回 400 错误

### 加减分服务流程

1. 校验输入（人数匹配、比分合法）
2. 查询 DB 中双方所有选手的当前 `elo_player_rating`
3. 如果选手不存在，用默认值（rating=1500, games=0, wins=0, losses=0）创建
4. 根据人数判断单打/双打，调用对应算法函数
5. 更新 `elo_player_rating`（UPSERT 语义）
6. 写入 `elo_match_record`（包含完整的因子分解信息）
7. 返回结果

### 胜率预测服务流程

1. 查询 DB 中双方选手的当前 `elo_player_rating`
2. 查询 `elo_match_record` 构建 `relation_graph`
3. 单打：直接调用 `predict_win_rate()`
4. 双打：两两配对 `predict_win_rate()` 后取平均值
5. 返回预测结果

### Service 层设计

Service 层函数接收 `AsyncSession` 作为参数，由 Router 通过 `get_db()` 依赖注入传入。这样测试时可以直接注入 mock session。

```python
# 伪代码示意
class EloService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record_match(self, req: RecordMatchRequest) -> RecordMatchResponse:
        # 1. 查询选手
        # 2. 调用算法
        # 3. 写库
        # 4. 返回
```

## Testing Decisions

- **测试目标**：只测试外部行为，不测试实现细节
- **算法层**（`elo_compute.py`、`winning_rate.py`）：纯函数单元测试，无需 mock，直接用 pytest 验证输入输出
- **Service 层**（`services/elo_service.py`、`services/prediction_service.py`）：注入 `AsyncMock` session，验证：
  - 正确调用算法函数
  - 正确执行 DB 读写操作
  - 正确处理边界情况（选手不存在、双打人数不匹配等）
- **API 层**：用 FastAPI `TestClient` 测试请求/响应格式和状态码
- 优先使用 `pytest` + `pytest-asyncio`

## Out of Scope

- 历史数据批量回放（"历史数据回放流程"中的 `SELECT ... ORDER BY event_id, event_index` 全量回放）—— 这是单独的任务，后续处理
- 增量更新机制（新比赛通过 Webhook 或事件触发）—— 当前只做手动 API 触发
- 段位映射（`get_badminton_rank()` 函数）—— 纯展示层，不涉及 Elo 计算
- 数据可视化（排名曲线、趋势图等）
- 用户认证和权限
- 赛事权重从数据库动态获取（目前统一 `1.0`，后续可扩展）
- CBT（基于能力的测试）—— 这是一项耗时且复杂的工作，需要专门的研究和设计，独立于当前功能交付

## Further Notes

- 算法层（`elo_compute.py` 和 `winning_rate.py`）已经过充分设计，本次不做任何修改
- `event_weight` 默认为 `1.0`，后续可从赛事表扩展
- `relation_graph` 目前从 `elo_match_record` 实时构建，数据量增大后需考虑缓存策略
- 双打预测的"两两计算取平均"是简化方案，后续可引入更复杂的队伍评分模型