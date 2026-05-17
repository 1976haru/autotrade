"""#2-09: Paper Auto Loop ledger — in-memory append-only event store.

AI Paper 판단 + 가상 체결 결과를 *advisory* ledger 에 기록한다. 본 PR 시점
저장소는 thread-safe in-memory ring (default capacity 1000) — DB / 파일
영구화는 후속 PR 에서 별도 옵트인.

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. **본 ledger 는 *실 broker 와 연결 0건*** — `record()` 가 broker / OrderExecutor /
   route_order 를 호출 0건. PaperLoopEvent dataclass 의 `is_order_signal=False`
   가 양 끝에서 lock.
2. **state-aware 기록 정책**: 거래성 event (BUY/SELL/EXIT) 는 `loop_state=
   "RUNNING"` 에서만 기록. 다른 state 에서 시도 시 `LedgerStateError` raise.
   HOLD / NO_OP 는 모든 state 에서 기록 가능.
3. **broker / OrderExecutor / route_order import 0건** — 정적 grep 가드.
4. **외부 HTTP / AI SDK import 0건**.
5. **secret 추가 0건** — `record()` 가 metadata 의 키 이름을 검사해 secret 패턴
   포함 시 `SecretInLedgerError` raise.
6. **DB write 0건** — in-memory ring 만.

## 사용 흐름

```python
from app.auto_paper.ledger import get_ledger, record_paper_event
from app.auto_paper.events import DecisionAction, PaperFillStatus

# Paper Auto Loop 의 paper_tick_handler 안에서 사용:
record_paper_event(
    loop_state="RUNNING",
    strategy="sma_crossover",
    symbol="005930",
    decision_action=DecisionAction.HOLD,
    confidence=0.65,
    reason="trend not confirmed yet",
)

# 또는 가상 체결:
record_paper_event(
    loop_state="RUNNING",
    strategy="sma_crossover",
    symbol="005930",
    decision_action=DecisionAction.BUY,
    confidence=0.78,
    reason="MA crossover + volume confirmation",
    paper_order_id="paper-2026-05-18-001",
    paper_fill_status=PaperFillStatus.PAPER_FILLED,
    virtual_position_delta=10,
    pnl_estimate=0.0,
)
```

API 는 `GET /api/auto-paper/ledger` 와 `GET /api/auto-paper/events` 가 모두
동일 ledger 를 read-only 노출 (alias — 운영자 친화 두 경로).
"""

from __future__ import annotations

import re
import threading
import uuid
from collections import deque
from typing import Any, Iterable

from app.auto_paper.events import (
    DecisionAction,
    PaperFillStatus,
    PaperLoopEvent,
    TRADE_ACTIONS,
    now_iso,
)


DEFAULT_LEDGER_CAPACITY = 1000


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────


class LedgerStateError(RuntimeError):
    """trade event (BUY/SELL/EXIT) 가 RUNNING 이 아닌 state 에서 기록 시도."""


class SecretInLedgerError(ValueError):
    """ledger metadata 에 secret 추정 패턴 발견 — 기록 거부."""


# ─────────────────────────────────────────────────────────────────────────────
# Secret guard — metadata key/value 검사
# ─────────────────────────────────────────────────────────────────────────────


# 키 이름 기반 차단 — *값* 패턴 매칭보다 *키* 차단이 더 명확/안전.
_SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "api_key", "apikey", "secret", "app_secret", "appsecret",
    "access_token", "accesstoken", "bearer", "password", "passwd",
    "private_key", "privatekey", "anthropic", "openai", "kis_app_key",
    "kis_app_secret", "account_no", "account_number", "kis_account",
)


# 값 패턴 — 실수로 secret 이 들어간 경우 마지막 안전망.
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"PST[A-Za-z0-9]{30,}"),    # KIS personal token shape
)


def _check_no_secret(metadata: dict[str, Any] | None) -> None:
    """metadata 의 key/value 에 secret 패턴 0건 검증.

    Raises:
        SecretInLedgerError — 패턴 발견 시.
    """
    if not metadata:
        return
    for k, v in metadata.items():
        kl = str(k).lower()
        for pat in _SECRET_KEY_PATTERNS:
            if pat in kl:
                raise SecretInLedgerError(
                    f"forbidden metadata key (secret-like): {k!r}"
                )
        if isinstance(v, str):
            for rx in _SECRET_VALUE_PATTERNS:
                if rx.search(v):
                    raise SecretInLedgerError(
                        f"forbidden metadata value (secret pattern) for key {k!r}"
                    )


# ─────────────────────────────────────────────────────────────────────────────
# Ledger class
# ─────────────────────────────────────────────────────────────────────────────


