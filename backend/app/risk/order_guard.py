"""Order Guard — duplicate / cooldown / pending pre-trade guard (#38).

봇 오류 / 네트워크 재시도 / AI 반복 판단으로 같은 주문이 여러 번 broker에
도달하는 사고를 차단한다. RiskManager의 기존 가드(notional / position /
loss limit / kill switch)와 *별개의 차원*인 "주문 흐름 자체의 중복 / 빈도"
를 검사하는 pre-trade guard.

설계 결정:
- **fingerprint**: symbol + side + quantity + order_type + price_bucket +
  strategy + mode + agent_chain_id를 stable hash로 묶어 식별. 가격은 작은
  noise로 fingerprint가 달라지지 않게 `price_bucket_pct`(기본 0.5%) 단위
  rounding.
- **idempotency replay vs duplicate**: 호출자가 보낸 `client_order_id`가 같으면
  RETRY_REPLAY (네트워크 재시도 — 안전). 다른 client_order_id로 같은
  fingerprint가 들어오면 DUPLICATE (실제 중복 주문 — 차단).
- **cooldown**: symbol / (strategy, symbol) / post-exit / AI extra 각각 별도
  윈도우. 모두 0이면 검사 비활성 (backwards compat).
- **pending guard**: 같은 symbol + side로 NEEDS_APPROVAL 또는 PendingApproval
  PENDING이 이미 있으면 신규 같은 방향 주문 차단.

본 모듈은 *주문을 만들지 않는다* — broker / OrderExecutor / route_order 어떤
함수도 호출하지 않는다. read-only DB 조회 + fingerprint 산출만. 결과를 받은
호출자(route_order)가 audit row 작성 + REJECTED 분기를 처리한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers.base import OrderRequest, OrderSide, OrderType
from app.db.models import OrderAuditLog, PendingApproval


class GuardDecision(StrEnum):
    """OrderGuard.check 결과."""
    ALLOW           = "ALLOW"
    DUPLICATE       = "DUPLICATE"        # 같은 fingerprint, 다른 idempotency_key
    RETRY_REPLAY    = "RETRY_REPLAY"     # 같은 idempotency_key — 네트워크 재시도
    COOLDOWN        = "COOLDOWN"         # 쿨타임 윈도우 안
    PENDING_BLOCKED = "PENDING_BLOCKED"  # 같은 방향 미체결 / 승인대기 존재


@dataclass(frozen=True)
class OrderGuardConfig:
    """OrderGuard 정책 묶음. 모든 필드 default = 검사 비활성 (기존 호환).

    필드:
    - `duplicate_window_seconds`: 같은 fingerprint가 N초 안에 또 들어오면
      DUPLICATE. 0 = 비활성.
    - `symbol_cooldown_seconds`: 같은 symbol에 마지막 주문 후 N초 cooldown.
    - `strategy_symbol_cooldown_seconds`: (strategy, symbol)별 cooldown.
    - `post_exit_cooldown_seconds`: SELL(=청산)이 발생한 직후 같은 symbol
      재진입 cooldown.
    - `ai_extra_cooldown_seconds`: AI 경로에 추가로 적용되는 cooldown
      (cooldown 위에 누적).
    - `block_when_pending_same_side`: 같은 symbol + side로 NEEDS_APPROVAL /
      PendingApproval PENDING 있으면 신규 주문 차단.
    - `price_bucket_pct`: fingerprint 가격 round 단위 (예: 0.5%).
    """
    duplicate_window_seconds:           int   = 0
    symbol_cooldown_seconds:            int   = 0
    strategy_symbol_cooldown_seconds:   int   = 0
    post_exit_cooldown_seconds:         int   = 0
    ai_extra_cooldown_seconds:          int   = 0
    block_when_pending_same_side:       bool  = False
    price_bucket_pct:                   float = 0.5


@dataclass(frozen=True)
class OrderGuardResult:
    """OrderGuard.check 결과 묶음.

    `decision == ALLOW`이면 reasons는 비어 있다. 그 외 분기는 reasons에 사유
    1건 이상 + (해당되면) 추가 metadata.
    """
    decision:       GuardDecision
    fingerprint:    str
    reasons:        list[str] = field(default_factory=list)
    audit_replay_id: int | None = None   # RETRY_REPLAY 시 기존 audit row id
    cooldown_remaining_seconds: int | None = None
    blocked_by:     str | None = None    # symbol_cooldown / strategy_symbol_cooldown / post_exit_cooldown / ai_cooldown / pending / duplicate

    @property
    def allowed(self) -> bool:
        return self.decision == GuardDecision.ALLOW

    def to_dict(self) -> dict:
        return {
            "decision":     self.decision.value,
            "fingerprint":  self.fingerprint,
            "reasons":      list(self.reasons),
            "audit_replay_id":              self.audit_replay_id,
            "cooldown_remaining_seconds":   self.cooldown_remaining_seconds,
            "blocked_by":   self.blocked_by,
        }


# ---------- fingerprint ----------


def _bucket_price(price: int | None, bucket_pct: float) -> int | None:
    """`bucket_pct`(%) 단위로 round. 가격이 같은 bucket이면 같은 값 반환.

    None은 그대로 None (market order).
    """
    if price is None:
        return None
    if bucket_pct <= 0:
        return int(price)
    # 가격을 (bucket_pct/100 × price) 절댓값으로 내림 — 가격 비례 bucket.
    # 매우 작은 가격(<200)은 절대값 1 단위로 처리해 분모 0 회피.
    bucket = max(1, int(round(price * bucket_pct / 100.0)))
    return (price // bucket) * bucket


def build_order_fingerprint(
    order: OrderRequest,
    *,
    mode:            str | None = None,
    price_bucket_pct: float = 0.5,
    agent_chain_id:  str | None = None,
) -> str:
    """주문의 stable fingerprint string.

    동일 주문(중복 의심)을 식별하기 위한 키. **secret을 포함하지 않는다** —
    계좌번호 / API key / 운영자 식별자는 입력으로 받지 않는다.

    구성:
    - symbol / side / quantity / order_type
    - market 주문은 price 미포함, limit는 `_bucket_price`로 round.
    - strategy (None이면 빈 문자열)
    - mode
    - agent_chain_id (Agent Council chain 식별 — 같은 chain의 같은 결정은
      같은 fingerprint).

    반환: SHA-256 hex (12자 prefix). 충분히 unique하면서 audit row에 carry
    가능한 짧은 길이.
    """
    side = order.side.value if isinstance(order.side, OrderSide) else str(order.side)
    otype = order.order_type.value if isinstance(order.order_type, OrderType) else str(order.order_type)
    bucketed_price = (
        _bucket_price(order.limit_price, price_bucket_pct)
        if otype != OrderType.MARKET.value else None
    )
    parts = [
        f"sym={order.symbol}",
        f"side={side}",
        f"qty={order.quantity}",
        f"type={otype}",
        f"price={bucketed_price if bucketed_price is not None else 'MKT'}",
        f"strat={order.strategy or ''}",
        f"mode={mode or ''}",
        f"chain={agent_chain_id or ''}",
    ]
    payload = "|".join(parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"of_{digest}"


# ---------- helpers (read-only DB) ----------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def find_audit_by_client_order_id(
    db: Session, *, client_order_id: str | None,
) -> OrderAuditLog | None:
    """idempotency replay 검사용 — 같은 client_order_id로 이미 처리된 row."""
    if not client_order_id:
        return None
    return db.execute(
        select(OrderAuditLog).where(OrderAuditLog.client_order_id == client_order_id)
        .order_by(OrderAuditLog.id).limit(1)
    ).scalar_one_or_none()


def _last_executed_for_symbol(
    db: Session, *, symbol: str, side: str | None = None,
) -> OrderAuditLog | None:
    """가장 최근 *executed* audit row (= broker로 실제 보낸 주문). cooldown용."""
    stmt = select(OrderAuditLog).where(
        OrderAuditLog.symbol == symbol,
        OrderAuditLog.executed.is_(True),
    )
    if side:
        stmt = stmt.where(OrderAuditLog.side == side)
    return db.execute(
        stmt.order_by(OrderAuditLog.id.desc()).limit(1)
    ).scalar_one_or_none()


def _last_executed_for_strategy_symbol(
    db: Session, *, strategy: str | None, symbol: str,
) -> OrderAuditLog | None:
    if strategy is None:
        return None
    return db.execute(
        select(OrderAuditLog)
        .where(
            OrderAuditLog.symbol == symbol,
            OrderAuditLog.strategy == strategy,
            OrderAuditLog.executed.is_(True),
        )
        .order_by(OrderAuditLog.id.desc()).limit(1)
    ).scalar_one_or_none()


def _has_pending_same_side(db: Session, *, symbol: str, side: str) -> bool:
    """같은 symbol + side로 PendingApproval(PENDING) 또는 OrderAuditLog
    NEEDS_APPROVAL이 있는지."""
    pending = db.execute(
        select(PendingApproval.id).where(
            PendingApproval.symbol == symbol,
            PendingApproval.side == side,
            PendingApproval.status == "PENDING",
        ).limit(1)
    ).first()
    if pending is not None:
        return True
    audit = db.execute(
        select(OrderAuditLog.id).where(
            OrderAuditLog.symbol == symbol,
            OrderAuditLog.side == side,
            OrderAuditLog.decision == "NEEDS_APPROVAL",
        ).limit(1)
    ).first()
    return audit is not None


def _find_recent_same_fingerprint(
    db: Session, *, fingerprint: str, window_seconds: int,
) -> OrderAuditLog | None:
    """`window_seconds` 안에 같은 fingerprint를 가진 audit row.

    audit row의 `note` / `message`는 fingerprint를 별도 컬럼으로 영구화하지
    않는다 (스키마 변경 회피). 대신 본 함수는 windows 안의 row를 가져와
    runtime에 fingerprint를 재계산해 매칭. 운영 환경에서 row 수가 많으면 별도
    인덱스 / 컬럼 도입을 backlog로 검토.
    """
    if window_seconds <= 0:
        return None
    cutoff = _now_utc() - timedelta(seconds=window_seconds)
    rows = db.execute(
        select(OrderAuditLog)
        .where(OrderAuditLog.created_at >= cutoff)
        .order_by(OrderAuditLog.id.desc())
        .limit(50)
    ).scalars().all()
    for r in rows:
        existing = build_order_fingerprint(
            OrderRequest(
                symbol=r.symbol,
                side=OrderSide(r.side),
                quantity=r.quantity,
                order_type=OrderType(r.order_type),
                limit_price=r.limit_price,
                strategy=r.strategy,
            ),
            mode=r.mode,
        )
        if existing == fingerprint:
            return r
    return None


# ---------- guard ----------


class OrderGuard:
    """주문 흐름 차원의 pre-trade guard.

    호출 위치: `route_order`의 첫 단계 (RiskManager / PermissionGate / broker
    호출 *전*). 결과가 ALLOW가 아니면 호출자가 audit row 작성 + REJECTED
    분기.

    설계 의도:
    - **단일 책임**: 본 클래스는 *흐름 차원의 가드*만 담당. 한도 / 자본 /
      손실 / regime은 RiskManager가 처리.
    - **read-only**: DB write 0건 — broker / OrderExecutor / route_order 어떤
      함수도 호출하지 않는다.
    - **opt-in**: 모든 cooldown / window는 default 0 = 비활성. 운영자가
      `RiskPolicy`에서 명시 활성화.
    """

    def __init__(self, config: OrderGuardConfig, db: Session):
        self.config = config
        self.db = db

    # ------------------------------------------------------------------

    def check(
        self,
        order:           OrderRequest,
        *,
        mode:            str | None = None,
        requested_by_ai: bool       = False,
        agent_chain_id:  str | None = None,
        now:             datetime | None = None,
    ) -> OrderGuardResult:
        """주문에 대해 fingerprint + idempotency + cooldown + pending 검사."""
        cfg = self.config
        now = now or _now_utc()

        fingerprint = build_order_fingerprint(
            order, mode=mode,
            price_bucket_pct=cfg.price_bucket_pct,
            agent_chain_id=agent_chain_id,
        )

        # 1. idempotency replay — client_order_id가 같으면 RETRY_REPLAY.
        if order.client_order_id:
            existing = find_audit_by_client_order_id(
                self.db, client_order_id=order.client_order_id,
            )
            if existing is not None:
                return OrderGuardResult(
                    decision=GuardDecision.RETRY_REPLAY,
                    fingerprint=fingerprint,
                    reasons=[
                        f"idempotency replay: client_order_id={order.client_order_id} "
                        f"already processed (audit_id={existing.id})"
                    ],
                    audit_replay_id=existing.id,
                    blocked_by="idempotency_replay",
                )

        # 2. duplicate fingerprint — 다른 client_order_id로 같은 주문 들어옴.
        if cfg.duplicate_window_seconds > 0:
            dup = _find_recent_same_fingerprint(
                self.db, fingerprint=fingerprint,
                window_seconds=cfg.duplicate_window_seconds,
            )
            if dup is not None:
                # idempotency replay와 구분하기 위해 client_order_id가 다르거나
                # 둘 다 None인 경우만 DUPLICATE로 분류.
                if (
                    order.client_order_id is None
                    or dup.client_order_id != order.client_order_id
                ):
                    return OrderGuardResult(
                        decision=GuardDecision.DUPLICATE,
                        fingerprint=fingerprint,
                        reasons=[
                            f"duplicate order detected: same fingerprint "
                            f"{fingerprint} within {cfg.duplicate_window_seconds}s "
                            f"window (audit_id={dup.id})"
                        ],
                        blocked_by="duplicate",
                    )

        # 3. pending order guard — 같은 symbol + side 미체결.
        if cfg.block_when_pending_same_side:
            side_str = order.side.value if isinstance(order.side, OrderSide) else str(order.side)
            if _has_pending_same_side(self.db, symbol=order.symbol, side=side_str):
                return OrderGuardResult(
                    decision=GuardDecision.PENDING_BLOCKED,
                    fingerprint=fingerprint,
                    reasons=[
                        f"pending order exists for ({order.symbol}, {side_str}) — "
                        "신규 같은 방향 주문 차단"
                    ],
                    blocked_by="pending",
                )

        # 4. cooldown — symbol / strategy-symbol / post-exit / ai
        side_str = order.side.value if isinstance(order.side, OrderSide) else str(order.side)
        last_symbol = _last_executed_for_symbol(self.db, symbol=order.symbol)
        last_strat_sym = _last_executed_for_strategy_symbol(
            self.db, strategy=order.strategy, symbol=order.symbol,
        )

        cooldowns: list[tuple[OrderAuditLog | None, int, str]] = []
        if cfg.symbol_cooldown_seconds > 0 and last_symbol is not None:
            cooldowns.append((last_symbol, cfg.symbol_cooldown_seconds, "symbol_cooldown"))
        if cfg.strategy_symbol_cooldown_seconds > 0 and last_strat_sym is not None:
            cooldowns.append((
                last_strat_sym, cfg.strategy_symbol_cooldown_seconds,
                "strategy_symbol_cooldown",
            ))
        # post-exit: 마지막 SELL이 cooldown 안이면 같은 symbol BUY 차단.
        if cfg.post_exit_cooldown_seconds > 0 and order.side == OrderSide.BUY:
            last_sell = _last_executed_for_symbol(
                self.db, symbol=order.symbol, side="SELL",
            )
            if last_sell is not None:
                cooldowns.append((
                    last_sell, cfg.post_exit_cooldown_seconds, "post_exit_cooldown",
                ))
        # AI extra: cooldown 윈도우 위에 누적 적용.
        if requested_by_ai and cfg.ai_extra_cooldown_seconds > 0:
            if last_symbol is not None:
                cooldowns.append((
                    last_symbol, cfg.ai_extra_cooldown_seconds, "ai_cooldown",
                ))

        for ref_row, window_s, label in cooldowns:
            ref_ts = _aware(ref_row.created_at) if ref_row is not None else None
            if ref_ts is None:
                continue
            elapsed = (now - ref_ts).total_seconds()
            if elapsed < window_s:
                remaining = int(window_s - elapsed) + 1
                return OrderGuardResult(
                    decision=GuardDecision.COOLDOWN,
                    fingerprint=fingerprint,
                    reasons=[
                        f"{label}: 마지막 주문 후 {elapsed:.0f}s 경과, "
                        f"{window_s}s cooldown 안 — {remaining}s 후 재시도"
                    ],
                    cooldown_remaining_seconds=remaining,
                    blocked_by=label,
                )

        return OrderGuardResult(
            decision=GuardDecision.ALLOW,
            fingerprint=fingerprint,
        )


# ---------- module invariants ----------
#
# 본 모듈은 broker.place_order, broker.cancel_order 호출 형태를 작성하지 않으며
# OrderExecutor / route_order 도 import하지 않는다. 본 가드는 순수 read-only —
# 결과를 받은 호출자가 audit row 작성과 분기를 수행. 테스트가 grep으로 invariant
# 강제 (tests/test_order_guard.py::TestSafety).
