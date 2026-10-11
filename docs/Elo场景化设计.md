# Elo 场景化加减分公式设计

## 1. 核心理念

Elo 加减分不是写死一大套 if-else 做情景枚举，而是将每个**最小情景原子单元**归类到公式中它所属的因子角色，按统一模板组装。加一个新场景 = 加一个纯函数 + 插到正确位置。

---

## 2. 情景最小原子单元

所有参与 Elo 计算的、不可再拆的独立判定条件，共 **11 个**：

### 选手原子（5 个）

| # | 原子 | 判定方式 | 影响 |
|---|---|---|---|
| 1 | 定级期 | `games < 2` | 使用 K=40 |
| 2 | 观察期 | `2 <= games < 30` | 使用 K=28 |
| 3 | 稳定期 | `games >= 30` | 使用 K=20 |
| 4 | 越级加分 | `games < 2 AND 胜 AND ΔElo ≥ 150` | 触发额外加分 |
| 5 | 被越级扣分 | 对手触发越级加分且本方输 | 触发额外扣分 |

### 比赛原子（4 个）

| # | 原子 | 判定方式 | 影响 |
|---|---|---|---|
| 6 | 比赛类型 | 队员人数：1 = 单打，2 = 双打 | 单打取个人 Elo，双打取平均 |
| 7 | 胜负结果 | `score_a > score_b` | S = 1.0 / 0.0 / 0.5 |
| 8 | 分差幅度 | `abs(score_a - score_b)` 连续值 | margin = 1.0 ~ 1.5 |
| 9 | 赛事权重 | CSV AQ 列值 | delta × weight |

### 队伍原子（2 个）

| # | 原子 | 判定方式 | 影响 |
|---|---|---|---|
| 10 | 队伍 Elo 差 | `team_rating_a - team_rating_b` 连续值 | 预期胜率 E |
| 11 | 队内人数 | 1 或 2 | 平均值计算、delta 等分 |

---

## 3. 公式模板

所有原子落地到公式时，只属于 **4 种因子角色**：

| 角色 | 符号 | 行为 | 已有原子 |
|---|---|---|---|
| 基础变化量 | `Δbase` | K × (S - E) | 队伍 Elo 差，胜负结果，赛龄 |
| 放大倍率 | `M₁ × M₂ × ...` | 乘在 base 上 | 分差幅度，赛事权重 |
| 封顶器 | `clamp(±cap)` | 限制单场波动 | — |
| 额外补丁 | `±bonus` | 乘性体系外的独立修正 | 越级加分，被越级扣分 |

### 3.1 通用公式

```
delta = clamp(K × M₁ × M₂ × ... × (S - E), ±cap) + bonus₁ + bonus₂ + ... - penalty₁ - penalty₂ - ...
```

### 3.2 当前系统的完整公式

```
① 计算预期胜率 E
   if 单打: team_rating = player.rating
   if 双打: team_rating = avg(player.rating, partner.rating)
   E = 1 / (1 + 10 ^ ((opponent_team_rating - team_rating) / elo_scale))

② 实际胜负 S
   S = 1.0 (score_a > score_b)
   S = 0.0 (score_a < score_b)
   S = 0.5 (score_a == score_b)

   输球减分的逻辑不单独写，而是由 (S - E) 的数学结构自动实现：
   - 赢球 → S = 1.0 → S - E > 0 → delta 为正 → 加分
   - 输球 → S = 0.0 → S - E < 0 → delta 为负 → 减分
   - 预期胜率越低（爆冷），|S - E| 越大，变化幅度越大
   - 预期胜率越高（本该赢），|S - E| 越小，变化幅度越小
   所以在常规场景中，加分和减分共用同一套公式，不需要单独写输球的 if 分支。

③ 分差倍率 M_margin
   diff = abs(score_a - score_b)
   cap = max(score_a, score_b, min_margin_cap, 1)
   M_margin = 1 + min(diff, cap) / cap × margin_weight

④ 赛事权重 M_weight
   M_weight = match_weight × csv_weight

⑤ K 值
   if games < 2:               K = new_player_k      # 40
   if games < provisional:     K = provisional_k      # 28
   else:                       K = stable_k           # 20

⑥ 普通变化
   base = K × M_weight × M_margin × (S - E)
   base_clamped = clamp(base, -delta_cap, +delta_cap)

⑦ 越级加分（定级中选手获胜且对手高出足够多，不受 clamp 限制）
   bonus 与常规加分无关：常规加分由 (S - E) 的数学结构自动处理输赢，
   bonus 是 clamp 之后叠加的额外修正，用于快速纠正初始值对新人水平的低估。
   if games < new_player_games AND S > 0.5 AND gap >= upset_min_rating_gap:
       bonus = min(upset_bonus_cap, gap / 100 × upset_bonus_per_100) × M_weight

⑧ 被越级扣分（高段位输给定级新人）
   if opponent 获得了 bonus AND S < 0.5:
       penalty = opponent_bonus × upset_loser_penalty_ratio / team_size

⑨ 最终变化
   delta = base_clamped + bonus - penalty

⑩ 更新选手
   rating += delta
   games += 1
   if S > 0.5 → wins += 1
   if S < 0.5 → losses += 1
```

