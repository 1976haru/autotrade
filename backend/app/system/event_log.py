"""Runtime event log — *in-memory ring buffer* of operator-facing events.

EXE / PAPER / SIMULATION 운영자가 "지금 무슨 일이 일어나고 있는가" 를 한
곳에서 시간순으로 볼 수 있게 하는 단일 store. DB 기반 audit log (#68 등)
와는 *별개* — 본 모듈은:

- 무거운 DB / I/O 없는 *in-memory only* (프로세스 재시작 시 비움)
- 8 level × 11 category × code 매트릭스
- *Secret 자동 차단* — emit() 가 sanitize 검사 후 의심 패턴 발견 시 reject
- broker / OrderExecutor / route_order import 0건

CLAUDE.md 절대 원칙 (테스트로 lock):
- broker / OrderExecutor / route_order import 0건
- KIS / Anthropic / OpenAI / httpx / requests import 0건
- app.core.config.get_settings import 0건
- app.brokers / app.execution / app.kis_paper.engine import 0건
- RuntimeEvent.is_order_signal / is_live_authorization = False 영구
- safe_for_ui = True 영구, contains_secret = False 영구
- DB write 0건 — 본 모듈은 read-only DB 도 사용 안 함
"""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from functools import lru_cache
from typing import Any


_log = logging.getLogger("autotrade.system.event_log")


# ============================================================================
# Level + Category enums
# ============================================================================


class EventLevel(StrEnum):
    """이벤트 심각도 — 화면 필터링 / 색상 매핑에 사용."""
    DEBUG    = "DEBUG"
    INFO     = "INFO"
    WARN     = "WARN"
    ERROR    = "ERROR"
    CRITICAL = "CRITICAL"


_LEVEL_RANK: dict[EventLevel, int] = {
    EventLevel.DEBUG:    10,
    EventLevel.INFO:     20,
    EventLevel.WARN:     30,
    EventLevel.ERROR:    40,
    EventLevel.CRITICAL: 50,
}


class EventCategory(StrEnum):
    """이벤트 분류 — UI 필터 / 라벨 + diagnostics 추론에 사용."""
    SYSTEM      = "SYSTEM"
    BACKEND     = "BACKEND"
    MARKET_DATA = "MARKET_DATA"
    UNIVERSE    = "UNIVERSE"
    STRATEGY    = "STRATEGY"
    AGENT       = "AGENT"
    RISK        = "RISK"
    PERMISSION  = "PERMISSION"
    ORDER       = "ORDER"
    PAPER       = "PAPER"
    DESKTOP     = "DESKTOP"


# ============================================================================
# Secret patterns — 자동 차단 (fail-closed)
# ============================================================================


_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_api_key",     re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("anthropic_api_key",  re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{30,}\b")),
    ("github_pat",         re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("slack_token",        re.compile(r"\bxox[bpaoist]-[A-Za-z0-9-]{10,}\b")),
    ("bearer_token",       re.compile(r"\bBearer\s+[A-Za-z0-9\.\-_]{20,}\b")),
    ("jwt",                re.compile(r"\beyJ[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\b")),
    ("kis_app_key",        re.compile(r"\bPS[A-Za-z0-9]{20,}\b")),
    # KIS 계좌번호: 8-2 형식. 6자리 종목코드 / 8자리 날짜와 구분 위해
    # *대시 포함 8-2* 만 매칭 (너무 공격적으로 잡지 않음).
    ("korean_account",     re.compile(r"\b\d{8}-\d{2}\b")),  # security-scan: ignore
    ("credit_card",        re.compile(r"\b\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}\b")),
    ("rrn",                re.compile(r"\b\d{6}-\d{7}\b")),
)


# 의심 *key 이름* — 값이 짧아도 key 자체를 노출하면 위험.
_SUSPICIOUS_KEY_NAMES = (
    "api_key", "api_secret", "secret_token", "access_token",
    "app_key", "app_secret", "kis_app_key", "kis_app_secret",
    "anthropic_api_key", "openai_api_key", "telegram_bot_token",
    "kis_account_no", "password",
)


class SecretLeakBlockedError(ValueError):
    """이벤트 message / details 에 secret 의심 패턴 발견 — emit() 거부.

    sanitize / redact 가 아니라 *fail-closed* — caller 가 잘못된 데이터를
    넣지 못하도록 raise. 호출 측에서 secret 을 *제거 후* 다시 emit.
    """


def _scan_for_secret(text: str) -> str | None:
    """secret 패턴이 발견되면 첫 매칭 이름 반환, 없으면 None."""
    if not text:
        return None
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return name
    return None


