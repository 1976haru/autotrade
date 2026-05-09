from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OrderAuditLog(Base):
    """주문 요청, 리스크 결정, 브로커 체결을 한 행에 기록하는 감사 로그."""

    __tablename__ = "order_audit_log"

    id:              Mapped[int]            = mapped_column(primary_key=True)
    created_at:      Mapped[datetime]       = mapped_column(DateTime, default=_utcnow, index=True)
    mode:            Mapped[str]            = mapped_column(String(32), index=True)
    requested_by_ai: Mapped[bool]           = mapped_column(Boolean, default=False)

    symbol:          Mapped[str]            = mapped_column(String(16), index=True)
    side:            Mapped[str]            = mapped_column(String(8))
    quantity:        Mapped[int]            = mapped_column(Integer)
    order_type:      Mapped[str]            = mapped_column(String(16))
    limit_price:     Mapped[int | None]     = mapped_column(Integer, nullable=True)
    latest_price:    Mapped[int]            = mapped_column(Integer)

    decision:        Mapped[str]            = mapped_column(String(32), index=True)
    reasons:         Mapped[list]           = mapped_column(JSON, default=list)

    # 134: 운영자/전략이 명시한 진입/청산 사유. 자유 문자열 — 'strategy_signal',
    # 'stop_loss', 'manual', 'ai_recommendation' 등. 0005 이전 row는 NULL.
    trade_reason:    Mapped[str | None]     = mapped_column(String(64), nullable=True)

    # #40: 주문 source 분류 (STRATEGY / AI / MANUAL / OPERATOR_OVERRIDE /
    # UNKNOWN). app.execution.order_executor.OrderSource enum과 1:1.
    # 0018 마이그레이션 이전 row + 호출자 미명시는 NULL — `OrderSource.UNKNOWN`
    # 으로 surface (호환). 운영자가 audit에서 어떤 출처의 주문이 가장 많은지
    # 분석할 수 있다.
    source:          Mapped[str | None]     = mapped_column(String(32), nullable=True, index=True)

    # 138: 어떤 전략이 만든 주문인지. LiveEngine이 자동 채움, 수동 주문은 NULL.
    # 0006 이전 row도 NULL. 향후 Strategy Scoreboard에 LIVE 결과 통합 가능.
    strategy:        Mapped[str | None]     = mapped_column(String(64), nullable=True, index=True)

    # 139: 신호 quality(136) 영구화 — 0-100 정수 두 축. 호출자가 quality를
    # 산출하지 않는 경로(수동 주문 등) + 0007 이전 row는 NULL. 사후 분석에서
    # '어떤 신뢰도/강도로 들어간 주문이 결국 어떤 결과를 냈나'를 추적 가능.
    signal_strength:   Mapped[int | None]   = mapped_column(Integer, nullable=True)
    signal_confidence: Mapped[int | None]   = mapped_column(Integer, nullable=True)

    # 140: idempotency 키. 호출자가 보낸 client_order_id를 그대로 저장 —
    # order_router가 같은 id가 이미 audit에 있으면 거부해 double-fire 사고를
    # 방지한다 (broker 단의 broker_order_id와는 별개 — 그건 broker가 발급).
    client_order_id: Mapped[str | None]    = mapped_column(String(64), nullable=True, index=True)

    # 152: VIRTUAL_AI_EXECUTION에서 AI가 만든 주문의 decision metadata.
    # {"confidence": 0..100, "reasons": [...], "rejected_by_guard": bool, ...}
    # NULL이면 AI 외 경로로 만들어진 주문. 0010 마이그레이션 이전 row도 NULL.
    ai_decision_meta: Mapped[dict | None]  = mapped_column(JSON, nullable=True)

    # 168: archival flag. cold storage로 분리된 row는 True. 기본 hot query
    # (routes_audit /api/audit/orders 등)는 archived=False만 본다. 0012
    # 마이그레이션 이전 row는 모두 False (default) — 자연스럽게 hot.
    # 별도 테이블 분리 대신 partial index로 hot 쿼리 최적화 — 컬럼 drift 위험
    # 없고 atomic.
    archived:        Mapped[bool]           = mapped_column(Boolean, default=False, index=True)

    executed:        Mapped[bool]           = mapped_column(Boolean, default=False)
    broker_order_id: Mapped[str | None]     = mapped_column(String(64), nullable=True)
    broker_status:   Mapped[str | None]     = mapped_column(String(32), nullable=True)
    filled_quantity: Mapped[int]            = mapped_column(Integer, default=0)
    avg_fill_price:  Mapped[int | None]     = mapped_column(Integer, nullable=True)
    message:         Mapped[str]            = mapped_column(String(255), default="")