### 3.3 因子角色分配表

每一类原子到因子角色的映射是 **一对一的、确定的**：

```
最小原子 → 因子角色 → 公式位置

定级期 ───────── K 值选择 ────── ⑤
观察期 ───────── K 值选择 ────── ⑤
稳定期 ───────── K 值选择 ────── ⑤
越级加分 ─────── bonus ──────── ⑦
被越级扣分 ───── penalty ────── ⑧
比赛类型 ─────── 队伍评分策略 ── ①
胜负结果 ─────── S 值 ──────── ②
分差幅度 ─────── M_margin ──── ③
赛事权重 ─────── M_weight ──── ④
队伍 Elo 差 ──── E 计算 ────── ①
队内人数 ─────── 队伍评分策略 ── ①
```

---

## 4. 流程总览

```
比赛原始数据
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ 数据处理层（不属于算法层）                           │
│  ① 按列位解析为 Match                               │
│  ② 按数据量比例稳定抽样（30% / 60% / 100%）          │
│  ③ 按 played_at → source_order → match_id 排序      │
└─────────────────────────────────────────────────────┘
  │
  ▼  逐场回放
┌─────────────────────────────────────────────────────┐
│ Elo 评分引擎（算法层）                               │
│                                                      │
│  ┌─ 11 个最小原子 ──────────────────────────┐       │
│  │ 选手: 定级期/观察期/稳定期/越级/被越级   │       │
│  │ 比赛: 类型/胜负/分差/权重               │       │
│  │ 队伍: Elo差/人数                       │       │
│  └────────────────────────────────────────┘       │
│         │                                           │
│         ▼ 分组                                      │
│  ┌─ 4 种因子角色 ──────── 公式模板 ─────┐          │
│  │ K    → K × (S - E)                   │          │
│  │ M₁   → 分差倍率                      │          │
│  │ M₂   → 赛事权重                      │          │
│  │ cap  → clamp(±delta_cap)            │          │
│  │ bonus → 越级加分                     │          │
│  │ penalty → 被越级扣分                 │          │
│  └────────────────────────────────────┘          │
│         │                                           │
│         ▼                                           │
│  更新选手 Elo / 场次 / 胜负                         │
└─────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ 关系图 + 预测引擎（不做 Elo 运算）                  │
│  ① 构建选手胜负关系图                               │
│  ② Elo 基础胜率 + 直接交手修正 + 间接路径修正        │
│  ③ 输出预测结果（不回写 Elo）                       │
└─────────────────────────────────────────────────────┘
```

---

## 5. 扩展方法论

### 5.1 加一个新情景的步骤

要增加一个新的情景原子，只需要：

1. **判断它属于哪种因子角色**

| 新原子举例 | 因子角色 | 理由 |
|---|---|---|
| 连败保护 | 封顶器 cap | 修改封顶值，不改公式结构 |
| 近期状态（近5场胜率） | 放大倍率 M | 乘性修正 |
| 年龄系数 | K 值计算逻辑 | 和赛龄同类 |
| 生锈惩罚（长期不打） | 额外补丁 bonus | 乘性体系无法覆盖的衰减 |
| 决赛权重 ×2 | 放大倍率 M | 和赛事权重同类 |
| 段位隔离 | 放大倍率 M | 倍率放大 |
| 连胜加成 | 额外补丁 bonus | 独立于预期差 |

2. **实现为一个纯函数**

```
输入（相关原子 + config）→ 输出（因子值）
```

3. **插入公式模板的对应位置**

```
K 系列 ──→ 步骤⑤ K 值计算
M 系列 ──→ 步骤③–④ 倍率链
cap 系列 ─→ 步骤⑥ clamp
bonus 系列 → 步骤⑦
penalty 系列 → 步骤⑧
```

### 5.2 不破坏公式结构的原则

