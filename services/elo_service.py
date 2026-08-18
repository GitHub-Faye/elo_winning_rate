"""Elo 服务层 — 处理比赛结果记录和 Elo 计算

核心职责：
  1. 接收 battle_id，自动从数据库获取所有比赛信息
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
from sqlalchemy import text

from core.models import EloMatchRecord, EloPlayerRating
from core.schemas import (
    EloRecordRequest,
    EloRecordResponse,
    PlayerResult,
    RecordData,
)
from core.score_parser import parse_item_score, parse_item_score_games
from elo_compute import (
    EloConfig,
    EloResult,
    FactorBreakdown,
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


@dataclass
class _MatchData:
    """从数据库查询到的比赛完整数据（内部使用）"""
    event_id: int
    battle_id: int
    source_order: int  # 设为 0
    score_a: int       # 从 item_score 解析（总分，用于记录存储）
    score_b: int       # 从 item_score 解析（总分，用于记录存储）
    team_a: list[str]  # card_code 列表
    team_b: list[str]  # card_code 列表
    event_weight: float
    played_at: Optional[datetime]
    games: list[tuple[int, int]]  # 逐局比分 [(a, b), ...]，用于逐局 Elo 计算


class EloService:
    """Elo 服务，通过 AsyncSession 注入实现可测试性。"""

    def __init__(self, db: AsyncSession, config: Optional[EloConfig] = None):
        self.db = db
        self.config = config or EloConfig()

    # ── 公共入口 ──

    async def record_match(self, req: EloRecordRequest) -> EloRecordResponse:
        """处理一场比赛，返回 Elo 变化记录。

        自动从数据库获取所有比赛信息，只需提供 battle_id。
        多局比赛逐局独立计算 Elo，取均值作为最终变化。
        胜负由「谁赢的局更多」决定（非总分）。
        """
        # 1. 从数据库获取比赛完整信息
        match_data = await self._fetch_match_data(req.battle_id, req.event_weight)

        # 2. 人数校验
        team_size = len(match_data.team_a)
        if team_size != len(match_data.team_b) or team_size not in (1, 2):
            raise ValueError(
                f"队伍人数不匹配或无效: A={len(match_data.team_a)}, B={len(match_data.team_b)}"
            )

        # 3. 查询 DB 获取双方选手当前状态
        states_a = await self._load_player_states(match_data.team_a)
        states_b = await self._load_player_states(match_data.team_b)

        # 4. 逐局 Elo 计算并取均值；同时统计各局胜负以判定总胜负
        num_games = len(match_data.games)
        if num_games == 0:
            raise ValueError(f"比赛无有效局数据: battle_id={match_data.battle_id}")

        # 累加器：各局结果累加
        acc_delta_a: list[float] = [0.0] * len(states_a)
        acc_delta_b: list[float] = [0.0] * len(states_b)
        acc_games_a: list[int] = [0] * len(states_a)
        acc_games_b: list[int] = [0] * len(states_b)
        acc_wins_a: list[int] = [0] * len(states_a)
        acc_wins_b: list[int] = [0] * len(states_b)
        acc_losses_a: list[int] = [0] * len(states_a)
        acc_losses_b: list[int] = [0] * len(states_b)
        acc_results_a: list[float] = [0.0] * len(states_a)
        acc_results_b: list[float] = [0.0] * len(states_b)

        # 用于 breakdown 的最近一局结果（取最后一局的因子分解）
        last_results_a: list[EloResult] | None = None
        last_results_b: list[EloResult] | None = None

        # 统计各局胜负（用于判定总胜负）
        a_games_won = 0  # A 赢的局数
        b_games_won = 0  # B 赢的局数

        for game_a, game_b in match_data.games:
            s_a, s_b = _scores(game_a, game_b)

            # 统计本局胜负
            if game_a > game_b:
                a_games_won += 1
            elif game_b > game_a:
                b_games_won += 1

            # 构造临时 match_data 用于本局计算
            game_match = _MatchData(
                event_id=match_data.event_id,
                battle_id=match_data.battle_id,
                source_order=match_data.source_order,
                score_a=game_a,
                score_b=game_b,
                team_a=match_data.team_a,
                team_b=match_data.team_b,
                event_weight=match_data.event_weight,
                played_at=match_data.played_at,
                games=[(game_a, game_b)],
            )

            if team_size == 1:
                results_a, results_b = self._run_singles(states_a, states_b, game_match, s_a, s_b)
            else:
                results_a, results_b = self._run_doubles(states_a, states_b, game_match, s_a, s_b)

            # 累加各局结果
            for i, r in enumerate(results_a):
                acc_delta_a[i] += r.delta
                acc_games_a[i] += r.games_after - states_a[i].games
                acc_wins_a[i] += r.wins_after - states_a[i].wins
                acc_losses_a[i] += r.losses_after - states_a[i].losses
                acc_results_a[i] += r.rating_after
            for i, r in enumerate(results_b):
                acc_delta_b[i] += r.delta
                acc_games_b[i] += r.games_after - states_b[i].games
                acc_wins_b[i] += r.wins_after - states_b[i].wins
                acc_losses_b[i] += r.losses_after - states_b[i].losses
                acc_results_b[i] += r.rating_after

            last_results_a = results_a
            last_results_b = results_b

        # 5. 构造均值结果（仅 Elo delta 取均值，胜负按局数判定）
        avg_results_a = []
        for i, state in enumerate(states_a):
            avg_delta = acc_delta_a[i] / num_games
            avg_rating = acc_results_a[i] / num_games
            avg_results_a.append(EloResult(
                delta=avg_delta,
                rating_after=avg_rating,
                games_after=state.games + round(acc_games_a[i] / num_games),
                wins_after=state.wins + round(acc_wins_a[i] / num_games),
                losses_after=state.losses + round(acc_losses_a[i] / num_games),
                breakdown=last_results_a[i].breakdown,
            ))

        avg_results_b = []
        for i, state in enumerate(states_b):
            avg_delta = acc_delta_b[i] / num_games
            avg_rating = acc_results_b[i] / num_games
            avg_results_b.append(EloResult(
                delta=avg_delta,
                rating_after=avg_rating,
                games_after=state.games + round(acc_games_b[i] / num_games),
                wins_after=state.wins + round(acc_wins_b[i] / num_games),
                losses_after=state.losses + round(acc_losses_b[i] / num_games),
                breakdown=last_results_b[i].breakdown,
            ))

        # 6. 判定总胜负（谁赢的局更多 = 胜者）
        a_is_winner = a_games_won > b_games_won
        b_is_winner = b_games_won > a_games_won

        # 7. 持久化 + 构建响应（score 使用局数比分，如 2:1）
        played_at = match_data.played_at or datetime.now()
        team_a_results = await self._process_team(
            states_a, avg_results_a, match_data, "A", a_is_winner, team_size,
            states_b[0].card_code,
            states_b[1].card_code if team_size == 2 else None,
            a_games_won, b_games_won, played_at,
        )
        team_b_results = await self._process_team(
            states_b, avg_results_b, match_data, "B", b_is_winner, team_size,
            states_a[0].card_code,
            states_a[1].card_code if team_size == 2 else None,
            b_games_won, a_games_won, played_at,
        )

        await self.db.flush()
        await self.db.commit()

        return EloRecordResponse(
            success=True,
            data=RecordData(team_a=team_a_results, team_b=team_b_results),
        )

    async def _fetch_match_data(
        self,
        battle_id: int,
        event_weight: float = 1.0,
    ) -> _MatchData:
        """从数据库获取比赛完整信息。"""
        from services.battle_card_service import get_card_codes_by_battle_id

        # 1. 获取选手信息
        card_info = await get_card_codes_by_battle_id(self.db, battle_id)
        if not card_info:
            raise ValueError(f"比赛不存在: battle_id={battle_id}")
        if not card_info["is_valid"]:
            raise ValueError(
                f"比赛选手信息不完整: battle_id={battle_id}, "
                f"missing_count={card_info['missing_count']}"
            )

        # 2. 获取 item_score
        stmt = text("""
            SELECT item_score, battle_time
            FROM motion_event_layout_stage_battle
            WHERE battle_id = :battle_id AND is_del = 0
        """)
        result = await self.db.execute(stmt, {"battle_id": battle_id})
        row = result.fetchone()
        if not row:
            raise ValueError(f"比赛不存在: battle_id={battle_id}")

        row = dict(row._mapping)
        item_score = row.get("item_score")
        battle_time = row.get("battle_time")

        # 3. 解析 item_score
        if item_score:
            score_a, score_b = parse_item_score(item_score)
            game_list = parse_item_score_games(item_score)
        else:
            # 回退到 player_one_score/two_score（局数）
            score_a = card_info["score_a"]
            score_b = card_info["score_b"]
            game_list = [(score_a, score_b)]

        # 4. 构造 _MatchData
        return _MatchData(
            event_id=card_info["event_id"],
            battle_id=battle_id,
            source_order=0,
            score_a=score_a,
            score_b=score_b,
            team_a=card_info["team_a"],
            team_b=card_info["team_b"],
            event_weight=event_weight,
            played_at=battle_time,
            games=game_list,
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

    # ── 归属地区查询 ──

    async def _fetch_region(self, card_code: str) -> tuple[Optional[str], Optional[str]]:
        """从 motion_user 表查询选手的归属省份和城市。"""
        result = await self.db.execute(text(
            "SELECT address_province, address_city "
            "FROM motion_user WHERE BINARY id_code = :card_code LIMIT 1"
        ), {"card_code": card_code})
        row = result.first()
        if row and (row[0] or row[1]):
            return row[0], row[1]
        return None, None

    # ── 单打 ──

    def _run_singles(
        self, states_a: list[_PlayerState], states_b: list[_PlayerState],
        match_data: _MatchData, s_a: float, s_b: float,
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
            score_a=match_data.score_a, score_b=match_data.score_b, event_weight=match_data.event_weight,
        )
        r_a, r_b = compute_match_pair(s_a_side, s_b_side, match, self.config)
        return [r_a], [r_b]

    # ── 双打 ──

    def _run_doubles(
        self, states_a: list[_PlayerState], states_b: list[_PlayerState],
        match_data: _MatchData, s_a: float, s_b: float,
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
            score_a=match_data.score_a, score_b=match_data.score_b, event_weight=match_data.event_weight,
        )
        return compute_team_match(team_a, team_b, match, self.config)

    # ── 队伍处理（提取公共逻辑） ──

    async def _process_team(
        self,
        states: list[_PlayerState],
        results: list[EloResult],
        match_data: _MatchData,
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
            self._save_record(match_data, state.card_code, result, team_side,
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
        match_data: _MatchData,
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
            event_id=match_data.event_id,
            battle_id=match_data.battle_id,
            source_order=match_data.source_order,
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
            # 查询 motion_user 获取归属地区（仅在创建新选手时）
            province, city = await self._fetch_region(player.card_code)
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
                province=province,
                city=city,
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