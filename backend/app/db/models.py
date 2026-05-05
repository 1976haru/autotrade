from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint
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


class AiAnalysisLog(Base):
    """AI 분석 요청과 응답을 한 행에 기록. 호출 실패도 audit 목적으로 남긴다."""

    __tablename__ = "ai_analysis_log"

    id:            Mapped[int]            = mapped_column(primary_key=True)
    created_at:    Mapped[datetime]       = mapped_column(DateTime, default=_utcnow, index=True)

    ticker:        Mapped[str]            = mapped_column(String(32), index=True)
    extra:         Mapped[str]            = mapped_column(String(512), default="")
    active_strats: Mapped[list]           = mapped_column(JSON, default=list)
    risk_params:   Mapped[dict]           = mapped_column(JSON, default=dict)

    text:          Mapped[str | None]     = mapped_column(Text, nullable=True)
    model:         Mapped[str | None]     = mapped_column(String(64), nullable=True)
    input_tokens:  Mapped[int]            = mapped_column(Integer, default=0)
    output_tokens: Mapped[int]            = mapped_column(Integer, default=0)
    score:         Mapped[dict | None]    = mapped_column(JSON, nullable=True)
    error:         Mapped[str | None]     = mapped_column(String(500), nullable=True)


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
