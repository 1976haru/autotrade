"""Data freshness 통합 helper (#20).

여러 데이터 소스(quote / tick / bar cache / WebSocket feed)의 stale 여부를
한 인터페이스로 통합한다. 본 모듈은 *조회만* — 신규 BUY 차단을 호출자가
선택할 수 있도록 (block, reason)을 반환한다.

설계 원칙:
- broker / RiskManager / PermissionGate / OrderExecutor를 import하지 않는다.
- 기존 staleness.py(MarketBar 캐시 stale)를 재사용 — 중복 구현 금지.
- 기존 RiskManager.evaluate_order의 latest_price_timestamp guard(143)는
  그대로 유지 — 본 모듈은 그 위에 *추가* freshness 신호를 제공한다.
- SELL/청산 신호는 *자동으로 차단되지 않는다* — 위험 축소 목적이므로
  호출자가 별도 정책으로 결정 (`should_block_buy_*` 함수만 제공).

CLAUDE.md 절대 원칙 — 본 모듈은 broker live order 호출 / LIVE flag
변경과 무관하며, frontend에 secret을 노출하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.market.staleness import is_bar_cache_stale


# ---------- DTOs ----------


@dataclass(frozen=True)
class FreshnessStatus:
    """단일 데이터 소스의 freshness 평가 결과."""
    symbol:           str
    source:           str           # "quote" | "bar" | "feed"
    is_stale:         bool
    age_seconds:      float | None  # None: timestamp 자체가 없음
    last_seen_at:     datetime | None
    max_age_seconds:  int
    reason:           str | None
    checked_at:       datetime

    def to_dict(self) -> dict:
        return {
            "symbol":          self.symbol,
            "source":          self.source,
            "is_stale":        self.is_stale,
            "age_seconds":     self.age_seconds,
            "last_seen_at":    self.last_seen_at.isoformat() if self.last_seen_at else None,
            "max_age_seconds": self.max_age_seconds,
            "reason":          self.reason,
            "checked_at":      self.checked_at.isoformat(),
        }


@dataclass(frozen=True)
class DataFeedState:
    """WebSocket / push feed의 연결 + 마지막 메시지 상태.

    실 KIS / Kiwoom WebSocket 통합은 Phase 2 — 본 PR에서는 외부 입력으로
    받아 freshness 판단에만 사용한다 (운영자가 health-check endpoint나
    내부 monitoring에서 채워주는 모델).
    """
    connected:        bool
    reconnecting:     bool = False
    last_message_at:  datetime | None = None


# ---------- helpers ----------


def _ensure_utc(ts: datetime) -> datetime:
    """naive datetime은 UTC로 가정. tz-aware은 UTC로 변환."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _age_seconds(now: datetime, ts: datetime | None) -> float | None:
    """now - ts (UTC). ts가 None이면 None. future timestamp는 0으로 clamp."""
    if ts is None:
        return None
    age = (now - _ensure_utc(ts)).total_seconds()
    return max(0.0, age)


def freshness_reason(*, source: str, is_stale: bool, has_timestamp: bool,
                     age_seconds: float | None, max_age_seconds: int) -> str | None:
    """사람이 읽을 수 있는 freshness reason 문구. fresh면 None.

    - timestamp 자체 없음 → "{source} data missing"
    - 시간 기준 검사 비활성(max_age_seconds<=0)인데 stale=True → "{source} data unavailable"
    - 일반 stale → "{source} data stale (Xs > Ys threshold)"
    """
    if not is_stale:
        return None
    if not has_timestamp:
        return f"{source} data missing (no timestamp recorded)"
    if max_age_seconds <= 0 or age_seconds is None:
        return f"{source} data unavailable"
    return (
        f"{source} data stale ({age_seconds:.0f}s > {max_age_seconds}s threshold)"
    )


# ---------- quote freshness ----------


