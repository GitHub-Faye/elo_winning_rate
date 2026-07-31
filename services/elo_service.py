"""Elo 服务层 — 处理比赛结果记录和 Elo 计算

核心职责：
  1. 接收比赛结果（比分、选手 ID 列表）
  2. 查询 DB 获取选手当前 Elo 分（新选手用默认值）
  3. 自动判断单打/双打，调用 elo_compute.py
  4. 写入 elo_match_record
  5. 更新 elo_player_rating

通过依赖注入 AsyncSession 实现可测试性。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import EloMatchRecord, EloPlayerRating
from core.schemas import (
    EloRecordRequest,
    EloRecordResponse,
    PlayerResult,
    RecordData,
)
from elo_compute import (
    EloConfig,
    EloResult,
    MatchInput,
    SideInput,
    TeamInput,
    compute_match_pair,
    compute_team_match,
)

# 当前运动品类（后续可扩展为多品类）
CURRENT_SPORT = "badminton"


@dataclass
class _PlayerState:
    """选手的赛前状态（来自 DB 或默认值）"""
    card_code: str
    rating: float
    games: int
    wins: int
    losses: int


class EloService:
    """Elo 服务，通过 AsyncSession 注入实现可测试性。"""

    def __init__(self, db: AsyncSession, config: Optional[EloConfig] = None):
        self.db = db
        self.config = config or EloConfig()

    # ── 公共入口 ──

    async def record_match(self, req: EloRecordRequest) -> EloRecordResponse:
        """处理一场比赛，返回 Elo 变化记录。

        自动判断单打/双打：
        - len(team)=1 → 单打，调用 compute_match_pair()
        - len(team)=2 → 双打，调用 compute_team_match()
        """
        team_size = len(req.team_a)
        if team_size != len(req.team_b):
            raise ValueError("双方人数不匹配")
        if team_size not in (1, 2):
            raise ValueError(f"不支持的队伍人数: {team_size}")

        # 查询 DB 获取双方选手当前状态
        states_a = await self._load_player_states(req.team_a)
        states_b = await self._load_player_states(req.team_b)

        s_a, s_b = _scores(req.score_a, req.score_b)

        if team_size == 1:
            results_a, results_b = self._run_singles(states_a, states_b, req, s_a, s_b)
        else:
            results_a, results_b = self._run_doubles(states_a, states_b, req, s_a, s_b)

        # 持久化 + 构建响应
        played_at = req.played_at or datetime.now()
        team_a_results = await self._process_team(
            states_a, results_a, req, "A", s_a > 0.5, team_size,
            states_b[0].card_code,
            states_b[1].card_code if team_size == 2 else None,
            req.score_a, req.score_b, played_at,
        )
        team_b_results = await self._process_team(
            states_b, results_b, req, "B", s_b > 0.5, team_size,
            states_a[0].card_code,
            states_a[1].card_code if team_size == 2 else None,
            req.score_b, req.score_a, played_at,
        )

        await self.db.flush()
        await self.db.commit()

        return EloRecordResponse(
            success=True,
            data=RecordData(team_a=team_a_results, team_b=team_b_results),
        )

    # ── 从 DB 加载选手状态 ──

    async def _load_player_states(self, card_codes: list[str]) -> list[_PlayerState]:
        """批量查询 DB 获取选手当前 Elo 分，不存在的选手使用默认值。"""
        stmt = select(EloPlayerRating).where(
            EloPlayerRating.card_code.in_(card_codes),
            EloPlayerRating.sport_type == CURRENT_SPORT,
        )
        result_db = await self.db.execute(stmt)
        rows = result_db.scalars().all()
        rating_map = {r.card_code: r for r in rows}

        states: list[_PlayerState] = []
        for code in card_codes:
            r = rating_map.get(code)
            if r is None:
                states.append(_PlayerState(code, 1500.0, 0, 0, 0))
            else:
                states.append(_PlayerState(
                    code,
                    float(r.rating),
                    r.games,
                    r.wins,
                    r.losses,
                ))
        return states

    # ── 单打 ──

    def _run_singles(
        self, states_a: list[_PlayerState], states_b: list[_PlayerState],
        req: EloRecordRequest, s_a: float, s_b: float,
    ) -> tuple[list[EloResult], list[EloResult]]:
        """执行单打 Elo 计算，返回 (results_a, results_b)。"""
        s_a_side = SideInput(
            rating=states_a[0].rating, games=states_a[0].games, team_size=1,
            actual_score=s_a, wins=states_a[0].wins, losses=states_a[0].losses,
        )
        s_b_side = SideInput(
            rating=states_b[0].rating, games=states_b[0].games, team_size=1,
            actual_score=s_b, wins=states_b[0].wins, losses=states_b[0].losses,
        )
        match = MatchInput(
            score_a=req.score_a, score_b=req.score_b, event_weight=req.event_weight,
        )
        r_a, r_b = compute_match_pair(s_a_side, s_b_side, match, self.config)
        return [r_a], [r_b]

    # ── 双打 ──

    def _run_doubles(
        self, states_a: list[_PlayerState], states_b: list[_PlayerState],
        req: EloRecordRequest, s_a: float, s_b: float,
    ) -> tuple[list[EloResult], list[EloResult]]:
        """执行双打 Elo 计算，返回 (results_a, results_b)。"""
        team_a = TeamInput(players=tuple(
            SideInput(rating=st.rating, games=st.games, team_size=2,
                      actual_score=s_a, wins=st.wins, losses=st.losses)
            for st in states_a
        ))
        team_b = TeamInput(players=tuple(
            SideInput(rating=st.rating, games=st.games, team_size=2,
                      actual_score=s_b, wins=st.wins, losses=st.losses)
            for st in states_b
        ))
        match = MatchInput(
            score_a=req.score_a, score_b=req.score_b, event_weight=req.event_weight,
        )
        return compute_team_match(team_a, team_b, match, self.config)

    # ── 队伍处理（提取公共逻辑） ──

    async def _process_team(
        self,
        states: list[_PlayerState],
        results: list[EloResult],
        req: EloRecordRequest,
        team_side: str,
        is_winner: bool,
        team_size: int,
        opponent_card_code: str,
        opponent_partner_card_code: Optional[str],
        score_self: int,
        score_opponent: int,
        played_at: datetime,
    ) -> list[PlayerResult]:
        """对一方的所有队员：写 record + 更新 rating + 构建响应。"""
        player_results = []
        for state, result in zip(states, results):
            self._save_record(req, state.card_code, result, team_side,
                              team_size, is_winner,
                              opponent_card_code, opponent_partner_card_code,
                              score_self, score_opponent, played_at)
            await self._upsert_rating(state, result)
            player_results.append(self._build_result(
                state, result, team_side, is_winner,
                opponent_card_code, opponent_partner_card_code,
            ))
        return player_results

    # ── 数据库写入 ──

    def _save_record(
        self,
        req: EloRecordRequest,
        card_code: str,
        result: EloResult,
        team_side: str,
        team_size: int,
        is_winner: bool,
        opponent_card_code: str,
        opponent_partner_card_code: Optional[str],
        score_self: int,
        score_opponent: int,
        played_at: datetime,
    ) -> None:
        """写入一条 elo_match_record。"""
        rating_before = result.rating_after - result.delta
        bd = result.breakdown

        record = EloMatchRecord(
            event_id=req.event_id,
            battle_id=req.battle_id,
            source_order=req.source_order,
            card_code=card_code,
            team_side=team_side,
            team_size=team_size,
            is_winner=1 if is_winner else 0,
            rating_before=Decimal(str(rating_before)).quantize(Decimal("0.01")),
            delta=Decimal(str(result.delta)).quantize(Decimal("0.01")),
            rating_after=Decimal(str(result.rating_after)).quantize(Decimal("0.01")),
            expected=Decimal(str(bd.expected)).quantize(Decimal("0.0001")),
            k_factor=Decimal(str(bd.k_factor)).quantize(Decimal("0.01")),
            weight_multiplier=Decimal(str(bd.weight_multiplier)).quantize(Decimal("0.0001")),
            margin_multiplier=Decimal(str(bd.margin_multiplier)).quantize(Decimal("0.0001")),
            base_delta=Decimal(str(bd.base_delta)).quantize(Decimal("0.01")),
            clamped_delta=Decimal(str(bd.clamped_delta)).quantize(Decimal("0.01")),
            upset_bonus=Decimal(str(bd.upset_bonus)).quantize(Decimal("0.01")),
            upset_penalty=Decimal(str(bd.upset_penalty)).quantize(Decimal("0.01")),
            opponent_card_code=opponent_card_code,
            opponent_partner_card_code=opponent_partner_card_code,
            score_self=score_self,
            score_opponent=score_opponent,
            played_at=played_at,
        )
        self.db.add(record)

    async def _upsert_rating(self, player: _PlayerState, result: EloResult) -> None:
        """更新或创建选手 Elo 评分。"""
        stmt = select(EloPlayerRating).where(
            EloPlayerRating.card_code == player.card_code,
            EloPlayerRating.sport_type == CURRENT_SPORT,
        )
        result_db = await self.db.execute(stmt)
        rating = result_db.scalar_one_or_none()

        delta_wins = result.wins_after - player.wins
        delta_losses = result.losses_after - player.losses

        if rating is None:
            new_rating = EloPlayerRating(
                card_code=player.card_code,
                sport_type=CURRENT_SPORT,
                rating=Decimal(str(result.rating_after)).quantize(Decimal("0.01")),
                games=result.games_after,
                wins=result.wins_after,
                losses=result.losses_after,
                draws=0,
                highest_rating=Decimal(str(result.rating_after)).quantize(Decimal("0.01")),
                lowest_rating=Decimal(str(result.rating_after)).quantize(Decimal("0.01")),
            )
            self.db.add(new_rating)
        else:
            rating.rating = Decimal(str(result.rating_after)).quantize(Decimal("0.01"))
            rating.games = rating.games + 1
            rating.wins = rating.wins + delta_wins
            rating.losses = rating.losses + delta_losses

            if result.rating_after > float(rating.highest_rating):
                rating.highest_rating = Decimal(str(result.rating_after)).quantize(Decimal("0.01"))
            if result.rating_after < float(rating.lowest_rating):
                rating.lowest_rating = Decimal(str(result.rating_after)).quantize(Decimal("0.01"))

    # ── 响应构建 ──

    @staticmethod
    def _build_result(
        state: _PlayerState,
        result: EloResult,
        team_side: str,
        is_winner: bool,
        opponent_card_code: str,
        opponent_partner_card_code: Optional[str],
    ) -> PlayerResult:
        """构建响应中的 PlayerResult。"""
        rating_before = result.rating_after - result.delta
        bd = result.breakdown
        return PlayerResult(
            card_code=state.card_code,
            delta=result.delta,
            rating_after=result.rating_after,
            games_after=result.games_after,
            wins_after=result.wins_after,
            losses_after=result.losses_after,
            rating_before=rating_before,
            expected=bd.expected,
            k_factor=bd.k_factor,
            weight_multiplier=bd.weight_multiplier,
            margin_multiplier=bd.margin_multiplier,
            base_delta=bd.base_delta,
            clamped_delta=bd.clamped_delta,
            upset_bonus=bd.upset_bonus,
            upset_penalty=bd.upset_penalty,
            opponent_card_code=opponent_card_code,
            opponent_partner_card_code=opponent_partner_card_code,
        )


# ── 辅助函数 ──


def _scores(score_a: int, score_b: int) -> tuple[float, float]:
    """将比分转换为实际胜负值 S（1.0=胜, 0.0=负, 0.5=平）。"""
    if score_a > score_b:
        return 1.0, 0.0
    elif score_a < score_b:
        return 0.0, 1.0
    else:
        return 0.5, 0.5