# 02 — 加减分服务（Elo Record API）

**What to build:** `POST /api/v1/elo/record` 端点。接收比赛结果（比分、双方选手 ID），自动判断单打/双打，调用 `elo_compute.py` 计算 Elo 变化，更新 `elo_player_rating`，写入 `elo_match_record`。Service 层通过依赖注入 `AsyncSession` 实现可测试性。

**Blocked by:** 01 — 数据库模型和迁移

**Status:** ready-for-agent

- [x] 定义 `core/schemas.py` 中的请求/响应 Pydantic 模型
- [x] 实现 `services/__init__.py`（含 `EloService` 类，接管 `AsyncSession`）
- [x] 实现 `routers/__init__.py` 中的 `POST /api/v1/elo/record` 路由
- [x] 单打场景：调用 `compute_match_pair()`，双方独立更新
- [x] 双打场景：调用 `compute_team_match()`，队伍平均分、各自 delta、平分 penalty
- [x] 新选手自动以默认值（rating=1500, games=0, wins=0, losses=0）初始化
- [x] 在 `main.py` 中注册路由
- [x] Service 层可通过 mock `AsyncSession` 测试