- 所有 **M 因子** 相乘，不分先后（乘法交换律）
- 所有 **bonus / penalty** 累加，在 clamp 之后独立计算（不影响封顶边界）
- **K 值** 只由选手赛龄决定，不与其他因子耦合
- **cap** 只限制 `K × M × (S - E)`，不限制 bonus
- 新增因子时优先选用 M 角色，其次 bonus，最后才考虑修改公式结构

### 5.3 当前系统的扩展点标记

```
delta = clamp(K × M₁ × M₂ × [M₃...] × (S - E), ±[cap₁...]) + bonus₁ - penalty₁
                │     │     │                      │          │        │
                │     │     └── 已预留             │          │        │
                │     └──────── 赛事权重            │          └── 已预留
                └────────────── 分差                 │
                                                     └── 已预留
```

- M₃+: 可轻松增加倍率因子
- cap₁+: 可增加不同维度的封顶（如分项 cap）
- bonus₂+: 可增加独立加减分项
- penalty₁: 可增加独立扣分项

### 5.4 不推荐的做法

| 做法 | 问题 |
|---|---|
| 在公式中写 if-else 判断场景组合 | 组合数爆炸（11 个原子全排列已超 2000 种），维护成本高 |
| 为特定场景硬编码新公式 | 不可复用，下次加场景又要改公式 |
| 在 clamp 前做 bonus 加减 | 让 bonus 被 cap 吃掉，预期效果丢失 |
| 把 M 因子写在 bonus 里 | 乘法和加法的语义混乱，预测时无法正确分解 |

---

## 6. 场景组合生效规则

实际的场景组合由 **11 个最小原子的当前取值** 决定，**不需要显式维护组合清单**。每次 `rate_matches` 循环中：

```
一场比赛的处理 = 读取 11 个原子的值 → 代入 4 种因子角色 → 走通公式模板 → 得到 delta
```

新选手 + 单打 + 碾压局 + 高权重赛事 + 对手明显更强 = 自然得到「定级新人越级碾压强手」的计分结果。无需专门写这个组合的 case。

这正是原子化设计的核心：**组合由数据驱动，不由代码枚举。**

---

## 7. 胜负预测（补充说明）

预测引擎位于 Elo 运算之后，是独立的查询层：

```
预测胜率 = clamp(
    Elo 基础胜率              (队伍 Elo 差 → expected_score)
  + 直接交手修正              (relation_graph → 历史交手胜率 × 场次系数)
  + 间接路径修正              (relation_graph → 5 层 beam-search → 加权信号)
  , 0.05, 0.95
)
```

预测不参与 Elo 更新（不回写），因此不进入公式模板。其场景原子与评分引擎共享 `队伍 Elo 差` 和 `relation_graph`。

---

## 8. 参数训练方法

所有可调超参数集中在 `elo_compute.EloConfig`（frozen dataclass）。训练 = 「在历史比赛集上按时间重放 → 计算预测损失 → 搜索更优参数」。项目已具备训练所需的地基：`best_config.json` + `import_historical_matches.load_best_config()` / `_config_from_dict()` 已能在重放时注入参数，缺的只是「损失函数 + 搜索循环」。

> ⚠ 本节参数一律以 **`elo_compute.EloConfig` 当前代码默认值**为准。第 2/3 节里 K=40/28/20、观察期 `< 30`、越级门槛 `ΔElo ≥ 150` 是早期设计稿，已废弃，实际代码为 K=80/30/15、`provisional_games=60`、`upset_min_rating_gap=50`。

### 8.1 训练目标：对数损失（log-loss）

Elo 系统的可预测量是「每场每方的预期胜率 E」。训练目标 = 让 E 尽量贴近真实结果 y（胜=1，负=0）：

```
L = −( y·ln E + (1−y)·ln(1−E) )
```

- 求所有 `elo_match_record` 行的均值（每场有 A/B 两方，双方 E 相加 =1，损失对称，可每场只算一方）。
- **关键**：E 会随参数变而整体重算，所以 **不能拿库里已存的 `expected` 直接算损失，必须用候选参数重新回放一遍**。
- 备选：Brier 分数 `(E−y)²`（对极端预测更宽容）、准确率（只作粗筛，不够敏感）。

### 8.2 数据切分（必须按时间序，禁止 shuffle）

Elo 顺序累积、样本不独立：

- 按 `battle_time → source_order → battle_id` 升序
- 前 ~80% 时间窗作训练集、后 ~20% 作验证集（模拟「在线预测未来」）
- 验证集新选手从 1500 冷启动，正好检验参数对新人的适应速度

### 8.3 调参直觉（各参数作用方向）

表中为 `EloConfig` 当前默认值：