def is_quote_stale(
    *,
    symbol:          str,
    last_seen_at:    datetime | None,
    max_age_seconds: int,
    now:             datetime | None = None,
) -> FreshnessStatus:
    """단일 quote 또는 tick의 freshness 평가.

    - last_seen_at is None → is_stale=True (data missing)
    - max_age_seconds <= 0 → 시간 기준 검사 비활성. last_seen_at이 있으면
      is_stale=False (아무 정보도 없으면 stale로 분류 — 안전 측)
    - age > max_age_seconds → is_stale=True
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        now = _ensure_utc(now)

    if last_seen_at is None:
        return FreshnessStatus(
            symbol=symbol, source="quote",
            is_stale=True, age_seconds=None,
            last_seen_at=None, max_age_seconds=max_age_seconds,
            reason=freshness_reason(
                source="quote", is_stale=True, has_timestamp=False,
                age_seconds=None, max_age_seconds=max_age_seconds,
            ),
            checked_at=now,
        )

    age = _age_seconds(now, last_seen_at)
    # age가 None일 수는 없음 (위에서 last_seen_at None을 처리함) — type-narrow.
    assert age is not None

    if max_age_seconds <= 0:
        # 시간 기준은 비활성 — last_seen_at이 있으니 fresh.
        return FreshnessStatus(
            symbol=symbol, source="quote",
            is_stale=False, age_seconds=age,
            last_seen_at=last_seen_at, max_age_seconds=max_age_seconds,
            reason=None, checked_at=now,
        )

    stale = age > max_age_seconds
    return FreshnessStatus(
        symbol=symbol, source="quote",
        is_stale=stale, age_seconds=age,
        last_seen_at=last_seen_at, max_age_seconds=max_age_seconds,
        reason=freshness_reason(
            source="quote", is_stale=stale, has_timestamp=True,
            age_seconds=age, max_age_seconds=max_age_seconds,
        ),
        checked_at=now,
    )


# ---------- bar freshness (wraps existing staleness.py) ----------


def is_bar_stale(
    db:              Session,
    *,
    symbol:          str,
    interval:        str,
    max_age_seconds: int,
    now:             datetime | None = None,
) -> FreshnessStatus:
    """봉 캐시 freshness — `staleness.is_bar_cache_stale`에 위임.

    기존 staleness.py 로직을 재사용해 중복 분기 방지. 결과를 `FreshnessStatus`
    로 통일해 호출자가 quote / bar / feed를 동형으로 처리.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        now = _ensure_utc(now)

    stale, age = is_bar_cache_stale(
        db, symbol=symbol, interval=interval,
        max_age_seconds=max_age_seconds, now=now,
    )
    has_ts = age is not None
    return FreshnessStatus(
        symbol=symbol, source=f"bar:{interval}",
        is_stale=stale, age_seconds=age,
        last_seen_at=None,  # staleness.py는 fetched_at 자체를 노출하지 않음 — 호출자는 latest_bar_fetched_at 별도 사용
        max_age_seconds=max_age_seconds,
        reason=freshness_reason(
            source=f"bar:{interval}", is_stale=stale, has_timestamp=has_ts,
            age_seconds=age, max_age_seconds=max_age_seconds,
        ),
        checked_at=now,
    )


# ---------- WebSocket / feed freshness ----------


