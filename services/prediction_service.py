"""胜率预测服务 — 基于 Elo 分预测比赛胜率

核心职责：
  1. 接收双方选手 ID，自动判断单打/双打
  2. 查询 DB 获取选手当前 Elo 分
  3. 调用 winning_rate.py 预测胜率（仅基于 Elo 分）
  4. 双打时各队取 Elo 最高的选手代表全队预测

通过依赖注入 AsyncSession 实现可测试性。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import EloPlayerRating
from core.schemas import (
    PredictionData,
    PredictionRequest,
    PredictionResponse,
    PlayerPredictionList,
    PlayerPredictionResult,
)
from winning_rate import (
    PlayerRecord,
    PredictionResult,
    predict_win_rate,
)

CURRENT_SPORT = "badminton"


@dataclass
class PlayerRatingSnapshot:
    """选手评分的快照（从 DB 或默认值填充）。"""
    rating: float
    games: int
    wins: int
    losses: int


_DEFAULT_RATING = PlayerRatingSnapshot(1500.0, 0, 0, 0)


# ── 纯函数：共享的翻转逻辑 ──


@dataclass
class _SideView:
    """从一方视角查看的 PredictionResult 快照。"""
    probability: float
    elo_base_probability: float


def _view_for_side(result: PredictionResult, is_a: bool) -> _SideView:
    """从指定方视角提取预测结果各项值。"""
    if is_a:
        return _SideView(
            probability=result.probability_a,
            elo_base_probability=result.elo_base_probability,
        )
    return _SideView(
        probability=result.probability_b,
        elo_base_probability=1.0 - result.elo_base_probability,
    )


def _to_player_record(card_code: str, snapshot: PlayerRatingSnapshot) -> PlayerRecord:
    """从快照构建 PlayerRecord（用于 winning_rate.py 的纯函数接口）。"""
    return PlayerRecord(
        player_id=card_code, name="",
        rating=snapshot.rating, games=snapshot.games,
        wins=snapshot.wins, losses=snapshot.losses,
    )


def _build_result(
    card_code: str,
    snapshot: PlayerRatingSnapshot,
    result: PredictionResult,
    is_a: bool,
) -> PlayerPredictionResult:
    """从单条 PredictionResult 构建 PlayerPredictionResult。"""
    v = _view_for_side(result, is_a)
    return PlayerPredictionResult(
        card_code=card_code,
        rating=snapshot.rating,
        games=snapshot.games,
        wins=snapshot.wins,
        losses=snapshot.losses,
        probability=v.probability,
        elo_base_probability=v.elo_base_probability,
    )


# ── Service 类 ──


class PredictionService:
    """胜率预测服务，通过 AsyncSession 注入实现可测试性。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── 公共入口 ──

    async def predict(self, req: PredictionRequest) -> PredictionResponse:
        """预测一场比赛的胜率。"""
        team_size = len(req.team_a)
        if team_size != len(req.team_b):
            raise ValueError("双方人数不匹配")
        if team_size not in (1, 2):
            raise ValueError(f"不支持的队伍人数: {team_size}")

        all_cards = list(set(req.team_a + req.team_b))
        ratings = await self._load_player_ratings(all_cards)

        if team_size == 1:
            data = self._predict_singles(
                req.team_a[0], req.team_b[0], ratings,
            )
        else:
            data = self._predict_doubles(
                req.team_a, req.team_b, ratings,
            )

        return PredictionResponse(success=True, data=data)

    # ── DB 查询 ──

    async def _load_player_ratings(
        self, card_codes: list[str],
    ) -> dict[str, PlayerRatingSnapshot]:
        """批量查询选手 Elo 评分，不存在的选手使用默认值。"""
        stmt = select(EloPlayerRating).where(
            EloPlayerRating.card_code.in_(card_codes),
            EloPlayerRating.sport_type == CURRENT_SPORT,
        )
        result = await self.db.execute(stmt)
        rows = result.scalars().all()
        ratings: dict[str, PlayerRatingSnapshot] = {}
        for r in rows:
            ratings[r.card_code] = PlayerRatingSnapshot(
                rating=float(r.rating),
                games=r.games,
                wins=r.wins,
                losses=r.losses,
            )
        return ratings

    # ── 单打预测 ──

    def _predict_singles(
        self,
        card_a: str,
        card_b: str,
        ratings: dict[str, PlayerRatingSnapshot],
    ) -> PredictionData:
        """直接调用 predict_win_rate() 完成单打预测。"""
        ra = ratings.get(card_a, _DEFAULT_RATING)
        rb = ratings.get(card_b, _DEFAULT_RATING)

        pa = _to_player_record(card_a, ra)
        pb = _to_player_record(card_b, rb)
        result = predict_win_rate(pa, pb)

        return PredictionData(
            match_type="singles",
            team_a=PlayerPredictionList(players=[
                _build_result(card_a, ra, result, is_a=True),
            ]),
            team_b=PlayerPredictionList(players=[
                _build_result(card_b, rb, result, is_a=False),
            ]),
        )

    # ── 双打预测 ──

    def _predict_doubles(
        self,
        team_a_cards: list[str],
        team_b_cards: list[str],
        ratings: dict[str, PlayerRatingSnapshot],
    ) -> PredictionData:
        """双打预测：每队取 Elo 最高的选手代表全队预测。"""
        # 选出各队 Elo 最高的选手
        rep_a = max(team_a_cards, key=lambda c: ratings.get(c, _DEFAULT_RATING).rating)
        rep_b = max(team_b_cards, key=lambda c: ratings.get(c, _DEFAULT_RATING).rating)

        ra = ratings.get(rep_a, _DEFAULT_RATING)
        rb = ratings.get(rep_b, _DEFAULT_RATING)
        pa = _to_player_record(rep_a, ra)
        pb = _to_player_record(rep_b, rb)
        result = predict_win_rate(pa, pb)

        results_a = [
            _build_result(card, ratings.get(card, _DEFAULT_RATING), result, is_a=True)
            for card in team_a_cards
        ]
        results_b = [
            _build_result(card, ratings.get(card, _DEFAULT_RATING), result, is_a=False)
            for card in team_b_cards
        ]

        return PredictionData(
            match_type="doubles",
            team_a=PlayerPredictionList(players=results_a),
            team_b=PlayerPredictionList(players=results_b),
        )