| 参数 | 默认 | 调大 → | 调小 → |
|---|---|---|---|
| `new_player_k` | 80 | 新人快速到位、波动大 | 收敛慢、更稳 |
| `provisional_k` | 30 | 观察期更激进 | 更保守 |
| `stable_k` | 15 | 老手排名易躁动 | 排名稳但滞后 |
| `new_player_games` / `provisional_games` | 2 / 60 | 新人/观察期拉长 | 更快进入稳定期 |
| `elo_scale` | 200 | 分差对 E 影响变小（保守） | 分差放大（激进） |
| `delta_cap` | 80 | 单场波动上限变大 | 波动收窄 |
| `upset_min_rating_gap` | 50 | 越级更难触发 | 更易触发 |
| `upset_bonus_per_100` / `upset_bonus_cap` | 15 / 25 | 越级加更多 | 加更少 |
| `upset_loser_penalty_ratio` | 0.5 | 被爆冷罚更重 | 罚更轻 |

> 注意：`event_weight` 现被接口硬编码为 1.0（`match_weight` 也 = 1.0），即 `M_weight` 恒为 1、是空操作。**赛事权重这一维的训练需先恢复 event_id → weight 的映射**。

### 8.4 搜索方法

参数空间小（~12 个）但目标非光滑（K 分段常数、clamp / bonus / penalty 有硬阈值），不宜用梯度法：

1. **坐标下降**：固定其余参数，逐参数一维搜索（网格或黄金分割），收敛快、可解释
2. 优先调**离散/分段参数**：`new_player_games`、`provisional_games`、三档 K、`upset_min_rating_gap`
3. 再调**连续参数**：`elo_scale`、`delta_cap`、`upset_bonus_per_100`、`upset_bonus_cap`、`upset_loser_penalty_ratio`
4. 每组候选参数都要**整集重放**；可多进程并行 + 只跑纯函数（不落库）加速

### 8.5 诊断指标（除 log-loss 外）

- **log-loss**（主目标，越低越好）
- **校准曲线**：把 E 分桶（0~0.1、0.1~0.2…），桶内真实胜率应 ≈ 桶中心
- **新人收敛速率**：新选手前 N 场的 log-loss 是否随 K 增大而快速下降
- **训练/验证差**：差值大说明过拟合，应减小参数自由度或回退

### 8.6 落地步骤（对应本项目）

1. 复用 `import_historical_matches.extract_importable_matches()` 取全量有序比赛
2. 写 `train_elo_params.py`：遍历候选 `EloConfig` → 用 `elo_compute.compute_match_pair() / compute_team_match()` **纯函数在内存重放**（不写库）→ 累加 log-loss → 记录「参数 → 训练/验证 log-loss」
3. 选验证集最优且训练/验证差小的一组
4. 把结果写入 `best_config.json`（`_config_from_dict` 已能自动加载），跑一次全量 `import_historical_matches.py` 落库

> 轻量替代：若不想重复搭内存重放，可把每组参数用 `replay_matches()` 灌进一个**临时库**，再 `SELECT expected, is_winner FROM elo_match_record` 算 log-loss——复用现有代码，但每轮都要清库重放、慢一些。

---

## 9. 单打选手六维雷达图设计

雷达图是**独立于 Elo 的查询层**：不参与积分运算、不回写任何表。它从外部赛事记分链路重建「逐分事件流」，把选手能力拆成六个 0–100 的维度，用于呈现选手风格画像。对应实现：`services/radar_service.py` + `routers/radar.py`，调参常量集中在文件头部。

### 9.1 边界与前提

- **只统计单打**（`motion_tool_score_team.score_type = 1`）；双打 / 五羽轮比 / 团体（score_type=2/3）一律排除。
- 取选手**最近 N 场**单打（默认 10，接口上限 50），并非全历史。
- 依赖记分系统把**每一分**都落到 `motion_tool_score_log`，并用 `station` JSON 里的 `serverBall` 区分发球权——没有逐分日志就无从谈起。
- 选手定位键与 Elo 一致：身份证优先，否则手机号（`get_battles_by_player_key`）。

### 9.2 数据链路

```
card_code / phone（统一定位键）
  → motion_event_apply_user_setting（user_setting_id, name）
  → get_battles_by_player_key（该选手所有对阵 battle_id）
  → battle_to_score_team（逐个 battle 关联记分表，只保留 score_type=1 单打）
  → motion_tool_score_team（score_team_id, team_one/two_score, 比分, create_time）
  → fetch_logs → motion_tool_score_log（log_id 升序：逐分 + is_revoke=0）
  → rebuild_score_sequence（按 round_num 重建每局事件序列，含发球权/换边标记）
  → 六维计算（纯函数，可单测）
```