def is_feed_stale(
    *,
    symbol:          str,
    feed:            DataFeedState,
    max_age_seconds: int,
    now:             datetime | None = None,
) -> FreshnessStatus:
    """WebSocket / push feed 상태 평가.

    우선순위 (가장 강한 신호부터):
    1. reconnecting → stale, "data feed reconnecting"
    2. connected=False → stale, "data feed disconnected"
    3. last_message_at None → stale, "feed data missing"
    4. age > max_age_seconds → stale (max_age_seconds<=0이면 검사 비활성)
    5. 그 외 → fresh
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        now = _ensure_utc(now)

    if feed.reconnecting:
        return FreshnessStatus(
            symbol=symbol, source="feed",
            is_stale=True, age_seconds=None,
            last_seen_at=feed.last_message_at,
            max_age_seconds=max_age_seconds,
            reason="data feed reconnecting",
            checked_at=now,
        )
    if not feed.connected:
        return FreshnessStatus(
            symbol=symbol, source="feed",
            is_stale=True, age_seconds=None,
            last_seen_at=feed.last_message_at,
            max_age_seconds=max_age_seconds,
            reason="data feed disconnected",
            checked_at=now,
        )

    if feed.last_message_at is None:
        return FreshnessStatus(
            symbol=symbol, source="feed",
            is_stale=True, age_seconds=None,
            last_seen_at=None, max_age_seconds=max_age_seconds,
            reason=freshness_reason(
                source="feed", is_stale=True, has_timestamp=False,
                age_seconds=None, max_age_seconds=max_age_seconds,
            ),
            checked_at=now,
        )

    age = _age_seconds(now, feed.last_message_at)
    assert age is not None

    if max_age_seconds <= 0:
        return FreshnessStatus(
            symbol=symbol, source="feed",
            is_stale=False, age_seconds=age,
            last_seen_at=feed.last_message_at,
            max_age_seconds=max_age_seconds,
            reason=None, checked_at=now,
        )

    stale = age > max_age_seconds
    return FreshnessStatus(
        symbol=symbol, source="feed",
        is_stale=stale, age_seconds=age,
        last_seen_at=feed.last_message_at,
        max_age_seconds=max_age_seconds,
        reason=freshness_reason(
            source="feed", is_stale=stale, has_timestamp=True,
            age_seconds=age, max_age_seconds=max_age_seconds,
        ),
        checked_at=now,
    )


# ---------- order pre-check helpers ----------


def should_block_buy_for_quote(
    *,
    symbol:          str,
    last_seen_at:    datetime | None,
    max_age_seconds: int,
    now:             datetime | None = None,
) -> tuple[bool, str | None, FreshnessStatus]:
    """단일 quote 기반 신규 BUY 차단 결정. SELL/청산은 별도 정책 — 호출자가 분기."""
    status = is_quote_stale(
        symbol=symbol, last_seen_at=last_seen_at,
        max_age_seconds=max_age_seconds, now=now,
    )
    return status.is_stale, status.reason, status


def should_block_buy_for_bar(
    db:              Session,
    *,
    symbol:          str,
    interval:        str,
    max_age_seconds: int,
    now:             datetime | None = None,
) -> tuple[bool, str | None, FreshnessStatus]:
    """봉 캐시 기반 신규 BUY 차단 결정."""
    status = is_bar_stale(
        db, symbol=symbol, interval=interval,
        max_age_seconds=max_age_seconds, now=now,
    )
    return status.is_stale, status.reason, status


def should_block_buy_for_feed(
    *,
    symbol:          str,
    feed:            DataFeedState,
    max_age_seconds: int,
    now:             datetime | None = None,
) -> tuple[bool, str | None, FreshnessStatus]:
    """WebSocket / push feed 기반 신규 BUY 차단 결정."""
    status = is_feed_stale(
        symbol=symbol, feed=feed,
        max_age_seconds=max_age_seconds, now=now,
    )
    return status.is_stale, status.reason, status


# ============================================================================
# P-14: 가격 freshness + 비정상 / 급등락 가격 BUY 차단
# ============================================================================
#
# 기존 #20 infrastructure (FreshnessStatus / is_quote_stale / is_bar_stale /
# is_feed_stale) 는 *데이터 소스 단위* — quote / bar / feed 의 timestamp 기반
# stale 평가. P-14 는 그 위에 *paper sizing / affordability 전 단계* 의 단일
# pure 함수 `check_price_freshness` 를 얹어 다음을 동시에 처리:
#
#   1. price 가 None / 0 / 음수 → INVALID_PRICE
#   2. price_timestamp 가 너무 오래됨 → PRICE_STALE
#   3. reference_price 대비 변동률이 한도 초과 → ABNORMAL_PRICE_MOVE
#
# 결과 `PriceFreshnessResult` 는:
#   - allowed: True/False (BUY 차단 여부)
#   - reason_code: PRICE_FRESHNESS_OK / PRICE_STALE / INVALID_PRICE /
#     ABNORMAL_PRICE_MOVE / PRICE_MISSING / PRICE_TOO_LOW / PRICE_TOO_HIGH /
#     PRICE_CHANGE_TOO_LARGE / PRICE_CHECK_NOT_APPLICABLE
#   - reason_message: 한국어 사용자 표시 메시지
#   - age_seconds / price_change_pct / max_age_seconds / max_change_pct
#
# SELL / HOLD / NO_ACTION 은 *원칙적으로 차단하지 않는다* — caller 가
# `_is_buy_side_action` 으로 분기 후 본 함수 호출 또는 본 함수에 action 을 넘겨
# `PRICE_CHECK_NOT_APPLICABLE` 로 처리. P-14 는 최소한 *BUY 차단* 만 구현, SELL
# stale 경고는 후속 PR.


DEFAULT_MAX_AGE_SECONDS: int   = 60
DEFAULT_MAX_CHANGE_PCT:  float = 10.0


# ---------- reason_code 상수 (사용자 요청서 §2 정확) ----------


PRICE_FRESHNESS_OK            = "PRICE_FRESHNESS_OK"
PRICE_STALE                   = "PRICE_STALE"
INVALID_PRICE                 = "INVALID_PRICE"
ABNORMAL_PRICE_MOVE           = "ABNORMAL_PRICE_MOVE"
PRICE_MISSING                 = "PRICE_MISSING"
PRICE_TOO_LOW                 = "PRICE_TOO_LOW"
PRICE_TOO_HIGH                = "PRICE_TOO_HIGH"
PRICE_CHANGE_TOO_LARGE        = "PRICE_CHANGE_TOO_LARGE"
PRICE_CHECK_NOT_APPLICABLE    = "PRICE_CHECK_NOT_APPLICABLE"


# ---------- 사용자 표시 메시지 ----------


PRICE_FRESHNESS_OK_MESSAGE_KO        = "현재가 확인: 정상"
PRICE_STALE_MESSAGE_KO               = "현재가가 오래되어 매수 차단"
INVALID_PRICE_MESSAGE_KO             = "현재가가 비정상이라 매수 차단"
ABNORMAL_PRICE_MOVE_MESSAGE_KO       = "가격 급등락이 감지되어 매수 차단"
PRICE_MISSING_MESSAGE_KO             = "현재가가 없어 매수 차단"
PRICE_CHECK_NOT_APPLICABLE_MESSAGE_KO = (
    "현재 action 은 BUY 가 아니므로 가격 검사 적용 안 함"
)


# ---------- helpers ----------


_PAPER_BUY_TOKENS = frozenset({
    "buy", "open", "open_long", "long", "enter", "entry",
})


def _is_buy_action_for_price_check(action: str | None) -> bool:
    """P-14 BUY 의도 판별 — affordability/sizing 모듈과 동일 토큰."""
    if action is None:
        # action 미지정 시 *BUY 로 간주* (caller 가 BUY 흐름에서 본 함수를 호출
        # 한 것이라고 가정 — 안전 측 default).
        return True
    return action.strip().lower() in _PAPER_BUY_TOKENS


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    """ISO 문자열 / datetime 둘 다 허용. None 은 None 그대로."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Z suffix → +00:00 변환 (Python 3.10 ISO parser 호환).
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(s)
        except ValueError as exc:
            raise ValueError(
                f"price_timestamp / now 가 ISO datetime 이 아님: {value!r}"
            ) from exc
        return _ensure_utc(parsed)
    raise TypeError(
        f"datetime / str / None 가 아님: {type(value).__name__}"
    )