class BacktestRun(Base):
    """단일 백테스트 실행에 대한 입력, 결과 지표, 체결 내역을 한 행에 저장."""

    __tablename__ = "backtest_run"

    id:             Mapped[int]      = mapped_column(primary_key=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)

    strategy:       Mapped[str]      = mapped_column(String(64), index=True)
    params:         Mapped[dict]     = mapped_column(JSON, default=dict)
    initial_cash:   Mapped[int]      = mapped_column(Integer)
    quantity:       Mapped[int]      = mapped_column(Integer)
    bars_processed: Mapped[int]      = mapped_column(Integer)

    final_cash:     Mapped[int]      = mapped_column(Integer)
    total_pnl:      Mapped[int]      = mapped_column(Integer)
    win_count:      Mapped[int]      = mapped_column(Integer, default=0)
    loss_count:     Mapped[int]      = mapped_column(Integer, default=0)
    max_drawdown:   Mapped[int]      = mapped_column(Integer, default=0)

    data_source:    Mapped[str]              = mapped_column(String(16), default="bars")
    data_symbol:    Mapped[str | None]       = mapped_column(String(16), nullable=True)
    data_start:     Mapped[datetime | None]  = mapped_column(DateTime, nullable=True)
    data_end:       Mapped[datetime | None]  = mapped_column(DateTime, nullable=True)
    data_interval:  Mapped[str | None]       = mapped_column(String(8), nullable=True)

    trades_json:    Mapped[list]     = mapped_column(JSON, default=list)


class PendingApproval(Base):
    """LIVE_MANUAL_APPROVAL/LIVE_AI_ASSIST 모드에서 사용자 승인을 기다리는 주문."""

    __tablename__ = "pending_approval"

    id:          Mapped[int]            = mapped_column(primary_key=True)
    created_at:  Mapped[datetime]       = mapped_column(DateTime, default=_utcnow, index=True)

    audit_id:    Mapped[int]            = mapped_column(
        Integer, ForeignKey("order_audit_log.id"), index=True
    )

    symbol:      Mapped[str]            = mapped_column(String(16), index=True)
    side:        Mapped[str]            = mapped_column(String(8))
    quantity:    Mapped[int]            = mapped_column(Integer)
    order_type:  Mapped[str]            = mapped_column(String(16))
    limit_price: Mapped[int | None]     = mapped_column(Integer, nullable=True)
    mode:        Mapped[str]            = mapped_column(String(32))

    status:      Mapped[str]            = mapped_column(String(16), default="PENDING", index=True)
    decided_at:  Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by:  Mapped[str | None]     = mapped_column(String(64), nullable=True)
    note:        Mapped[str | None]     = mapped_column(String(500), nullable=True)

    # 076: 070 재평가에서 거부된 시도 이력. 한 행이 여러 번 거부되면 그때마다
    # {at, decided_by, reasons} 항목이 append 된다. 075가 frontend session 메모리로
    # 같은 정보를 보여줬지만, 새로고침/운영자 인계 시 잃었다 — 이 필드로 영속화.
    attempts:    Mapped[list]           = mapped_column(JSON, default=list)


class AiAnalysisLog(Base):
    """AI 분석 요청과 응답을 한 행에 기록. 호출 실패도 audit 목적으로 남긴다."""

    __tablename__ = "ai_analysis_log"

    id:            Mapped[int]            = mapped_column(primary_key=True)
    created_at:    Mapped[datetime]       = mapped_column(DateTime, default=_utcnow, index=True)

    ticker:        Mapped[str]            = mapped_column(String(32), index=True)
    extra:         Mapped[str]            = mapped_column(String(512), default="")
    active_strats: Mapped[list]           = mapped_column(JSON, default=list)
    risk_params:   Mapped[dict]           = mapped_column(JSON, default=dict)

    # 123: 호출 시점의 운용모드 — 0004 마이그레이션에서 nullable로 추가. 이전
    # row는 NULL("기록 전")이고, FE의 ModeBadge가 mode=null이면 미렌더하므로
    # 자연스럽게 surface된다. 미래 mode별 cost 분포 분석을 가능하게 함.
    mode:          Mapped[str | None]     = mapped_column(String(32), nullable=True)

    text:          Mapped[str | None]     = mapped_column(Text, nullable=True)
    model:         Mapped[str | None]     = mapped_column(String(64), nullable=True)
    input_tokens:  Mapped[int]            = mapped_column(Integer, default=0)
    output_tokens: Mapped[int]            = mapped_column(Integer, default=0)
    score:         Mapped[dict | None]    = mapped_column(JSON, nullable=True)
    error:         Mapped[str | None]     = mapped_column(String(500), nullable=True)