### 9.3 六维指标定义

| 维度 | 原始量 | 公式 / 说明 |
|---|---|---|
| **进攻 offense** | 本方有发球权时的得分率 | `my_serve_won / my_serve_pts × 100`，再经 `sigmoid_map` 归一化 |
| **防守 defense** | 本方接对方发球时的得分率 | `opp_serve_won / opp_serve_pts × 100`，同上归一化 |
| **发球 serve** | 本方发球回合得分率 | 与进攻同源（`serve = offense`） |
| **接发 receive** | 本方接发回合得分率 | 与防守同源（`receive = defense`） |
| **抗压 anti_pressure** | 落后/逆转/关键分表现 | 见 9.4 公式，跨局逆风加权 |
| **场区 field** | 换边前后落差 | `100 − (ΔP×2.2 + ΔE×3.0 + ΔO×1.8)`，落差越小分越高 |

> 发球权判定：`station` JSON 中 `teamType == 我方` 的一方 `serverBall == true` → 本方发球；解析失败则该事件不参与攻/防统计（`my_serve is None` 跳过）。

### 9.4 标准化与公式细节

**① 得分率归一化（进攻/防守/发球/接发共用）**

```
sigmoid_map(pct) = 100 / (1 + e^(−SIGMOID_K × (pct − SIGMOID_CENTER)))
默认 SIGMOID_K=12, SIGMOID_CENTER=0.50（50% → 50 分）
```

用 sigmoid 而非线性：次极端值被拉开、极端值压缩到上下界，六维图视觉区分度更高。

**② 抗压（逐局，再跨局加权）**

```
S = 50 + 3.5·D − 2.5·L + 20·R + 15·K − E_COEF·E   （clamp 0–100）

D = 本局最大落后分差
L = 本局最长连续失分
R = 逆转标志：D>0 且最终胜=1，D>0 且最终负=0，否则=0.5
K = 关键分胜率：任一方打到 20 分（GAME_CAP−1）且分差 ≤1 的回合中本方得分占比
E = 落后阶段失误数（deficit>0 且本方丢分的次数），E_COEF=1.0 压低其权重
```

跨局加权平均：**逆转局 ×1.2**、**顺风局（全程未落后）×0.8**、其余 ×1.0。

**③ 场区（换边前后落差）**

先把一局按换边拆成前后两段：
- 有 `is_sides` 日志 → 用换边事件切分；
- 无换边日志 → 用羽毛球规则近似（任一方达到 11 分换边）。

```
ΔP = |本方前段得分 − 本方后段得分|
ΔE = |对手前段得分 − 对手后段得分|
ΔO = ΔP（无进攻分类，用得分差近似）
field = clamp(100 − (ΔP×2.2 + ΔE×3.0 + ΔO×1.8), 0, 100)
```

含义：换边后比分走势越平稳（落差小），对场地适应性越好，分越高。

### 9.5 连续得分 / 连续失分（附加指标）

不归入六维，作为看板附加项：

- **平均连续得分 avg_score** = 连胜片段的平均长度
- **平均连续失分 avg_lose** = 连失片段的平均长度
- **最大连胜 max_score** / **最大连失 max_lose**（单场维度）

### 9.6 汇总与接口

每场先算六维 → 对最近 N 场的各维**取算术平均**输出：

```
GET /api/v1/radar/{card_code}?limit=10
→ { name, card_code, matches, total_singles,
    offense, defense, serve, receive, anti_pressure, field,
    consecutive_score, consecutive_lose, match_details[] }
```

`matches` = 实际参与计算的场数（最近 N 场单打），`total_singles` = 历史全部单打场数。

### 9.7 调参常量（`radar_service.py` 头部）

| 常量 | 默认 | 作用 |
|---|---|---|
| `E_COEF` | 1.0 | 抗压公式里「落后失误 E」的系数（已压低，因 E≈落后丢分非真失误） |
| `SIGMOID_K` | 12.0 | 得分率归一化的 sigmoid 斜率（越大中间越陡） |
| `SIGMOID_CENTER` | 0.50 | 归一化中心（50% 映射 50 分） |
| `GAME_CAP` | 21 | 每局封顶分（关键分判定用 `GAME_CAP−1=20`） |
| `MAX_RECENT_GAMES` | 10 | 默认取最近 N 场单打 |

> 扩展方向：`serve`/`receive` 当前与 `offense`/`defense` 同源，是占位实现了「发/接发」两个轴；若要真正区分，需在记分日志里补充回合性质（如发球直接得分率、抢攻率）再单独计算。