def _format_pct(value: float) -> str:
    """변동률 한국어 표시 — 소수 1자리."""
    return f"{value:.1f}%"


# ---------- P-14 result dataclass ----------


@dataclass(frozen=True)
class PriceFreshnessResult:
    """가격 freshness + 비정상 / 급등락 평가 결과 — *advisory*.

    `is_paper_only=True` / `is_order_signal=False` / `is_live_authorization=
    False` 영구 (post_init 가드).
    """

    allowed:               bool
    reason_code:           str
    reason_message:        str
    symbol:                str | None       = None
    action:                str | None       = None
    price:                 float | None     = None
    price_timestamp:       datetime | None  = None
    now:                   datetime | None  = None
    age_seconds:           float | None     = None
    max_age_seconds:       int              = DEFAULT_MAX_AGE_SECONDS
    reference_price:       float | None     = None
    price_change_pct:      float | None     = None
    max_change_pct:        float            = DEFAULT_MAX_CHANGE_PCT
    detail_message:        str              = ""

    is_paper_only:         bool = True
    is_order_signal:       bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        if self.is_paper_only is not True:
            raise ValueError(
                "PriceFreshnessResult.is_paper_only must be True"
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "PriceFreshnessResult.is_order_signal must be False"
            )
        if self.is_live_authorization is not False:
            raise ValueError(
                "PriceFreshnessResult.is_live_authorization must be False"
            )

    @property
    def is_blocked(self) -> bool:
        return self.allowed is False and self.reason_code != PRICE_FRESHNESS_OK

    def to_dict(self) -> dict:
        return {
            "allowed":           bool(self.allowed),
            "reason_code":       self.reason_code,
            "reason_message":    self.reason_message,
            "symbol":            self.symbol,
            "action":            self.action,
            "price":             self.price,
            "price_timestamp":   (
                self.price_timestamp.isoformat()
                if self.price_timestamp else None
            ),
            "now":               (
                self.now.isoformat() if self.now else None
            ),
            "age_seconds":       self.age_seconds,
            "max_age_seconds":   int(self.max_age_seconds),
            "reference_price":   self.reference_price,
            "price_change_pct":  self.price_change_pct,
            "max_change_pct":    float(self.max_change_pct),
            "detail_message":    self.detail_message,
            "is_paper_only":           self.is_paper_only,
            "is_order_signal":         self.is_order_signal,
            "is_live_authorization":   self.is_live_authorization,
        }