class EmergencyStopEvent(Base):
    """긴급 정지 토글 이력.

    `RiskManager.emergency_stop` 자체는 in-memory 토글이라 재시작 시 초기화되지만,
    누가 언제 어떤 사유로 켜고 껐는지를 추적할 수 있어야 사고 분석이 가능하다.
    이 테이블은 토글이 발생할 때마다 한 행씩 추가된다 — 같은 상태로 다시 토글한
    경우(no-op)는 라우트 레이어에서 걸러서 노이즈를 줄인다.
    """

    __tablename__ = "emergency_stop_event"

    id:         Mapped[int]            = mapped_column(primary_key=True)
    created_at: Mapped[datetime]       = mapped_column(DateTime, default=_utcnow, index=True)
    enabled:    Mapped[bool]           = mapped_column(Boolean)
    decided_by: Mapped[str | None]     = mapped_column(String(64), nullable=True)
    note:       Mapped[str | None]     = mapped_column(String(500), nullable=True)
    # 153: 구조화된 사유 코드. enum app.risk.emergency_reasons.EmergencyStopReason.
    # NULL = 0011 이전 row 또는 미명시. 운영 단계에서는 명시 권장.
    reason_code: Mapped[str | None]    = mapped_column(String(32), nullable=True, index=True)
    # #37: 3단계 Kill Switch level (OFF/LEVEL_1/LEVEL_2/LEVEL_3). NULL =
    # 0017 이전 row 또는 legacy on/off 토글 — enabled=True + level=NULL은
    # 기존 의미와 동일한 LEVEL_1로 표시 (app/risk/emergency_stop.py
    # `normalize_legacy_level` 참고). 운영자가 단계 표시를 원하면 명시 권장.
    level:       Mapped[str | None]    = mapped_column(String(16), nullable=True)


class AgentDecisionLog(Base):
    """Agent Council 판단 기록 (185, MUST).

    각 Agent가 만든 structured 결정을 한 행에 기록한다. ChiefTradingAgent가
    종합 결정을 만들 때 다른 Agent들의 출력을 참고하므로, 사후 분석 시 의사
    결정 사슬을 재구성할 수 있어야 한다.

    실제 LLM 호출 없이 deterministic stub만 동작 — AI Key 없어도 운영 가능.
    """

    __tablename__ = "agent_decision_log"

    id:          Mapped[int]            = mapped_column(primary_key=True)
    created_at:  Mapped[datetime]       = mapped_column(DateTime, default=_utcnow, index=True)

    agent_name:  Mapped[str]            = mapped_column(String(64), index=True)
    symbol:      Mapped[str | None]     = mapped_column(String(16), nullable=True, index=True)
    mode:        Mapped[str]            = mapped_column(String(32), index=True)

    # 결정 카테고리: BUY / SELL / HOLD / APPROVE / REJECT / WARN / INFO 등.
    decision:    Mapped[str]            = mapped_column(String(32), index=True)
    confidence:  Mapped[int | None]     = mapped_column(Integer, nullable=True)

    reasons:     Mapped[list]           = mapped_column(JSON, default=list)
    # 자유 형식 metadata — agent별로 추가 구조화 (예: regime / score / size_pct).
    meta:        Mapped[dict | None]    = mapped_column(JSON, nullable=True)

    # 같은 의사결정 사슬을 묶는 키 — ChiefTradingAgent가 발급, 다른 agent들의
    # 결정에 같은 chain_id 부여해 사후 분석 시 한 번에 조회.
    chain_id:    Mapped[str | None]     = mapped_column(String(64), nullable=True, index=True)


