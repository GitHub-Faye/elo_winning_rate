"""Pydantic schemas for Elo API

请求/响应模型，用于 POST /api/v1/elo/record 端点。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── 请求模型 ──


class EloRecordRequest(BaseModel):
    """Elo 记录请求体"""
    event_id: int = Field(
        ...,
        description="赛事 ID。用于标识这场比赛隶属于哪场赛事，并写入比赛记录。",
    )
    battle_id: int = Field(
        ...,
        description="对阵 ID。标识该赛事内的一场具体对阵（两队之间的一场比赛），与 event_id 共同构成比赛记录的唯一性。",
    )
    source_order: int = Field(
        0,
        description="赛事内场序号。该对阵在赛事场次中的排序位置，仅用于展示/追溯，不参与 Elo 计算。",
    )
    score_a: int = Field(
        ..., ge=0,
        description="A 方（第一队）本场比赛得分。用于分差倍率 M_margin 计算与胜负判定（A>B 则 A 胜）。",
    )
    score_b: int = Field(
        ..., ge=0,
        description="B 方（第二队）本场比赛得分。用于分差倍率 M_margin 计算与胜负判定（B>A 则 B 胜）。",
    )
    team_a: list[str] = Field(
        ..., min_length=1, max_length=2,
        description="A 方选手身份证号（card_code）列表。1 个 = 单打，2 个 = 双打。选手以身份证号定位，未注册用户同样适用。",
    )
    team_b: list[str] = Field(
        ..., min_length=1, max_length=2,
        description="B 方选手身份证号（card_code）列表。1 个 = 单打，2 个 = 双打。双方人数必须一致。",
    )
    event_weight: float = Field(
        1.0, gt=0,
        description="赛事权重（须大于 0）。作为赛事权重倍率 M_weight 的一部分参与 Elo 计算，如热身赛/正式赛可设不同权重。",
    )
    played_at: Optional[datetime] = Field(
        None,
        description="比赛时间。缺省时使用服务器当前时间。",
    )

    @field_validator("score_a", "score_b")
    @classmethod
    def _check_score_non_negative(cls, v: int) -> int:
        """比分必须非负。"""
        if v < 0:
            raise ValueError("比分不能为负数")
        return v

    @field_validator("event_weight")
    @classmethod
    def _check_weight_positive(cls, v: float) -> float:
        """赛事权重必须大于 0。"""
        if v <= 0:
            raise ValueError("赛事权重必须大于 0")
        return v


# ── 响应模型 ──


class PlayerResult(BaseModel):
    """单名选手的 Elo 变化结果（含因子分解）"""
    card_code: str = Field(..., description="选手身份证号")
    delta: float = Field(..., description="本次 Elo 最终变化量（正=加分，负=减分）")
    rating_after: float = Field(..., description="更新后的 Elo 分")
    games_after: int = Field(..., description="更新后的总场次")
    wins_after: int = Field(..., description="更新后的胜场")
    losses_after: int = Field(..., description="更新后的负场")
    # 因子分解
    rating_before: float = Field(..., description="本次比赛前的 Elo 分")
    expected: float = Field(..., description="预期胜率 E（基于双方 Elo 差距计算）")
    k_factor: float = Field(..., description="K 值（按赛龄阶段：新秀/观察期/稳定期不同）")
    weight_multiplier: float = Field(..., description="赛事权重倍率 M_weight（= match_weight × event_weight）")
    margin_multiplier: float = Field(..., description="分差倍率 M_margin（比分差距越大越高）")
    base_delta: float = Field(..., description="封顶前的普通变化（= K×M_weight×M_margin×(S-E)）")
    clamped_delta: float = Field(..., description="封顶后的普通变化（限制在 ±delta_cap 内）")
    upset_bonus: float = Field(..., description="越级加分：新秀选手爆冷击败高分对手时的额外加分")
    upset_penalty: float = Field(..., description="被越级扣分：输给爆冷获胜的新秀时被扣的分数")
    opponent_card_code: str = Field(..., description="对手选手身份证号")
    opponent_partner_card_code: Optional[str] = Field(
        None, description="对手搭档身份证号（双打时有值，单打为 null）",
    )


class RecordData(BaseModel):
    """按方分组的 Elo 变化数据"""
    team_a: list[PlayerResult] = Field(..., description="A 方每位选手的 Elo 变化结果")
    team_b: list[PlayerResult] = Field(..., description="B 方每位选手的 Elo 变化结果")


class EloRecordResponse(BaseModel):
    """Elo 记录响应体"""
    success: bool = Field(..., description="请求是否成功")
    data: RecordData = Field(..., description="比赛记录后双方选手的 Elo 变化明细")


class ErrorResponse(BaseModel):
    detail: str


# ── 预测模型 ──


class PredictionRequest(BaseModel):
    """胜率预测请求体"""
    team_a: list[str] = Field(
        ..., min_length=1, max_length=2,
        description="A 方选手身份证号（card_code）列表。1 个 = 单打，2 个 = 双打。",
    )
    team_b: list[str] = Field(
        ..., min_length=1, max_length=2,
        description="B 方选手身份证号（card_code）列表。1 个 = 单打，2 个 = 双打。双方人数必须一致。",
    )

    @model_validator(mode="after")
    def _check_unique_ids(self) -> PredictionRequest:
        """同一方不能有重复选手，双方不能有重叠选手。"""
        if len(set(self.team_a)) != len(self.team_a):
            raise ValueError("Team A 中有重复选手")
        if len(set(self.team_b)) != len(self.team_b):
            raise ValueError("Team B 中有重复选手")
        if set(self.team_a) & set(self.team_b):
            raise ValueError("双方不能有相同的选手")
        return self


class PlayerPredictionResult(BaseModel):
    """单名选手的胜率预测结果"""
    card_code: str = Field(..., description="选手身份证号")
    rating: float = Field(..., description="选手当前 Elo 分")
    games: int = Field(..., description="选手总场次")
    wins: int = Field(..., description="选手胜场")
    losses: int = Field(..., description="选手负场")
    probability: float = Field(
        ..., description="最终预测胜率（0-1，含关系图调优后的 clamp，本选手所在方的胜率）",
    )
    elo_base_probability: float = Field(
        ..., description="Elo 基础胜率（仅基于双方 Elo 分差的原始预期胜率，未加调优）",
    )


class PlayerPredictionList(BaseModel):
    """一方队伍的预测结果列表"""
    players: list[PlayerPredictionResult] = Field(
        ..., description="本方每位选手的预测结果（单打 1 人，双打 2 人）",
    )


class PredictionData(BaseModel):
    """完整的预测响应数据"""
    match_type: str = Field(
        ..., description="比赛类型: singles=单打 / doubles=双打",
    )
    team_a: PlayerPredictionList = Field(..., description="A 方预测结果")
    team_b: PlayerPredictionList = Field(..., description="B 方预测结果")


class PredictionResponse(BaseModel):
    """胜率预测响应体"""
    success: bool = Field(..., description="请求是否成功")
    data: PredictionData = Field(..., description="双方选手的胜率预测结果")


# ── 交手记录模型 ──


class HeadToHeadRecord(BaseModel):
    """单条交手记录"""
    event_id: int = Field(..., description="赛事 ID")
    battle_id: int = Field(..., description="对阵 ID")
    team_size: int = Field(..., description="比赛形式：1=单打 2=双打")
    score_a: int = Field(..., description="选手 A 视角得分")
    score_b: int = Field(..., description="选手 B 视角得分")
    winner_card: str = Field(..., description="获胜方选手身份证号")
    played_at: Optional[datetime] = Field(None, description="比赛时间")


class HeadToHeadData(BaseModel):
    """交手记录汇总"""
    player_a_card: str = Field(..., description="选手 A 身份证号")
    player_b_card: str = Field(..., description="选手 B 身份证号")
    total_matches: int = Field(..., description="两人交手的总场次（含单打和双打）")
    a_wins: int = Field(..., description="选手 A 获胜场次")
    b_wins: int = Field(..., description="选手 B 获胜场次")
    records: list[HeadToHeadRecord] = Field(..., description="逐场交手明细")


class HeadToHeadResponse(BaseModel):
    """交手记录响应"""
    success: bool = Field(..., description="请求是否成功")
    data: HeadToHeadData = Field(..., description="两人交手记录汇总与明细")


# ── 积分查询模型 ──


class RatingQueryRequest(BaseModel):
    """按身份证号批量查询积分请求体"""
    card_codes: list[str] = Field(
        ..., min_length=1, max_length=50,
        description="选手身份证号（card_code）列表。服务端自动去重，最多 50 个。",
    )
    sport_type: Optional[str] = Field(
        None, description="运动品类（默认 badminton）",
    )
    province: Optional[str] = Field(
        None, description="筛选省份（如「山西省」）",
    )
    city: Optional[str] = Field(
        None, description="筛选城市（如「太原市」，优先级高于 province）",
    )

    @model_validator(mode="after")
    def _dedupe_card_codes(self) -> RatingQueryRequest:
        """去除重复身份证号（同一选手只查一次）。"""
        seen: set[str] = set()
        deduped: list[str] = []
        for code in self.card_codes:
            if code not in seen:
                seen.add(code)
                deduped.append(code)
        self.card_codes = deduped
        return self


class PlayerRatingResult(BaseModel):
    """单名选手的积分查询结果"""
    card_code: str = Field(..., description="选手身份证号")
    rating: Optional[float] = Field(None, description="当前 Elo 分（未建档选手为 null）")
    games: Optional[int] = Field(None, description="总比赛场次")
    wins: Optional[int] = Field(None, description="胜场")
    losses: Optional[int] = Field(None, description="负场")
    rank: Optional[str] = Field(
        None,
        description="段位（1段-9段；场次<2 为「定级中」；未建档为 null）",
    )
    is_provisional: bool = Field(False, description="是否处于定级期（总场次 < 2）")
    is_new: bool = Field(False, description="是否未建档（无 elo_player_rating 记录）")
    region_rank: Optional[int] = Field(
        None,
        description="在指定地区内的排名（仅 games>=2 的已定级选手参与排名；无地区筛选或未定级时为 null）",
    )
    region_total: Optional[int] = Field(
        None,
        description="指定地区内的已定级选手总数（仅 games>=2；无地区筛选时为 null）",
    )


class RatingQueryData(BaseModel):
    """批量积分查询数据"""
    sport_type: str = Field(..., description="运动品类（如 badminton）")
    province: Optional[str] = Field(None, description="筛选的省份（未筛选为 null）")
    city: Optional[str] = Field(None, description="筛选的城市（未筛选为 null）")
    results: list[PlayerRatingResult] = Field(..., description="各选手的积分查询结果")


class RatingQueryResponse(BaseModel):
    """积分查询响应体"""
    success: bool = Field(..., description="请求是否成功")
    data: RatingQueryData = Field(..., description="各选手的积分与段位查询结果")


# ── 赛事报名人积分模型 ──


class EventPlayerRating(PlayerRatingResult):
    """赛事报名人积分查询结果（在积分基础上附加选手姓名）"""
    name: Optional[str] = Field(None, description="报名时填写的姓名")


class EventRatingData(BaseModel):
    """赛事报名人积分查询数据"""
    event_id: int = Field(..., description="赛事 ID")
    sport_type: str = Field(..., description="运动品类（如 badminton）")
    results: list[EventPlayerRating] = Field(..., description="各报名人（含姓名）的积分与段位")


class EventRatingResponse(BaseModel):
    """赛事报名人积分查询响应体"""
    success: bool = Field(..., description="请求是否成功")
    data: EventRatingData = Field(..., description="赛事全部有效报名人的积分与段位")


# ── 六维雷达图模型 ──


class RadarMatchDetail(BaseModel):
    """单场六维雷达图明细"""
    score_team_id: int = Field(..., description="记分 ID（对应记分表主键）")
    battle_id: int = Field(..., description="对阵 ID")
    opponent: Optional[str] = Field(None, description="对手姓名（队伍名）")
    score: Optional[str] = Field(None, description="比分（本方:对手，字符串）")
    create_time: Optional[str] = Field(None, description="比赛日期（YYYY-MM-DD）")
    offense: float = Field(..., description="进攻得分（0-100，本方有发球权时得分率归一化）")
    defense: float = Field(..., description="防守得分（0-100，本方接对方发球时得分率归一化）")
    serve: float = Field(..., description="发球得分（0-100，本方发球回合得分率归一化）")
    receive: float = Field(..., description="接发得分（0-100，本方接发回合得分率归一化）")
    anti_pressure: float = Field(..., description="抗压得分（0-100，落后/逆转/关键分表现）")
    field: float = Field(..., description="场区得分（0-100，换边前后落差越小分越高）")
    consecutive_score: float = Field(..., description="平均连续得分")
    consecutive_lose: float = Field(..., description="平均连续失分")


class RadarProfile(BaseModel):
    """单名选手的六维雷达图"""
    name: Optional[str] = Field(None, description="选手姓名")
    card_code: str = Field(..., description="选手身份证号")
    matches: int = Field(..., description="参与计算的单打场次（即本次统计的最近 N 场）")
    total_singles: int = Field(..., description="历史全部单打场次")
    offense: Optional[float] = Field(None, description="进攻得分（发球权得分率归一化，0-100）")
    defense: Optional[float] = Field(None, description="防守得分（接发权得分率归一化，0-100）")
    serve: Optional[float] = Field(None, description="发球得分（0-100）")
    receive: Optional[float] = Field(None, description="接发得分（0-100）")
    anti_pressure: Optional[float] = Field(None, description="抗压得分（0-100）")
    field: Optional[float] = Field(None, description="场区得分（换边适应性，0-100）")
    consecutive_score: Optional[float] = Field(None, description="平均连续得分")
    consecutive_lose: Optional[float] = Field(None, description="平均连续失分")
    match_details: list[RadarMatchDetail] = Field(
        default_factory=list, description="参与统计的各单场明细",
    )


class RadarResponse(BaseModel):
    """六维雷达图响应体"""
    success: bool = Field(..., description="请求是否成功")
    data: RadarProfile = Field(..., description="选手六维雷达图（含各场明细）")


# ── 个人比赛记录模型 ──


class PlayerRecord(BaseModel):
    """单条个人比赛记录（以本人视角呈现）"""
    event_id: int = Field(..., description="赛事 ID")
    battle_id: int = Field(..., description="对阵 ID")
    source_order: int = Field(0, description="赛事内场序号")
    team_size: int = Field(..., description="比赛形式：1=单打 2=双打")
    is_winner: bool = Field(..., description="本场是否获胜")
    score_self: int = Field(..., description="本方得分")
    score_opponent: int = Field(..., description="对方得分")
    # Elo 变化
    rating_before: float = Field(..., description="赛前 Elo")
    rating_after: float = Field(..., description="赛后 Elo")
    delta: float = Field(..., description="Elo 变化量（正=加分，负=减分）")
    # 对手信息
    opponent_card_code: Optional[str] = Field(
        None, description="对手身份证号（双打时为第一个对手）",
    )
    opponent_partner_card_code: Optional[str] = Field(
        None, description="对手搭档身份证号（双打时有值，单打为 null）",
    )
    played_at: Optional[datetime] = Field(None, description="比赛时间")


class PlayerRecordSummary(BaseModel):
    """个人比赛记录汇总统计"""
    total_matches: int = Field(..., description="总场次（含单打和双打）")
    total_singles: int = Field(..., description="单打场次")
    total_doubles: int = Field(..., description="双打场次")
    wins: int = Field(..., description="胜场")
    losses: int = Field(..., description="负场")
    win_rate: Optional[float] = Field(None, description="胜率（0-1，无比赛为 null）")
    avg_score_self: Optional[float] = Field(None, description="场均本方得分")
    avg_score_opponent: Optional[float] = Field(None, description="场均对方得分")
    avg_delta: Optional[float] = Field(None, description="场均 Elo 变化")


class PlayerRecordsData(BaseModel):
    """个人比赛记录响应数据"""
    card_code: str = Field(..., description="选手身份证号")
    summary: PlayerRecordSummary = Field(..., description="汇总统计")
    records: list[PlayerRecord] = Field(..., description="逐场比赛明细（按时间倒序）")


class PlayerRecordsResponse(BaseModel):
    """个人比赛记录响应体"""
    success: bool = Field(..., description="请求是否成功")
    data: PlayerRecordsData = Field(..., description="选手比赛记录与汇总统计")


# ── 删除比赛记录模型 ──


class RollbackResult(BaseModel):
    """单名选手的回滚结果"""
    card_code: str = Field(..., description="选手身份证号")
    is_latest_match: bool = Field(
        ..., description="该场是否为该选手的最新一场（仅最新一场才回滚积分）",
    )
    rating_after: Optional[float] = Field(
        None, description="回滚后积分（非最新一场为 null，积分未变动）",
    )
    games_after: Optional[int] = Field(None, description="回滚后总场次")
    wins_after: Optional[int] = Field(None, description="回滚后胜场")
    losses_after: Optional[int] = Field(None, description="回滚后负场")
    deleted: bool = Field(..., description="本名选手的比赛记录是否已删除（比赛存在前提）")


class DeleteMatchResult(BaseModel):
    """单名选手的删除/回滚结果"""
    card_code: str = Field(..., description="选手身份证号")
    removed: bool = Field(..., description="本名选手的该场比赛记录是否被删除")
    rollback: Optional[RollbackResult] = Field(
        None, description="积分回滚结果（若该场为该选手最新一场则回滚）",
    )


class DeleteMatchData(BaseModel):
    """删除比赛记录响应数据"""
    event_id: int = Field(..., description="赛事 ID")
    battle_id: int = Field(..., description="对阵 ID")
    match_type: str = Field(..., description="比赛类型: singles=单打 / doubles=双打")
    deleted: bool = Field(..., description="该场比赛记录是否已删除（比赛存在则为 true）")
    total_records_deleted: int = Field(..., description="删除的 elo_match_record 条数")
    players_affected: list[DeleteMatchResult] = Field(
        ..., description="各受影响选手的删除与积分回滚明细",
    )


class DeleteMatchResponse(BaseModel):
    """删除比赛记录响应体"""
    success: bool = Field(..., description="请求是否成功")
    data: DeleteMatchData = Field(..., description="删除与积分回滚明细")
    notice: Optional[str] = Field(
        None, description="提示信息（如部分选手非最新一场，积分未回滚）",
    )


class MatchDeleteErrorResponse(BaseModel):
    """比赛不存在 / 无匹配记录的错误响应"""
    detail: str = Field(..., description="错误描述")
    event_id: Optional[int] = Field(None, description="赛事 ID")
    battle_id: Optional[int] = Field(None, description="对阵 ID")
    code: Optional[str] = Field(None, description="错误码，如 match_not_found")


# ── 比赛结果分析模型 ──


class MatchPlayerResult(BaseModel):
    """单名选手在本场比赛中的基础变化"""
    card_code: str = Field(..., description="选手身份证号")
    team_side: str = Field(..., description="所在方 A / B")
    is_winner: bool = Field(..., description="本场是否获胜")
    rating_before: float = Field(..., description="赛前 Elo")
    rating_after: float = Field(..., description="赛后 Elo")
    delta: float = Field(..., description="Elo 变化量（正=加分，负=减分）")
    score_self: int = Field(..., description="本方得分")
    score_opponent: int = Field(..., description="对方得分")
    rank_before: Optional[str] = Field(
        None, description="赛前段位（场次<2 为定级中）",
    )
    rank_after: Optional[str] = Field(
        None, description="赛后段位（场次<2 为定级中）",
    )


class MatchAnalysis(BaseModel):
    """指定选手的详细分析（含排名变化和段位差分）"""
    card_code: str = Field(..., description="选手身份证号")
    delta: float = Field(..., description="Elo 变化量")
    rating_before: float = Field(..., description="赛前 Elo")
    rating_after: float = Field(..., description="赛后 Elo")
    rank_before: Optional[str] = Field(None, description="赛前段位")
    rank_after: Optional[str] = Field(None, description="赛后段位")
    points_to_next_tier: Optional[float] = Field(
        None, description="距下一段位的分数差（null=已最高段或定级中）",
    )
    next_tier: Optional[str] = Field(
        None, description="下一段位名称（null=已最高段或定级中）",
    )
    region_rank_before: Optional[int] = Field(
        None, description="赛前地区排名（仅 games>=2 的已定级选手参与）",
    )
    region_rank_after: Optional[int] = Field(
        None, description="赛后地区排名",
    )
    region_total: Optional[int] = Field(
        None, description="地区已定级选手总数",
    )
    region_rank_change: Optional[int] = Field(
        None, description="排名变化（正=上升，负=下降，0=不变）",
    )


class MatchDetailData(BaseModel):
    """比赛结果分析数据"""
    battle_id: int = Field(..., description="对阵 ID")
    event_id: int = Field(..., description="赛事 ID")
    match_type: str = Field(
        ..., description="比赛类型: singles=单打 / doubles=双打",
    )
    score_a: int = Field(..., description="A 方得分")
    score_b: int = Field(..., description="B 方得分")
    played_at: Optional[datetime] = Field(None, description="比赛时间")
    players: list[MatchPlayerResult] = Field(
        ..., description="所有参赛选手的基础变化",
    )
    analysis: Optional[MatchAnalysis] = Field(
        None, description="指定选手的详细分析（仅传入 card_code 时返回）",
    )


class MatchDetailResponse(BaseModel):
    """比赛结果分析响应体"""
    success: bool = Field(..., description="请求是否成功")
    data: MatchDetailData = Field(..., description="比赛结果分析数据")