def _scan_dict_for_secret(data: Any) -> str | None:
    """details dict 재귀 스캔 — key 이름 + value 모두 점검."""
    if data is None:
        return None
    if isinstance(data, str):
        return _scan_for_secret(data)
    if isinstance(data, (int, float, bool)):
        return None
    if isinstance(data, dict):
        for k, v in data.items():
            # key 이름 자체가 의심스러우면 거부.
            key_norm = str(k).lower()
            for suspicious in _SUSPICIOUS_KEY_NAMES:
                if suspicious in key_norm:
                    return f"suspicious_key:{k}"
            found = _scan_dict_for_secret(v)
            if found:
                return found
        return None
    if isinstance(data, (list, tuple)):
        for item in data:
            found = _scan_dict_for_secret(item)
            if found:
                return found
        return None
    # unknown type — stringify 후 검사.
    return _scan_for_secret(str(data))


# ============================================================================
# Event dataclass
# ============================================================================


@dataclass(frozen=True)
class RuntimeEvent:
    """단일 runtime event — *advisory*, broker / route_order 호출 0건."""

    id:                int
    timestamp:         str            # ISO 8601 UTC
    level:             EventLevel
    category:          EventCategory
    code:              str
    message:           str
    details:           dict[str, Any] = field(default_factory=dict)

    safe_for_ui:       bool = True
    contains_secret:   bool = False
    is_order_signal:   bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        if self.safe_for_ui is not True:
            raise ValueError("RuntimeEvent.safe_for_ui must be True")
        if self.contains_secret is not False:
            raise ValueError("RuntimeEvent.contains_secret must be False")
        if self.is_order_signal is not False:
            raise ValueError("RuntimeEvent.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError(
                "RuntimeEvent.is_live_authorization must be False"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id":                self.id,
            "timestamp":         self.timestamp,
            "level":             self.level.value,
            "category":          self.category.value,
            "code":              self.code,
            "message":           self.message,
            "details":           dict(self.details),
            "safe_for_ui":       self.safe_for_ui,
            "contains_secret":   self.contains_secret,
            "is_order_signal":   self.is_order_signal,
            "is_live_authorization": self.is_live_authorization,
        }


# ============================================================================
# Ring buffer
# ============================================================================


_DEFAULT_CAPACITY: int = 500