class PaperLoopLedger:
    """Thread-safe in-memory ring ledger — append-only, capacity-limited.

    오래된 entry 는 capacity 도달 시 자동 drop (FIFO). 운영자가 영구 저장이
    필요하면 후속 PR 의 DB / file ledger 와 통합.
    """

    def __init__(self, capacity: int = DEFAULT_LEDGER_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity: int = int(capacity)
        self._events: deque[PaperLoopEvent] = deque(maxlen=self._capacity)
        self._lock = threading.Lock()
        # 통계 — 운영자 read-only 카운터.
        self._dropped_count: int = 0    # capacity overflow 로 drop 된 누계
        self._reject_count: int = 0     # state guard 로 거부된 누계

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def record(self, event: PaperLoopEvent) -> PaperLoopEvent:
        """state-aware 기록 — trade event 는 RUNNING 에서만, secret 검증 포함.

        Raises:
            LedgerStateError — trade event + non-RUNNING state.
            SecretInLedgerError — metadata 에 secret 패턴.
        """
        # 1) secret guard.
        _check_no_secret(event.metadata)
        # 2) state guard.
        if event.is_trade_event() and event.loop_state != "RUNNING":
            with self._lock:
                self._reject_count += 1
            raise LedgerStateError(
                f"trade event ({event.decision_action.value}) rejected in "
                f"loop_state={event.loop_state!r} — only RUNNING allowed."
            )
        # 3) append.
        with self._lock:
            if len(self._events) == self._capacity:
                self._dropped_count += 1
            self._events.append(event)
        return event

    def recent(self, limit: int = 50) -> list[PaperLoopEvent]:
        """가장 최근 N 개 — 최신순 (오래된 것이 마지막)."""
        if limit < 1:
            return []
        with self._lock:
            snapshot = list(self._events)
        return snapshot[-limit:]

    def all_events(self) -> list[PaperLoopEvent]:
        """전체 snapshot (capacity 내). read-only copy."""
        with self._lock:
            return list(self._events)

    def filter_by(
        self,
        *,
        loop_state:      str | None = None,
        strategy:        str | None = None,
        symbol:          str | None = None,
        decision_action: DecisionAction | None = None,
    ) -> list[PaperLoopEvent]:
        """단순 필터 — AND 조합."""
        with self._lock:
            snapshot = list(self._events)
        out: list[PaperLoopEvent] = []
        for ev in snapshot:
            if loop_state is not None and ev.loop_state != loop_state:
                continue
            if strategy is not None and ev.strategy != strategy:
                continue
            if symbol is not None and ev.symbol != symbol:
                continue
            if decision_action is not None and ev.decision_action != decision_action:
                continue
            out.append(ev)
        return out

    def stats(self) -> dict[str, Any]:
        """카운트 통계 — read-only."""
        with self._lock:
            total = len(self._events)
            by_action: dict[str, int] = {}
            by_state: dict[str, int] = {}
            for ev in self._events:
                by_action[ev.decision_action.value] = \
                    by_action.get(ev.decision_action.value, 0) + 1
                by_state[ev.loop_state] = \
                    by_state.get(ev.loop_state, 0) + 1
            return {
                "total_events":   total,
                "capacity":       self._capacity,
                "dropped_count":  self._dropped_count,
                "reject_count":   self._reject_count,
                "by_action":      by_action,
                "by_state":       by_state,
            }

    def clear(self) -> int:
        """전체 비우기 (test 용). 운영 시 호출 안 함 — append-only 정책."""
        with self._lock:
            n = len(self._events)
            self._events.clear()
            self._dropped_count = 0
            self._reject_count = 0
            return n


# ─────────────────────────────────────────────────────────────────────────────
# Singleton + helper
# ─────────────────────────────────────────────────────────────────────────────


_LEDGER_LOCK = threading.Lock()
_LEDGER: PaperLoopLedger | None = None


def get_ledger() -> PaperLoopLedger:
    """프로세스 단위 singleton — caller (route handler / paper handler) 가 사용."""
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is None:
            _LEDGER = PaperLoopLedger()
        return _LEDGER


def reset_ledger_for_tests() -> None:
    """test 격리용 — 운영 코드에서 호출 금지."""
    global _LEDGER
    with _LEDGER_LOCK:
        _LEDGER = None


def record_paper_event(
    *,
    loop_state:             str,
    strategy:               str,
    symbol:                 str,
    decision_action:        DecisionAction,
    reason:                 str,
    confidence:             float | None = None,
    risk_flags:             Iterable[str] | None = None,
    paper_order_id:         str | None = None,
    paper_fill_status:      PaperFillStatus = PaperFillStatus.NA,
    virtual_position_delta: int = 0,
    pnl_estimate:           float = 0.0,
    metadata:               dict[str, Any] | None = None,
    event_id:               str | None = None,
    timestamp:              str | None = None,
) -> PaperLoopEvent:
    """편의 helper — event 생성 + singleton ledger 에 기록.

    `record_paper_event(loop_state="RUNNING", strategy=..., symbol=...,
    decision_action=DecisionAction.HOLD, reason="...")` 형태.

    state-aware 정책 + secret 검사 모두 `PaperLoopLedger.record()` 가 강제.
    """
    event = PaperLoopEvent(
        event_id=(event_id or _new_event_id()),
        timestamp=(timestamp or now_iso()),
        loop_state=loop_state,
        strategy=strategy,
        symbol=symbol,
        decision_action=decision_action,
        confidence=confidence,
        reason=reason,
        risk_flags=list(risk_flags or []),
        paper_order_id=paper_order_id,
        paper_fill_status=paper_fill_status,
        virtual_position_delta=int(virtual_position_delta),
        pnl_estimate=float(pnl_estimate),
        metadata=dict(metadata or {}),
    )
    return get_ledger().record(event)


def _new_event_id() -> str:
    return f"paper-evt-{uuid.uuid4().hex[:12]}"


# Re-export for caller convenience.
__all__ = [
    "DEFAULT_LEDGER_CAPACITY",
    "LedgerStateError",
    "PaperLoopLedger",
    "SecretInLedgerError",
    "get_ledger",
    "record_paper_event",
    "reset_ledger_for_tests",
    # forward enum names for caller convenience.
    "DecisionAction",
    "PaperFillStatus",
    "TRADE_ACTIONS",
]