class FuturesOrderAuditLog(Base):
    """선물 주문/청산 감사 로그 (169, MUST).

    OrderAuditLog는 주식 전용 스키마라(`symbol`, `latest_price` 등) 선물 주문은
    별도 테이블에 기록한다 — `contract`, `leverage`, `liquidation_price`,
    `forced_liquidation` 등 선물 고유 필드 보존.

    `MockFuturesBroker`가 db session을 받으면 매 place_order / 청산 / 강제청산
    호출 후 한 행을 추가한다. db 미주입 시 in-memory state만 — 기존 동작 유지.
    """

    __tablename__ = "futures_order_audit_log"

    id:            Mapped[int]            = mapped_column(primary_key=True)
    created_at:    Mapped[datetime]       = mapped_column(DateTime, default=_utcnow, index=True)

    mode:          Mapped[str]            = mapped_column(String(32), index=True)
    contract:      Mapped[str]            = mapped_column(String(32), index=True)
    side:          Mapped[str]            = mapped_column(String(8))
    quantity:      Mapped[int]            = mapped_column(Integer)
    order_type:    Mapped[str]            = mapped_column(String(16))
    limit_price:   Mapped[int | None]     = mapped_column(Integer, nullable=True)
    leverage:      Mapped[float]          = mapped_column(Float, default=1.0)

    decision:      Mapped[str]            = mapped_column(String(32), index=True)
    reasons:       Mapped[list]           = mapped_column(JSON, default=list)

    executed:        Mapped[bool]         = mapped_column(Boolean, default=False)
    broker_status:   Mapped[str | None]   = mapped_column(String(32), nullable=True)
    filled_quantity: Mapped[int]          = mapped_column(Integer, default=0)
    avg_fill_price:  Mapped[int | None]   = mapped_column(Integer, nullable=True)
    margin_delta:    Mapped[int]          = mapped_column(Integer, default=0)

    # 선물 고유 — 진입 시점의 강제청산 가격, 본 거래가 강제청산이었는지.
    liquidation_price:   Mapped[int | None]  = mapped_column(Integer, nullable=True)
    forced_liquidation:  Mapped[bool]        = mapped_column(Boolean, default=False, index=True)

    message:       Mapped[str]            = mapped_column(String(255), default="")


class VirtualOrder(Base):
    """Virtual Order Ledger (148, MUST).

    가상 주문의 라이프사이클 추적 — `OrderAuditLog`가 *결정*(REJECTED/APPROVED/
    NEEDS_APPROVAL)을 기록한다면, 본 테이블은 *주문 자체*의 상태 전이(NEW →
    ACCEPTED → PARTIALLY_FILLED → FILLED/CANCELLED/REJECTED/EXPIRED)를 기록한다.

    실거래 broker가 활성화되기 전까지는 모든 주문이 가상이고, 본 테이블이
    "이 시스템에서 만들어진 주문의 단일 진실"이다. 149/150에서 fill engine과
    position engine이 이 테이블을 읽고 갱신한다.

    상태 매트릭스:
    - NEW              : 시스템에 등록 직후 (RiskManager 평가 전)
    - ACCEPTED         : RiskManager APPROVED + (모드에 따라) 큐 통과
    - PARTIALLY_FILLED : 일부 체결, 잔량 있음
    - FILLED           : 전량 체결 종료
    - CANCELLED        : 운영자 또는 timeout으로 취소
    - REJECTED         : RiskManager 또는 broker 거부
    - EXPIRED          : 시간 한도 초과로 자동 만료 (e.g. day order)

    structured_reason: 상태 전이 사유의 코드 (자유 문자열). 운영 분석용.
    """

    __tablename__ = "virtual_order"

    id:                Mapped[int]            = mapped_column(primary_key=True)
    created_at:        Mapped[datetime]       = mapped_column(DateTime, default=_utcnow, index=True)
    updated_at:        Mapped[datetime]       = mapped_column(DateTime, default=_utcnow)

    # OrderAuditLog와의 cross-reference (옵션) — 같은 흐름에서 audit row를 만든
    # 주문이 virtual_order로 추적되는 경우 audit_id를 채워둔다.
    audit_id:          Mapped[int | None]     = mapped_column(
        Integer, ForeignKey("order_audit_log.id"), nullable=True, index=True
    )

    symbol:            Mapped[str]            = mapped_column(String(16), index=True)
    side:              Mapped[str]            = mapped_column(String(8))
    quantity:          Mapped[int]            = mapped_column(Integer)
    order_type:        Mapped[str]            = mapped_column(String(16))
    limit_price:       Mapped[int | None]     = mapped_column(Integer, nullable=True)
    requested_price:   Mapped[int | None]     = mapped_column(Integer, nullable=True)

    status:            Mapped[str]            = mapped_column(String(24), default="NEW", index=True)
    structured_reason: Mapped[str | None]     = mapped_column(String(64), nullable=True)

    strategy:          Mapped[str | None]     = mapped_column(String(64), nullable=True, index=True)
    mode:              Mapped[str]            = mapped_column(String(32), index=True)

    filled_quantity:   Mapped[int]            = mapped_column(Integer, default=0)
    avg_fill_price:    Mapped[int | None]     = mapped_column(Integer, nullable=True)
    filled_at:         Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 운영자/엔진이 만든 cancel/reject 사유 자유 텍스트.
    note:              Mapped[str | None]     = mapped_column(String(500), nullable=True)