# ---------- core function ----------


def check_price_freshness(
    *,
    symbol:           str | None             = None,
    price:            int | float | None     = None,
    price_timestamp:  datetime | str | None  = None,
    now:              datetime | str | None  = None,
    max_age_seconds:  int                    = DEFAULT_MAX_AGE_SECONDS,
    reference_price:  int | float | None     = None,
    max_change_pct:   float                  = DEFAULT_MAX_CHANGE_PCT,
    action:           str | None             = None,
) -> PriceFreshnessResult:
    """가격 freshness + 비정상 / 급등락 평가 — *pure*.

    사용자 요청서 §3 정책 그대로:
      1. action 이 BUY 가 아니면 PRICE_CHECK_NOT_APPLICABLE (차단 X)
      2. price is None → PRICE_MISSING
      3. price <= 0 → INVALID_PRICE
      4. price_timestamp 가 없으면 PRICE_STALE (안전 측 — fresh 로 보지 않음)
      5. now - price_timestamp > max_age_seconds → PRICE_STALE
      6. reference_price > 0 이고 |price - ref| / ref > max_change_pct →
         ABNORMAL_PRICE_MOVE
      7. 모두 통과 → PRICE_FRESHNESS_OK

    Args:
        symbol: 종목 코드 (carry 만, verdict 영향 없음).
        price: 현재가.
        price_timestamp: 현재가 timestamp (datetime / ISO str).
        now: 평가 시각 (None → datetime.now(UTC)).
        max_age_seconds: stale 한도. 기본 60.
        reference_price: 직전 기준가 / previous_close. None 또는 <=0 이면 급등락
            검사 skip.
        max_change_pct: 변동률 한도 (%). 기본 10.0.
        action: BUY/SELL/HOLD/NO_ACTION. BUY 아니면 적용 안 함.

    Returns:
        PriceFreshnessResult — broker / route_order 호출 0건.
    """
    # 1. action 분기.
    if not _is_buy_action_for_price_check(action):
        return PriceFreshnessResult(
            allowed=True,
            reason_code=PRICE_CHECK_NOT_APPLICABLE,
            reason_message=PRICE_CHECK_NOT_APPLICABLE_MESSAGE_KO,
            symbol=symbol, action=action,
            price=float(price) if price is not None else None,
            max_age_seconds=int(max_age_seconds),
            max_change_pct=float(max_change_pct),
            detail_message=(
                "BUY 가 아닌 action 은 가격 검사 적용 안 함 — "
                "SELL / HOLD / NO_ACTION 은 별도 정책."
            ),
        )

    now_dt = _coerce_datetime(now) or datetime.now(timezone.utc)
    ts_dt  = _coerce_datetime(price_timestamp)

    # 2. price None.
    if price is None:
        return PriceFreshnessResult(
            allowed=False,
            reason_code=PRICE_MISSING,
            reason_message=PRICE_MISSING_MESSAGE_KO,
            symbol=symbol, action=action,
            price=None,
            price_timestamp=ts_dt, now=now_dt,
            max_age_seconds=int(max_age_seconds),
            max_change_pct=float(max_change_pct),
            detail_message="현재가 값이 없어 BUY 를 차단합니다.",
        )

    p = float(price)

    # 3. price <= 0.
    if p <= 0:
        return PriceFreshnessResult(
            allowed=False,
            reason_code=INVALID_PRICE,
            reason_message=INVALID_PRICE_MESSAGE_KO,
            symbol=symbol, action=action,
            price=p,
            price_timestamp=ts_dt, now=now_dt,
            max_age_seconds=int(max_age_seconds),
            max_change_pct=float(max_change_pct),
            detail_message=(
                f"현재가 {p} 가 0 이하라 BUY 를 차단합니다."
            ),
        )

    # 4. price_timestamp 없음 → 안전 측 STALE.
    if ts_dt is None:
        return PriceFreshnessResult(
            allowed=False,
            reason_code=PRICE_STALE,
            reason_message=PRICE_STALE_MESSAGE_KO,
            symbol=symbol, action=action,
            price=p,
            price_timestamp=None, now=now_dt,
            age_seconds=None,
            max_age_seconds=int(max_age_seconds),
            max_change_pct=float(max_change_pct),
            detail_message=(
                "현재가 timestamp 가 없어 신선도를 평가할 수 없습니다. "
                "BUY 를 차단합니다."
            ),
        )

    # 5. now - ts > max_age_seconds.
    age = _age_seconds(now_dt, ts_dt)
    # ts_dt 가 None 가 아니므로 age 도 None 일 수 없음.
    assert age is not None

    if int(max_age_seconds) > 0 and age > float(max_age_seconds):
        return PriceFreshnessResult(
            allowed=False,
            reason_code=PRICE_STALE,
            reason_message=PRICE_STALE_MESSAGE_KO,
            symbol=symbol, action=action,
            price=p,
            price_timestamp=ts_dt, now=now_dt,
            age_seconds=age,
            max_age_seconds=int(max_age_seconds),
            max_change_pct=float(max_change_pct),
            detail_message=(
                f"현재가 데이터가 {age:.0f}초 전 값입니다. "
                f"허용 한도 {int(max_age_seconds)}초를 초과하여 BUY 를 "
                f"차단합니다."
            ),
        )

    # 6. reference_price 대비 변동률 검사.
    change_pct: float | None = None
    if reference_price is not None:
        ref = float(reference_price)
        if ref > 0:
            change_pct = abs(p - ref) / ref * 100.0
            if change_pct > float(max_change_pct):
                return PriceFreshnessResult(
                    allowed=False,
                    reason_code=ABNORMAL_PRICE_MOVE,
                    reason_message=ABNORMAL_PRICE_MOVE_MESSAGE_KO,
                    symbol=symbol, action=action,
                    price=p,
                    price_timestamp=ts_dt, now=now_dt,
                    age_seconds=age,
                    max_age_seconds=int(max_age_seconds),
                    reference_price=ref,
                    price_change_pct=change_pct,
                    max_change_pct=float(max_change_pct),
                    detail_message=(
                        f"직전 기준가 대비 현재가 변동률이 "
                        f"{_format_pct(change_pct)} 입니다. 허용 한도 "
                        f"{_format_pct(float(max_change_pct))} 를 초과하여 "
                        f"BUY 를 차단합니다."
                    ),
                )
        # ref <= 0 → 급등락 검사 skip (안전 측 — invalid reference 로 분류).

    # 7. 모두 통과 → OK.
    return PriceFreshnessResult(
        allowed=True,
        reason_code=PRICE_FRESHNESS_OK,
        reason_message=PRICE_FRESHNESS_OK_MESSAGE_KO,
        symbol=symbol, action=action,
        price=p,
        price_timestamp=ts_dt, now=now_dt,
        age_seconds=age,
        max_age_seconds=int(max_age_seconds),
        reference_price=(
            float(reference_price) if reference_price is not None else None
        ),
        price_change_pct=change_pct,
        max_change_pct=float(max_change_pct),
        detail_message=(
            f"현재가 {p} (age {age:.0f}s, "
            f"max {int(max_age_seconds)}s) — 정상."
        ),
    )
