# 03 — 胜率预测服务（Win Rate Prediction API）

**What to build:** `POST /api/v1/prediction` 端点。接收双方选手 ID 列表，自动判断单打/双打，从 `elo_player_rating` 读 Elo 分，从 `elo_match_record` 构建关系图，调用 `winning_rate.py` 预测胜率。单打直接预测，双打两两配对后取平均。

**Blocked by:** 01 — 数据库模型和迁移

**Status:** ready-for-agent

- [ ] 定义预测请求/响应的 Pydantic 模型（`core/schemas.py`）
- [ ] 实现 `services/prediction_service.py` 中的 `build_relation_graph()` 函数（从 DB 查询 `elo_match_record` 构建）
- [ ] 实现单打预测逻辑（直接调用 `predict_win_rate()`）
- [ ] 实现双打预测逻辑（两两配对计算后取平均）
- [ ] 实现 `routers/prediction.py` 中的 `POST /api/v1/prediction` 路由
- [ ] 在 `main.py` 中注册路由
- [ ] Service 层可通过 mock `AsyncSession` 测试