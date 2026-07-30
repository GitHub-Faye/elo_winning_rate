# 羽毛球 Elo 数据库分析文档

> 基于 yzmp_dev 数据库实际数据分析（2026-07-29）

---

## 1. 数据概览

### 1.1 与羽毛球相关的关键表

| 表名 | 行数 | 作用 |
|---|---|---|
| `motion_user` | 73,519 | 用户主表，`user_id` 主键 |
| `motion_event` | 613 | 赛事信息，`event_motion_type='badminton'` 的占 **591 条** |
| `motion_event_apply` | 60,725 | 用户报名订单 |
| `motion_event_apply_user_setting` | 124,052 | 报名人员明细，`member_id` → `motion_user.user_id` |
| `motion_event_layout_stage_player` | 128,737 | 选手阶段映射，含 `user_ids`(逗号分隔的 user_setting_id) |
| `motion_event_layout_stage_battle` | 189,444 | **比赛对阵表，核心竞争力数据**，含比分、胜负 |
| `motion_user_sports` | 153,101 | 用户关联运动品类 |

### 1.2 运动品类（`motion_event.event_motion_type`）

| 类型 | 赛事数 |
|---|---|
| `badminton`（羽毛球） | **591** ← 这是我们要处理的 |
| `comprehensive`（综合） | 12 |
| `tabletennis`（乒乓球） | 6 |
| `basketball`（篮球） | 2 |
| `pickball`（匹克球） | 2 |

---

## 2. 羽毛球的比赛数据

### 2.1 已完成的有效对阵数

```
羽毛球 + 已结束 + 非轮空 + 有比分
  → 总计 75,293 场
```

### 2.2 按实际上场人数判断单打/双打

不管 `project_type`（单打类/团体类）和 `project_sub_type`（常规/十羽争分/五羽轮比），只看 **每场 battle 实际对阵双方的人数**：

| 对阵形式 | 场数 |
|---|---|
| **单打（1人 vs 1人）** | 32,603 |
| **双打（2人 vs 2人）** | 19,272 |
| 单 vs 双 / 双 vs 单 | 3（可忽略） |
| 多人（4-11人） | 66（可忽略） |
| 其余（含团体赛 project_type=2 等） | 约 23,349 |

> 核心逻辑：`project_type` 和 `project_sub_type` 只是赛制标识，不影响 Elo 计算。**按实际 user_ids 的逗号数量判断单打或双打**，与赛制无关。

### 2.3 羽毛球赛事跨度

75,293 场比赛分布在 **230 场不同的赛事**中，时间跨度足够覆盖长期 Elo 演化。

---

## 3. 数据链路（完整解析路径）

### 3.1 实际数据

**对阵表** `motion_event_layout_stage_battle`：

| 关键字段 | 含义 |
|---|---|
| `battle_id` | 对阵ID |
| `event_id` → `motion_event.event_id` | 关联赛事，取 `event_motion_type` |
| `project_type` | 1=单打类 2=团体（仅赛制标识，Elo 按实际上场人数处理）|
| `project_sub_type` | 1=常规 2=十羽争分 3=五羽轮比（同上，仅赛制标识）|
| `player_one_id` → `stage_player.id` | 选手段位1 |
| `player_two_id` → `stage_player.id` | 选手段位2 |
| `player_one_score` | 段位1得分 **（部分正常得分为 0/1/2，也有 `-1` 表示异常）** |
| `player_two_score` | 段位2得分 |
| `victory_player_id` | 获胜方（可为 null，用比分判断更可靠） |
| `status` | 0=未开始 1=进行中 **2=已结束** |
| `is_empty` | 0=非轮空 1=轮空 |
| `battle_time` | 比赛时间（可为 null） |

**选手映射** `motion_event_layout_stage_player`：

| 关键字段 | 含义 |
|---|---|
| `id` | 选手阶段ID（被 battle 引用） |
| `user_ids` | **逗号分隔的 user_setting_id** |
| `player_names` | 逗号分隔的选手姓名 |
| `apply_id` | 报名ID |

**用户最终映射** `motion_event_apply_user_setting` → `motion_user`：

```
battle.player_one_id
  → stage_player.id
    → stage_player.user_ids = "user_setting_id1,user_setting_id2,..."
      → motion_event_apply_user_setting.user_setting_id
        → motion_event_apply_user_setting.member_id
          → motion_user.user_id  （最终用户）
```

