# 工单4：身份证作为选手定位键（替换 user_id）

**What to build:** 把选手定位键从 `user_id`（int）切换为身份证号 `card_code`（varchar(32)），
覆盖 `elo_player_rating`（分表主键）、`elo_match_record`（选手/对手字段）、
Elo 记录 API、胜率预测 API、交手记录 API 及全部 mock 测试。

**背景（已确认的事实，来自 dbhub 分析）：**
- 未注册用户没有 `user_id`（`motion_event_apply_user_setting.member_id = 0`），
  赛事中能唯一定位选手段位与胜负记录的只有身份证号 `card_code`。
- `card_code` 即身份证号（varchar(32)，含校验，无需保留 `card_type`）——用户已确认。
- 存量 46 条 elo_match_record（user_id 1001-1008 测试数据）——用户已确认**清空重建**，
  不做迁移。

## 变更清单

### 1. 数据库（Alembic 迁移 `xxxx_idcard_player_key`）
- `elo_player_rating`：
  - 新增 `card_code` varchar(32)，回填虚拟身份证（来自 `seed_test_data.py`），置 NOT NULL
  - 主键改为 `(card_code, sport_type)`
  - 删除 `user_id` 列（含旧主键）
- `elo_match_record`：
  - 新增 `card_code` varchar(32) NOT NULL（选手身份证）
  - 新增 `opponent_card_code` varchar(32) NULL（对手身份证，双打为第一个对手）
  - 新增 `opponent_partner_card_code` varchar(32) NULL（双打第二个对手）
  - 删除 `user_id` / `opponent_user_id` / `opponent_partner_id`
- 迁移前**清空**两张表（用户确认：存量为测试数据，清空重建）
- `b1f888ad99af` 的 `upgrade()` 也同步改为新 schema（空表历史，无回填需求）

### 2. 核心模型 `core/models.py`
- `EloPlayerRating`：主键 `card_code: str` + `sport_type`
- `EloMatchRecord`：`card_code` / `opponent_card_code` / `opponent_partner_card_code`

### 3. API Schema `core/schemas.py`
- `EloRecordRequest`：`team_a`/`team_b` 改为 `list[str]`（身份证号）
- `PlayerResult`：`card_code`（替换 `user_id`）、`opponent_card_code`、`opponent_partner_card_code`
- `PredictionRequest`：`team_a`/`team_b` 改为 `list[str]`；去重校验逻辑不变
- `PlayerPredictionResult`：`card_code`（替换 `user_id`）
- `HeadToHeadData`/`HeadToHeadRecord`：`player_a_card`/`player_b_card`/`winner_card`

### 4. 服务层
- `services/elo_service.py`：`_PlayerState.card_code`；`_load_player_states` 按 `card_code`
  查 `elo_player_rating`；`_save_record` 写 `card_code`/`opponent_card_code`/`opponent_partner_card_code`
- `services/prediction_service.py`：`build_relation_graph` 按 `card_code`/`opponent_card_code`
  构建关系图；`PlayerRatingSnapshot` 键为 card_code
- `services/head_to_head_service.py`：按 `card_code` 匹配；`winner_card` 判定
- `routers/elo.py`、`routers/prediction.py`、`routers/head_to_head.py`：参数名/说明同步

### 5. 测试 `tests/`
- `test_elo_service.py` / `test_prediction_service.py` / `test_head_to_head_service.py`：
  记录构造、mock DB、断言全部改用身份证号

### 6. seed 脚本 `seed_test_data.py`
- `PLAYERS` 键从 user_id 改为身份证号，增加 `name` 与出生日期/性别信息

## 完成标准
- 全量 `pytest` 通过
- Alembic 迁移可 `upgrade` 成功（dbhub 验证表结构）
- 重新 seed 后真实库可用：`elo_player_rating` 以 `card_code` 为主键