class Watchlist(Base):
    """관심종목 그룹. 전략/Agent의 universe 후보군 — 주문 신호 아님 (#18).

    한 운영자가 여러 watchlist를 가질 수 있고 그중 하나를 active로 표시한다.
    실제 주문/리스크/PermissionGate 흐름과 분리 — RiskManager는 본 테이블을
    참조하지 않는다. Strategy/Agent가 향후 active watchlist를 universe 필터로
    사용하더라도 RiskManager → PermissionGate → OrderExecutor 단일 경로는
    그대로 유지된다 (CLAUDE.md 절대 원칙 5/7).
    """

    __tablename__ = "watchlist"

    id:         Mapped[int]      = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    name:        Mapped[str]            = mapped_column(String(64), index=True)
    description: Mapped[str | None]     = mapped_column(String(255), nullable=True)
    is_active:   Mapped[bool]           = mapped_column(Boolean, default=False, index=True)


class WatchlistItem(Base):
    """Watchlist에 속하는 종목 한 줄. (watchlist_id, symbol)이 유일."""

    __tablename__ = "watchlist_item"
    __table_args__ = (
        UniqueConstraint("watchlist_id", "symbol", name="uq_watchlist_item_symbol"),
    )

    id:           Mapped[int]      = mapped_column(primary_key=True)
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)

    watchlist_id: Mapped[int]      = mapped_column(
        Integer, ForeignKey("watchlist.id", ondelete="CASCADE"), index=True
    )
    symbol:  Mapped[str]            = mapped_column(String(16), index=True)
    name:    Mapped[str | None]     = mapped_column(String(64),  nullable=True)
    market:  Mapped[str | None]     = mapped_column(String(32),  nullable=True)
    sector:  Mapped[str | None]     = mapped_column(String(64),  nullable=True)
    note:    Mapped[str | None]     = mapped_column(String(255), nullable=True)


class ThemeSignal(Base):
    """테마/뉴스/트렌드 시그널 (#22).

    구글트렌드/뉴스/공시/수동 입력 데이터를 단일 행으로 보관한다. **주문 신호가
    아니라 후보 필터** — `used_for_order` 기본 False, ThemeFilter는 BUY/SELL/HOLD를
    반환하지 않고 candidate symbol 목록만 반환 (CLAUDE.md 절대 원칙).

    실 Google Trends API alpha 접근 권한이 없으면 `provider="mock"`로 채워진다.
    실제 외부 API 호출은 별도 옵트인 PR이며, 본 PR에서는 import / call 0건.
    """

    __tablename__ = "theme_signals"

    id:           Mapped[int]      = mapped_column(primary_key=True)
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)

    theme:        Mapped[str]            = mapped_column(String(64),  index=True)
    keywords:     Mapped[list]           = mapped_column(JSON, default=list)
    related_symbols: Mapped[list]        = mapped_column(JSON, default=list)

    # 0~100 정수. compute_theme_score로 산출. grade는 STRONG/WATCH/WEAK/IGNORE.
    score:        Mapped[int]            = mapped_column(Integer, default=0, index=True)
    grade:        Mapped[str]            = mapped_column(String(16), default="WEAK", index=True)
    confidence:   Mapped[int]            = mapped_column(Integer, default=0)

    source:       Mapped[str]            = mapped_column(String(32), index=True)
    # provider 식별자 — mock / google_trends_alpha / news_xxx / disclosure_dart / manual
    provider:     Mapped[str]            = mapped_column(String(32), index=True)

    summary:      Mapped[str | None]     = mapped_column(Text,    nullable=True)
    raw:          Mapped[dict | None]    = mapped_column(JSON,    nullable=True)

    # CLAUDE.md 절대 원칙 — 본 시그널은 주문에 직접 사용되지 않는다는 invariant.
    # True로 바뀌는 경로는 본 PR에서 만들지 않는다 (운영자 명시 옵트인 후 별도 PR).
    used_for_order: Mapped[bool]         = mapped_column(Boolean, default=False, index=True)