### 3.2 示例

实际数据验证链路完整：

```
battle_id=3202
  → player_one_id=4494, player_two_id=4495
  → player_one_score=1, player_two_score=0
  → victory_player_id=4494

stage_player.id=4494:
  → user_ids="3234", player_names="侯庆泽"
  → apply_id=2010

motion_event_apply_user_setting.user_setting_id=3234:
  → member_id=505481

motion_user.user_id=505481:
  → nick_name="乆"
```

---

## 4. 需补充的数据

### 4.1 赛事权重（`event_weight`）

`motion_event` 表未显著标识"赛事级别/权重"字段。可行的方案：
- 使用 `event_scale` 字段（varchar，默认 `"D"`），但数据可能稀疏
- **默认设 `event_weight=1.0`**，后续按赛事级别人工调整

### 4.2 比赛时间（`battle_time`）

部分 battle 记录 `battle_time` 为 null。在做历史数据回放时，如果没有时间戳，应按 `event_id` + `event_index`（总场序号）排序。

### 4.3 双打时区分用户

双打时 `user_ids="1547,1548"`，需要按逗号拆分后分别查询 `motion_event_apply_user_setting`。

---

## 5. 新增表设计

### 5.1 `elo_player_rating` — 选手 Elo 分段位分

```sql
CREATE TABLE elo_player_rating (
    user_id         BIGINT NOT NULL COMMENT '用户ID，逻辑外键 → motion_user.user_id',
    sport_type      VARCHAR(32) NOT NULL DEFAULT 'badminton' COMMENT '运动品类，如 badminton / tabletennis',

    rating          DECIMAL(10,2) NOT NULL DEFAULT 1500.00 COMMENT '当前 Elo 分',
    games           INT NOT NULL DEFAULT 0 COMMENT '总比赛场次',
    wins            INT NOT NULL DEFAULT 0 COMMENT '胜场',
    losses          INT NOT NULL DEFAULT 0 COMMENT '负场',
    draws           INT NOT NULL DEFAULT 0 COMMENT '平场',
    highest_rating  DECIMAL(10,2) NOT NULL DEFAULT 1500.00 COMMENT '历史最高 Elo 分',
    lowest_rating   DECIMAL(10,2) NOT NULL DEFAULT 1500.00 COMMENT '历史最低 Elo 分',
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (user_id, sport_type),
    INDEX idx_rating (rating DESC)
) COMMENT='选手 Elo 分段位分（按运动品类）';
```

> 设计思路：
> - `(user_id, sport_type)` 复合主键，预留多运动品类扩展
> - 不设数据库外键约束。原因详见下文 **§ 外键决策**

### 5.2 `elo_match_record` — 比赛 Elo 变化日志

**核心变更：每人每场一条记录**，统一处理单打和双打。

```
单打：1 场 → 2 条（每人 1 条）
双打：1 场 → 4 条（每人 1 条）
```

