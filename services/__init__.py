"""Elo 服务层 — 处理比赛结果记录和 Elo 计算

核心职责：
  1. 接收比赛结果（比分、选手列表）
  2. 自动判断单打/双打，调用 elo_compute.py
  3. 写入 elo_match_record
  4. 更新 elo_player_rating

通过依赖注入 AsyncSession 实现可测试性。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import EloMatchRecord, EloPlayerRating
from core.schemas import EloRecordRequest, EloPlayerRecord, EloRecordResponse, PlayerInput
from elo_compute import (
    EloConfig,
    SideInput,
    TeamInput,
    MatchInput,
    compute_match_pair,
    compute_team_match,
    EloResult,
)


class EloService:
    """Elo 服务，通过 AsyncSession 注入实现可测试性。"""

    def __init__(self, db: AsyncSession, config: Optional[EloConfig] = None):
        self.db = db
        self.config = config or EloConfig()

    # ── 公共入口 ──

    async def record_match(self, req: EloRecordRequest) -> EloRecordResponse:
        """处理一场比赛，返回 Elo 变化记录。

        自动判断单打/双打：
        - len(players)=1 → 单打，调用 compute_match_pair()
        - len(players)=2 → 双打，调用 compute_team_match()
        """
        team_size = len(req.players_a)
        if team_size != len(req.players_b):
            raise ValueError("双方人数不匹配")

        if team_size == 1:
            records = await self._handle_singles(req)
        elif team_size == 2:
            records = await self._handle_doubles(req)
        else:
            raise ValueError(f"不支持的队伍人数: {team_size}")

        await self.db.flush()
        records.sort(key=lambda r: (r.team_side, r.user_id))
        return EloRecordResponse(
            battle_id=req.battle_id,
            records=records,
            team_size=team_size,
        )

    # ── 单打处理 ──

    async def _handle_singles(self, req: EloRecordRequest) -> list[EloPlayerRecord]:
        p_a = req.players_a[0]
        p_b = req.players_b[0]

        s_a, s_b = _scores(req.score_a, req.score_b)

        side_a = SideInput(
            rating=p_a.rating, games=p_a.games, team_size=1,
            actual_score=s_a, wins=p_a.wins, losses=p_a.losses,
        )
        side_b = SideInput(
            rating=p_b.rating, games=p_b.games, team_size=1,
            actual_score=s_b, wins=p_b.wins, losses=p_b.losses,
        )

        match = MatchInput(
            score_a=req.score_a,
            score_b=req.score_b,
            event_weight=req.event_weight,
        )

        result_a, result_b = compute_match_pair(side_a, side_b, match, self.config)

        played_at = req.played_at or datetime.now()

        rec_a = await self._save_and_upsert(
            req=req, player=p_a, team_side="A",
            is_winner=s_a > 0.5, team_size=1,
            result=result_a, opponent_user_id=p_b.user_id,
            opponent_partner_id=None,
            score_self=req.score_a, score_opponent=req.score_b,
            played_at=played_at,
        )
        rec_b = await self._save_and_upsert(
            req=req, player=p_b, team_side="B",
            is_winner=s_b > 0.5, team_size=1,
            result=result_b, opponent_user_id=p_a.user_id,
            opponent_partner_id=None,
            score_self=req.score_b, score_opponent=req.score_a,
            played_at=played_at,
        )

        return [rec_a, rec_b]

    # ── 双打处理 ──

    async def _handle_doubles(self, req: EloRecordRequest) -> list[EloPlayerRecord]:
        p_a1, p_a2 = req.players_a[0], req.players_a[1]
        p_b1, p_b2 = req.players_b[0], req.players_b[1]

        s_a, s_b = _scores(req.score_a, req.score_b)

        team_a = TeamInput(players=(
            SideInput(rating=p_a1.rating, games=p_a1.games, team_size=2,
                      actual_score=s_a, wins=p_a1.wins, losses=p_a1.losses),
            SideInput(rating=p_a2.rating, games=p_a2.games, team_size=2,
                      actual_score=s_a, wins=p_a2.wins, losses=p_a2.losses),
        ))
        team_b = TeamInput(players=(
            SideInput(rating=p_b1.rating, games=p_b1.games, team_size=2,
                      actual_score=s_b, wins=p_b1.wins, losses=p_b1.losses),
            SideInput(rating=p_b2.rating, games=p_b2.games, team_size=2,
                      actual_score=s_b, wins=p_b2.wins, losses=p_b2.losses),
        ))

        match = MatchInput(
            score_a=req.score_a,
            score_b=req.score_b,
            event_weight=req.event_weight,
        )

        results_a, results_b = compute_team_match(team_a, team_b, match, self.config)

        played_at = req.played_at or datetime.now()
        records: list[EloPlayerRecord] = []

        for player, result in zip(req.players_a, results_a):
            rec = await self._save_and_upsert(
                req=req, player=player, team_side="A",
                is_winner=s_a > 0.5, team_size=2,
                result=result, opponent_user_id=p_b1.user_id,
                opponent_partner_id=p_b2.user_id,
                score_self=req.score_a, score_opponent=req.score_b,
                played_at=played_at,
            )
            records.append(rec)

        for player, result in zip(req.players_b, results_b):
            rec = await self._save_and_upsert(
                req=req, player=player, team_side="B",
                is_winner=s_b > 0.5, team_size=2,
                result=result, opponent_user_id=p_a1.user_id,
                opponent_partner_id=p_a2.user_id,
                score_self=req.score_b, score_opponent=req.score_a,
                played_at=played_at,
            )
            records.append(rec)

        return records

    # ── 数据库持久化 ──

    async def _save_and_upsert(
        self,
        req: EloRecordRequest,
        player: PlayerInput,
        team_side: str,
        is_winner: bool,
        team_size: int,
        result: EloResult,
        opponent_user_id: int,
        opponent_partner_id: Optional[int],
        score_self: int,
        score_opponent: int,
        played_at: datetime,
    ) -> EloPlayerRecord:
        """写入一条 elo_match_record 并更新 elo_player_rating。"""
        rating_before = result.rating_after - result.delta
        bd = result.breakdown

        record = EloMatchRecord(
            event_id=req.event_id,
            battle_id=req.battle_id,
            source_order=req.source_order,
            user_id=player.user_id,
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
            opponent_user_id=opponent_user_id,
            opponent_partner_id=opponent_partner_id,
            score_self=score_self,
            score_opponent=score_opponent,
            played_at=played_at,
        )
        self.db.add(record)
        await self._upsert_rating(player, result)

        return EloPlayerRecord(
            user_id=player.user_id,
            team_side=team_side,
            is_winner=is_winner,
            rating_before=rating_before,
            delta=result.delta,
            rating_after=result.rating_after,
            expected=bd.expected,
            k_factor=bd.k_factor,
            weight_multiplier=bd.weight_multiplier,
            margin_multiplier=bd.margin_multiplier,
            base_delta=bd.base_delta,
            clamped_delta=bd.clamped_delta,
            upset_bonus=bd.upset_bonus,
            upset_penalty=bd.upset_penalty,
            opponent_user_id=opponent_user_id,
            opponent_partner_id=opponent_partner_id,
        )

    async def _upsert_rating(self, player: PlayerInput, result: EloResult) -> None:
        """更新或创建选手 Elo 评分。"""
        stmt = select(EloPlayerRating).where(
            EloPlayerRating.user_id == player.user_id,
            EloPlayerRating.sport_type == "badminton",
        )
        result_db = await self.db.execute(stmt)
        rating = result_db.scalar_one_or_none()

        delta_wins = result.wins_after - player.wins
        delta_losses = result.losses_after - player.losses

        if rating is None:
            new_rating = EloPlayerRating(
                user_id=player.user_id,
                sport_type="badminton",
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


# ── 辅助函数 ──


def _scores(score_a: int, score_b: int) -> tuple[float, float]:
    """将比分转换为实际胜负值 S（1.0=胜, 0.0=负, 0.5=平）。"""
    if score_a > score_b:
        return 1.0, 0.0
    elif score_a < score_b:
        return 0.0, 1.0
    else:
        return 0.5, 0.5