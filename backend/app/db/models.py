from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
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