```sql
CREATE TABLE elo_match_record (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_id        INT NOT NULL COMMENT '赛事ID，逻辑外键 → motion_event.event_id',
    battle_id       INT NOT NULL COMMENT '对阵ID，逻辑外键 → motion_event_layout_stage_battle.battle_id',
    source_order    INT NOT NULL DEFAULT 0 COMMENT '赛事内场序号（event_index），用于回放排序',

    -- 选手维度（一人一条）
    user_id         BIGINT NOT NULL COMMENT '选手用户ID，逻辑外键 → motion_user.user_id',
    team_side       VARCHAR(1) NOT NULL COMMENT '所在方 A 或 B',
    team_size       TINYINT NOT NULL COMMENT '队内人数 1=单打 2=双打',
    is_winner       TINYINT NOT NULL COMMENT '本方是否获胜 1=是 0=否',

    -- 赛前赛后 Elo 状态
    rating_before   DECIMAL(10,2) NOT NULL COMMENT '赛前 Elo',
    delta           DECIMAL(10,2) NOT NULL COMMENT 'Elo 变化（正=加分，负=减分）',
    rating_after    DECIMAL(10,2) NOT NULL COMMENT '赛后 Elo',

    -- 因子分解（完整复现计算所需）
    expected            DECIMAL(10,4) NOT NULL COMMENT '本方预期胜率 E',
    k_factor            DECIMAL(10,2) NOT NULL COMMENT 'K 值',
    weight_multiplier   DECIMAL(10,4) NOT NULL COMMENT '赛事权重 M_weight',
    margin_multiplier   DECIMAL(10,4) NOT NULL COMMENT '分差倍率 M_margin',
    base_delta          DECIMAL(10,2) NOT NULL COMMENT 'clamp 前的普通变化',
    clamped_delta       DECIMAL(10,2) NOT NULL COMMENT 'clamp 后的普通变化',
    upset_bonus         DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '越级加分 bonus',
    upset_penalty       DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '被越级扣分 penalty',

    -- 对方信息
    opponent_user_id    BIGINT NOT NULL COMMENT '对手用户ID（双打时为第一个对手）',
    opponent_partner_id BIGINT DEFAULT NULL COMMENT '双打时第二个对手，单打为 NULL',

    -- 比赛信息
    score_self          INT NOT NULL COMMENT '本方得分',
    score_opponent      INT NOT NULL COMMENT '对方得分',
    played_at           DATETIME DEFAULT NULL COMMENT '比赛时间（来自 battle_time，可为空）',

    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_user_id (user_id),
    INDEX idx_user_played_at (user_id, played_at),
    INDEX idx_battle_id (battle_id),
    INDEX idx_event_id (event_id),
    INDEX idx_played_at (played_at)
) COMMENT='Elo 变化日志：每人每场一条记录，支持单打和双打';
```

> 不设数据库外键约束。详见下文 **§ 外键决策**。

---

## 6. 段位映射（代码层）

```python
BADMINTON_RANK_TIERS = [
    (2600, "🏆 传奇"),
    (2400, "👑 大师"),
    (2200, "💎 钻石"),
    (2000, "🌟 铂金"),
    (1800, "🥇 黄金"),
    (1600, "🥈 白银"),
    (1400, "🥉 青铜"),
    (0,    "⚪ 新手"),
]

def get_badminton_rank(rating: float) -> str:
    for threshold, name in BADMINTON_RANK_TIERS:
        if rating >= threshold:
            return name
    return "⚪ 新手"
```

---

## 7. 历史数据回放流程

```
① 查询羽毛球已完成对阵（不再限制 project_type）
   SELECT b.* FROM motion_event_layout_stage_battle b
   INNER JOIN motion_event e ON b.event_id = e.event_id
   WHERE e.event_motion_type = 'badminton'
     AND b.status = 2 AND b.is_empty = 0
   ORDER BY b.event_id, b.event_index

② 过滤有效比分
   IF player_one_score < 0 OR player_two_score < 0 → 跳过（弃权/异常）
   IF player_one_score IS NULL OR player_two_score IS NULL → 跳过
   IF player_one_score == player_two_score → 跳过（平局暂不处理）

③ 判断单打/双打（按实际 user_ids 逗号数量，与赛制无关）
   解析 player_one_id → stage_player.user_ids
   解析 player_two_id → stage_player.user_ids
   - 双方都只有1个 user_setting_id → 单打
   - 双方都只有2个 user_setting_id → 双打
   - 其他 → 跳过（人数不匹配，暂不处理）

④ 映射到真实用户ID
   user_setting_id → motion_event_apply_user_setting.member_id

⑤ 查询双方当前 Elo 分
   SELECT rating, games, wins, losses FROM elo_player_rating WHERE user_id = ?

⑥ 调用 elo_compute.py
   单打 → compute_match_pair()
   双打 → compute_team_match()

⑦ 写入 elo_match_record
⑧ 更新 elo_player_rating
```

---

## 8. 关键注意事项

| 注意点 | 说明 |
|---|---|
| `-1` 比分 | 有少量 `score=-1` 的记录，表示弃权或异常，应跳过 |
| 单打/双打判定 | 不看 `project_type`，只看 `user_ids` 逗号数量 |
| 双打映射 | 2个 user_setting_id 对应2个 user_id，取平均 Elo 作为队伍评分 |
| 赛事权重 | 没有现成的权重字段，先统一用 `1.0` |
| 同赛事内排序 | 用 `event_index`（总场序号）而不是 `battle_time`（部分为空） |
| 增量更新 | 历史数据回放完一遍后，后续新比赛记录触发实时计算 |