class ShadowTrade(Base):
    """LIVE_SHADOW mode signal-only trade record (#43 — Live Shadow finalization).

    실제 주문이 아닌 *추정* 기록이다. CLAUDE.md 절대 원칙 5/7 — `broker.place_order`는
    절대 호출되지 않으며, `actual_broker_order_sent`는 invariant False. RiskManager가
    LIVE_SHADOW 주문을 항상 REJECTED로 변환하기 때문에 OrderAuditLog 자체는 reject
    이력만 남는다 — 본 테이블은 그 위에 *would-have* 정보(다른 가드 통과 여부 +
    추정 체결가)를 영구화해 운영자가 “실제 시세 기준으로 주문 냈다면 어떻게 됐을까”를
    사후 분석할 수 있게 한다.

    `estimated_fill_price`는 latest_price proxy로 시작 (`estimation_method=
    "latest_price_proxy"`). orderbook depth / 호가 공백 / 부분체결 / 슬리피지는
    반영하지 않으므로 실제 체결 품질과 다를 수 있다 — UI/문서에서 명시한다.
    """

    __tablename__ = "shadow_trade"

    id:               Mapped[int]            = mapped_column(primary_key=True)
    created_at:       Mapped[datetime]       = mapped_column(DateTime, default=_utcnow, index=True)

    # OrderAuditLog와의 cross-reference. LIVE_SHADOW 경로는 항상 audit row를
    # 만들고, 본 row는 그 audit_id를 carry — 두 view를 함께 조회 가능.
    audit_id:         Mapped[int]            = mapped_column(
        Integer, ForeignKey("order_audit_log.id"), index=True
    )

    mode:             Mapped[str]            = mapped_column(String(32), index=True)
    requested_by_ai:  Mapped[bool]           = mapped_column(Boolean, default=False)

    symbol:           Mapped[str]            = mapped_column(String(16), index=True)
    side:             Mapped[str]            = mapped_column(String(8))
    quantity:         Mapped[int]            = mapped_column(Integer)
    order_type:       Mapped[str]            = mapped_column(String(16))
    limit_price:      Mapped[int | None]     = mapped_column(Integer, nullable=True)
    latest_price:     Mapped[int]            = mapped_column(Integer)

    # would-have decision — 실제 audit.decision은 항상 REJECTED. LIVE_SHADOW
    # 단일 reason만 있으면 다른 가드는 통과한 후보 (APPROVED), 그 외 reason이
    # 함께 누적되어 있으면 그 reason이 실제 거부 사유 (REJECTED). 운영자가
    # "실 시세에서 다른 가드까지 다 통과한 후보 비율"을 측정 가능.
    would_have_decision: Mapped[str]         = mapped_column(String(32), index=True)
    would_have_reasons:  Mapped[list]        = mapped_column(JSON, default=list)

    # CLAUDE.md 절대 원칙 5/7 — broker.place_order는 절대 호출되지 않는다.
    # 본 컬럼은 default False이며 모든 코드 경로에서 True로 set되지 않는다 —
    # 테스트(test_shadow_trade.py)로 invariant 강제.
    actual_broker_order_sent: Mapped[bool]   = mapped_column(Boolean, default=False)

    # 추정 체결가 — 실 체결과 다를 수 있다. estimation_method:
    #   "latest_price_proxy" — 본 PR의 기본. latest_price를 그대로 추정 체결가로
    #   사용, slippage_bps=0. 향후 orderbook 기반 추정 추가 시 method 문자열 갱신.
    estimated_fill_price:    Mapped[int]     = mapped_column(Integer)
    estimated_slippage_bps:  Mapped[float]   = mapped_column(Float, default=0.0)
    estimation_method:       Mapped[str]     = mapped_column(String(32), default="latest_price_proxy")
    confidence_note:         Mapped[str | None] = mapped_column(String(255), nullable=True)

    strategy:         Mapped[str | None]     = mapped_column(String(64), nullable=True, index=True)
    trade_reason:     Mapped[str | None]     = mapped_column(String(64), nullable=True)
    source:           Mapped[str | None]     = mapped_column(String(32), nullable=True, index=True)
    client_order_id:  Mapped[str | None]     = mapped_column(String(64), nullable=True, index=True)
    ai_decision_meta: Mapped[dict | None]    = mapped_column(JSON, nullable=True)


