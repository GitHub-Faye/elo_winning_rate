# 01 — 数据库模型 + Alembic 迁移

**What to build:** 在 `core/models.py` 中定义 `elo_player_rating` 和 `elo_match_record` 两个 SQLModel 模型，生成 Alembic 迁移脚本，确保表结构正确创建到 yzmp_dev 数据库。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] 创建 `core/__init__.py`、`core/schemas.py`、`routers/__init__.py`、`services/__init__.py` 空模块文件
- [ ] 在 `core/models.py` 中定义 `EloPlayerRating` 和 `EloMatchRecord` 两个 SQLModel 模型
- [ ] 运行 `alembic revision --autogenerate` 生成迁移脚本
- [ ] 验证迁移脚本不含 DROP TABLE（仅 CREATE TABLE）
- [ ] 运行 `alembic upgrade head` 确认表创建成功