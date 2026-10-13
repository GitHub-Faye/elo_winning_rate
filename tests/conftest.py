"""pytest 全局配置

生产代码引入身份路由 `identity_service.resolve_to_primary` 后，绝大多数单元测试
（只 mock `db.execute`，不构造 Elo 账号数据）无需感知路由。为让既有测试保持通过，
这里把所有导入该函数的模块的 `resolve_to_primary` 自动替换为「恒等路由」（同键返回）：
  输入任何身份键 → 返回其自身（即不合并）。

需要真正测试身份路由行为的用例，可在测试体内用 `monkeypatch.setattr(...)` 覆盖成
想要的映射。
"""
from __future__ import annotations

import pytest


async def _identity_resolve(db, keys, sport_type="badminton"):
    """恒等路由：输入键原样返回（不合并任何身份）。"""
    return {k: k for k in keys}


# 这些模块 `from services.identity_service import resolve_to_primary`，按引用绑定，
# 需逐模块打补丁才算避开了该依赖。
_TARGETS = [
    "services.rating_service.resolve_to_primary",
    "services.event_rating_service.resolve_to_primary",
    "services.prediction_service.resolve_to_primary",
    "services.head_to_head_service.resolve_to_primary",
    "services.player_records_service.resolve_to_primary",
    "services.match_detail_service.resolve_to_primary",
    "services.radar_service.resolve_to_primary",
    "services.battle_card_service.resolve_to_primary",
]


@pytest.fixture(autouse=True)
def _identity_routing_noop(monkeypatch):
    """把各服务模块的身份路由替换为恒等路由（默认不合并）。"""
    for target in _TARGETS:
        monkeypatch.setattr(target, _identity_resolve, raising=False)