class AgentMemory(Base):
    """Agent Memory — 과거 운영 사례 / 손실 원인 / 전략 변경 이력 / 운영자 메모를
    *검색 가능*한 형태로 저장하는 read-only 학습 저장소.

    절대 원칙:
    - 본 테이블은 *주문 신호*가 아니다. 검색 결과는 BUY/SELL/HOLD 결정을 만들지
      않으며, RiskManager / PermissionGate / OrderExecutor 우회에 사용 X.
    - API key / Secret / 계좌번호 / 인증 토큰 / 개인정보를 *저장하지 않는다*
      (caller인 `app.agents.agent_memory.sanitize_text`가 sanitize 후 INSERT).
    - 본 row가 `audit_id` 같은 외부 row를 참조해도 *값을 복제*하지 않는다 —
      caller가 식별자(`source_id`)만 carry하고, 본 테이블은 요약 / lessons /
      next_action 같은 운영자 친화적 추출본만 보관.
    """

    __tablename__ = "agent_memory"

    id:           Mapped[int]      = mapped_column(primary_key=True)
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    updated_at:   Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow,
    )

    # 분류 (검색 필터 대상 — 모두 인덱스)
    memory_type:  Mapped[str]      = mapped_column(String(32), index=True)
    # daily_report / risk_incident / strategy_research / backtest_review /
    # agent_decision / operator_note / loss_post_mortem / lesson_learned

    source_kind:  Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # daily_report / risk_audit / strategy_research / agent_decision_log /
    # order_audit / backtest_run / operator (수동 입력)

    source_id:    Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 원본 row의 PK (참조용 — 값 복제 X)

    # 운영 컨텍스트 (선택, 검색 필터)
    strategy:     Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    symbol:       Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    mode:         Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    # 평가 (필터)
    severity:     Mapped[str]      = mapped_column(String(16), default="INFO", index=True)
    # INFO / WARN / HIGH / CRITICAL

    # 본문 — *민감정보 sanitize 후*에만 저장
    title:        Mapped[str]      = mapped_column(String(200))
    summary:      Mapped[str]      = mapped_column(Text)
    lessons:      Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action:  Mapped[str | None] = mapped_column(Text, nullable=True)

    # 검색용 태그 (JSON list[str])
    tags:         Mapped[list]     = mapped_column(JSON, default=list)

    # 추가 메타 (JSON; 자유 carry — 단, sanitize 통과해야 함)
    meta:         Mapped[dict]     = mapped_column(JSON, default=dict)

    # 작성자 (운영자 메모인 경우; agent 자동 생성은 NULL)
    author:       Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 운영자가 보관 처리하면 검색에서 default 제외 (수동 toggle)
    archived:     Mapped[bool]     = mapped_column(Boolean, default=False, index=True)


class MarketBar(Base):
    """업스트림에서 가져온 OHLCV 봉의 캐시. (symbol, interval, timestamp)가 유일."""

    __tablename__ = "market_bar"
    __table_args__ = (UniqueConstraint("symbol", "interval", "timestamp", name="uq_market_bar_key"),)

    id:         Mapped[int]      = mapped_column(primary_key=True)
    symbol:     Mapped[str]      = mapped_column(String(16), index=True)
    interval:   Mapped[str]      = mapped_column(String(8), index=True)
    timestamp:  Mapped[datetime] = mapped_column(DateTime, index=True)
    open:       Mapped[int]      = mapped_column(Integer)
    high:       Mapped[int]      = mapped_column(Integer)
    low:        Mapped[int]      = mapped_column(Integer)
    close:      Mapped[int]      = mapped_column(Integer)
    volume:     Mapped[int]      = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