class RuntimeEventLog:
    """Thread-safe in-memory ring buffer event log.

    `emit(level, category, code, message, details)` 한 entry append. 기본
    capacity 500 — 그 이상이면 가장 오래된 entry 가 자동 제거.

    `recent(limit, level, category, since, code)` 로 filter 조회.

    secret 의심 패턴은 emit 시점에 *자동 차단* (SecretLeakBlockedError) —
    sanitize / redact 가 아니라 *fail-closed*.
    """

    def __init__(self, *, capacity: int = _DEFAULT_CAPACITY) -> None:
        if int(capacity) < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        self._lock = threading.Lock()
        self._buffer: deque[RuntimeEvent] = deque(maxlen=int(capacity))
        self._capacity: int = int(capacity)
        self._next_id: int = 1
        self._counters: dict[str, int] = {}

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    def clear(self) -> None:
        """테스트 / 운영자 명시 reset."""
        with self._lock:
            self._buffer.clear()
            self._next_id = 1
            self._counters.clear()

    def emit(
        self,
        *,
        level:    EventLevel | str,
        category: EventCategory | str,
        code:     str,
        message:  str,
        details:  dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> RuntimeEvent:
        """단일 event 기록. Secret 자동 차단 + thread-safe.

        Returns:
            RuntimeEvent — 기록된 entry. caller 는 보통 무시.

        Raises:
            SecretLeakBlockedError: message / details 에 secret 의심 패턴
                발견 시 fail-closed.
            ValueError: level / category 가 enum 으로 변환되지 않을 때.
        """
        lvl = level if isinstance(level, EventLevel) else EventLevel(str(level).upper())
        cat = (
            category if isinstance(category, EventCategory)
            else EventCategory(str(category).upper())
        )
        msg = str(message or "")
        det = dict(details or {})

        # ── Secret scan — fail-closed ──
        found = _scan_for_secret(msg) or _scan_for_secret(code or "")
        if found is None:
            found = _scan_dict_for_secret(det)
        if found:
            raise SecretLeakBlockedError(
                f"event would leak secret-like pattern: {found}"
            )

        # ── Build entry ──
        ts = (timestamp or datetime.now(timezone.utc)).isoformat()
        with self._lock:
            event = RuntimeEvent(
                id=self._next_id,
                timestamp=ts,
                level=lvl,
                category=cat,
                code=str(code or "EVENT"),
                message=msg,
                details=det,
            )
            self._next_id += 1
            self._buffer.append(event)
            counter_key = f"{cat.value}:{lvl.value}"
            self._counters[counter_key] = self._counters.get(counter_key, 0) + 1
        return event

    def recent(
        self,
        *,
        limit:    int = 100,
        level:    EventLevel | str | None = None,
        category: EventCategory | str | None = None,
        since:    datetime | str | None = None,
        code:     str | None = None,
        min_level: EventLevel | str | None = None,
    ) -> list[RuntimeEvent]:
        """최근 이벤트 read-only — 가장 최근이 list 의 끝 (DESC).

        filter 조합:
        - `level` = 정확 매치 (예: WARN 만)
        - `min_level` = 이상 (예: WARN → WARN/ERROR/CRITICAL)
        - `category` = 정확 매치
        - `since` = 해당 시각 이후 ISO 또는 datetime
        - `code` = 정확 매치 (대소문자 구분)
        - `limit` = 최대 entry 수 (기본 100, 0 이하는 1 로 보정)
        """
        lim = max(1, int(limit))
        lvl_exact = (
            level if level is None or isinstance(level, EventLevel)
            else EventLevel(str(level).upper())
        )
        lvl_min = (
            min_level if min_level is None or isinstance(min_level, EventLevel)
            else EventLevel(str(min_level).upper())
        )
        cat = (
            category if category is None or isinstance(category, EventCategory)
            else EventCategory(str(category).upper())
        )
        since_dt: datetime | None = None
        if isinstance(since, datetime):
            since_dt = since
        elif isinstance(since, str) and since:
            try:
                since_dt = datetime.fromisoformat(since)
            except ValueError:
                since_dt = None

        with self._lock:
            items = list(self._buffer)
        # 시간 ASC → 필터 → tail N → 그대로 ASC 로 반환 (caller 가 보통 ASC 기대).
        out: list[RuntimeEvent] = []
        for ev in items:
            if lvl_exact is not None and ev.level != lvl_exact:
                continue
            if lvl_min is not None and _LEVEL_RANK[ev.level] < _LEVEL_RANK[lvl_min]:
                continue
            if cat is not None and ev.category != cat:
                continue
            if code is not None and ev.code != code:
                continue
            if since_dt is not None:
                try:
                    ev_dt = datetime.fromisoformat(ev.timestamp)
                except ValueError:
                    ev_dt = None
                if ev_dt is None or ev_dt < since_dt:
                    continue
            out.append(ev)
        return out[-lim:]

    def counters_snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def summary(self) -> dict[str, Any]:
        """레벨 / 카테고리 별 count 요약 — diagnostics 가 carry."""
        with self._lock:
            items = list(self._buffer)
        by_level: dict[str, int] = {lv.value: 0 for lv in EventLevel}
        by_category: dict[str, int] = {ct.value: 0 for ct in EventCategory}
        for ev in items:
            by_level[ev.level.value] += 1
            by_category[ev.category.value] += 1
        return {
            "total":          len(items),
            "by_level":       by_level,
            "by_category":    by_category,
            "capacity":       self._capacity,
        }


# ============================================================================
# Process singleton
# ============================================================================


@lru_cache
def _get_log_singleton() -> RuntimeEventLog:
    return RuntimeEventLog()


def get_runtime_event_log() -> RuntimeEventLog:
    return _get_log_singleton()


def reset_runtime_event_log_for_tests() -> None:
    """테스트 fixture — singleton 의 entry / counter 초기화."""
    get_runtime_event_log().clear()


# ============================================================================
# Convenience emit helpers — 운영 코드에서 짧게 호출
# ============================================================================


def log_event(
    *,
    level:    EventLevel | str,
    category: EventCategory | str,
    code:     str,
    message:  str,
    details:  dict[str, Any] | None = None,
) -> RuntimeEvent | None:
    """일반 emit helper. SecretLeakBlockedError 발생 시 *경고만* — 운영
    흐름을 깨뜨리지 않도록 None 반환 (운영자가 별도 audit log 점검).
    """
    try:
        return get_runtime_event_log().emit(
            level=level, category=category, code=code,
            message=message, details=details,
        )
    except SecretLeakBlockedError as e:
        _log.warning(
            "[event_log] dropped event with secret-like pattern: %s "
            "(code=%s level=%s category=%s)",
            e, code, level, category,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "[event_log] emit failed: %s: %s", type(exc).__name__, exc,
        )
        return None


__all__ = [
    "EventLevel",
    "EventCategory",
    "RuntimeEvent",
    "RuntimeEventLog",
    "SecretLeakBlockedError",
    "get_runtime_event_log",
    "reset_runtime_event_log_for_tests",
    "log_event",
]
