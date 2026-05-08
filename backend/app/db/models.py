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
