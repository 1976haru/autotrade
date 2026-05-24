"""#55 / 7-03: Paper / KIS Paper 포트폴리오 source 통일 (read-only, 표시 전용).

Dashboard / Settings / Agent 화면에서 표시되는 현금 · 총자산 · 포지션 값이
*어떤 source 에서 왔는지* 와 *조회 상태* 를 명확히 표시하기 위한 표준 snapshot.

핵심 원칙:
- **한 카드의 cash / total_asset / positions 는 *같은 source* 여야 한다.**
- **API 실패 시 cash / total_asset 을 0 으로 채우지 않는다.** (`None` = "확인 불가")
- **실제 0원** (status=OK, cash=0) 과 **조회 실패** (status=ERROR…, cash=None) 를 구분.
- Paper 모의 포트폴리오(`PAPER_SIMULATED`) 와 KIS 모의 계좌(`KIS_PAPER_ACCOUNT`) 를
  *섞지 않는다*. 섞으려 하면 `MIXED_BLOCKED`.
- secret / API key / 계좌번호 원문 carry 0건.

CLAUDE.md 가드 (정적 grep 으로 lock):
- broker / OrderExecutor / route_order / paper_trader import 0건.
- `app.ai.assist` / `app.ai.client` / `anthropic` / `openai` / `httpx` /
  `requests` / `app.core.config.get_settings` import 0건.
- KIS live endpoint 호출 0건, 주문 생성 0건, 안전 flag 변경 0건.
- `PortfolioSourceSnapshot.zero_fallback_used` 항상 False (불변),
  `is_order_signal=False` / `auto_apply_allowed=False` /
  `is_live_authorization=False` / `contains_secret=False` 불변.

**본 화면은 Paper / 모의 포트폴리오이며 실제 (실전) 계좌 잔고가 아니다.**
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable, Iterable, Optional, Sequence


class PortfolioSource(StrEnum):
    """표준 portfolio source. 어떤 값도 *주문 결정 라벨이 아니다*."""

    PAPER_SIMULATED = "PAPER_SIMULATED"        # 내부 Paper 모의 포트폴리오 (capital/ledger 기반)
    KIS_PAPER_ACCOUNT = "KIS_PAPER_ACCOUNT"    # KIS 모의투자 계좌 조회 기반
    UNAVAILABLE = "UNAVAILABLE"                # 조회 실패 / 자격 미설정 / API 오류 — 0원 아님
    MIXED_BLOCKED = "MIXED_BLOCKED"            # 서로 다른 source 를 섞으려 할 때 차단


class PortfolioStatus(StrEnum):
    """표준 조회 상태."""

    OK = "OK"
    STALE = "STALE"
    ERROR = "ERROR"
    CREDENTIALS_MISSING = "CREDENTIALS_MISSING"
    API_UNAVAILABLE = "API_UNAVAILABLE"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNKNOWN = "UNKNOWN"


# reason_code — UI 가 사용자에게 사유를 명확히 보여주는 라벨 (주문 라벨 아님).
PORTFOLIO_SOURCE_PAPER_SIMULATED = "PORTFOLIO_SOURCE_PAPER_SIMULATED"
PORTFOLIO_SOURCE_KIS_PAPER_ACCOUNT = "PORTFOLIO_SOURCE_KIS_PAPER_ACCOUNT"
PORTFOLIO_SOURCE_UNAVAILABLE = "PORTFOLIO_SOURCE_UNAVAILABLE"
PORTFOLIO_API_FAILED_NO_ZERO_FALLBACK = "PORTFOLIO_API_FAILED_NO_ZERO_FALLBACK"
PORTFOLIO_CREDENTIALS_MISSING = "PORTFOLIO_CREDENTIALS_MISSING"
PORTFOLIO_STALE_DATA = "PORTFOLIO_STALE_DATA"
PORTFOLIO_MIXED_SOURCE_BLOCKED = "PORTFOLIO_MIXED_SOURCE_BLOCKED"
PORTFOLIO_ACCOUNT_VALUE_NOT_DISPLAYED = "PORTFOLIO_ACCOUNT_VALUE_NOT_DISPLAYED"
PORTFOLIO_SECRET_REDACTED = "PORTFOLIO_SECRET_REDACTED"
PORTFOLIO_KIS_PAPER_NOT_CONFIGURED = "PORTFOLIO_KIS_PAPER_NOT_CONFIGURED"

# status 가 이 집합이면 cash / total_asset / position_count 는 *반드시 None*.
# (조회 실패를 0원으로 표시하지 않게 강제 — 본 PR 의 핵심 불변)
_NO_VALUE_STATUSES = frozenset({
    PortfolioStatus.ERROR,
    PortfolioStatus.API_UNAVAILABLE,
    PortfolioStatus.CREDENTIALS_MISSING,
    PortfolioStatus.NOT_CONFIGURED,
    PortfolioStatus.UNKNOWN,
})

# source 가 이 집합이면 값 표시 불가 (UNAVAILABLE / MIXED_BLOCKED).
_NO_VALUE_SOURCES = frozenset({
    PortfolioSource.UNAVAILABLE,
    PortfolioSource.MIXED_BLOCKED,
})

_MESSAGES_KO = {
    PORTFOLIO_SOURCE_PAPER_SIMULATED:
        "Paper 모의 포트폴리오 기준입니다. 실제 계좌 잔고가 아닙니다.",
    PORTFOLIO_SOURCE_KIS_PAPER_ACCOUNT:
        "KIS 모의 계좌 기준입니다. 실전 계좌가 아닙니다.",
    PORTFOLIO_SOURCE_UNAVAILABLE:
        "포트폴리오를 조회할 수 없습니다. 실제 잔고 0원이 아닙니다.",
    PORTFOLIO_API_FAILED_NO_ZERO_FALLBACK:
        "조회 실패: 실제 잔고 0원이 아닙니다. 잠시 후 다시 확인하세요.",
    PORTFOLIO_CREDENTIALS_MISSING:
        "KIS 모의 계좌 자격이 설정되지 않아 조회할 수 없습니다. 잔고 0원이 아닙니다.",
    PORTFOLIO_STALE_DATA:
        "마지막 조회 값이 오래되었습니다(STALE). 최신 값이 아닐 수 있습니다.",
    PORTFOLIO_MIXED_SOURCE_BLOCKED:
        "서로 다른 source 의 값을 한 카드에 섞을 수 없습니다(차단).",
    PORTFOLIO_KIS_PAPER_NOT_CONFIGURED:
        "KIS 모의 계좌 잔고 조회가 아직 연결되지 않았습니다. 잔고 0원이 아닙니다.",
}

# secret 으로 의심되는 position/계좌 필드 키 — KIS fetcher 결과에서 *제거*.
_SENSITIVE_KEY = re.compile(
    r"(secret|app_?key|access_?token|api_?key|account_?no|account_?number|"
    r"password|token|product_?code)",
    re.IGNORECASE,
)

# UI 가 표시할 position 필드 화이트리스트 (그 외는 carry 안 함).
_POSITION_KEYS = (
    "symbol", "strategy", "quantity", "average_price", "current_price",
    "market_value", "cost_basis", "unrealized_pnl", "unrealized_pnl_pct",
    "realized_pnl", "portfolio_weight_pct", "max_symbol_weight_pct",
    "symbol_weight_status",
)


def _now_iso(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.isoformat()


def _safe_position(p: Any) -> dict[str, Any]:
    """position dict 에서 화이트리스트 필드만 carry — secret 키 0건."""
    if not isinstance(p, dict):
        return {}
    return {k: p.get(k) for k in _POSITION_KEYS if k in p}


def _sanitize_positions(positions: Optional[Iterable[Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(_safe_position(p) for p in (positions or []))


@dataclass(frozen=True)
class PortfolioSourceSnapshot:
    """source 가 태깅된 단일 포트폴리오 snapshot — UI 안전 payload.

    cash / total_asset / position_count 는 status 가 OK / STALE 일 때만 숫자.
    그 외(실패/미설정)는 *반드시 None* — 0 으로 채우지 않는다.
    """

    source: str
    status: str
    reason_code: str
    message_ko: str = ""

    # 같은 source 에서 온 cash / total_asset / positions (조회 실패 시 None — 0 아님).
    cash: int | None = None
    total_asset: int | None = None
    position_count: int | None = None
    positions: tuple[dict[str, Any], ...] = ()
    last_updated: str | None = None

    # 표시용 부가 정보 (조회 실패 시 None).
    starting_cash: int | None = None
    position_value: int | None = None
    unrealized_pnl: int | None = None
    invested: int | None = None
    realized_pnl: int | None = None

    # 조회 실패여도 어떤 source 를 *시도* 했는지 (UI 섹션 라벨용).
    attempted_source: str | None = None
    detail_messages: tuple[str, ...] = ()

    # 불변 — 본 PR 의 핵심 가드.
    zero_fallback_used: bool = False
    is_order_signal: bool = False
    auto_apply_allowed: bool = False
    is_live_authorization: bool = False
    contains_secret: bool = False
    is_paper_only: bool = True

    def __post_init__(self) -> None:
        # 1) source / status enum 유효성.
        valid_sources = {s.value for s in PortfolioSource}
        valid_statuses = {s.value for s in PortfolioStatus}
        if self.source not in valid_sources:
            raise ValueError(f"invalid portfolio source: {self.source!r}")
        if self.status not in valid_statuses:
            raise ValueError(f"invalid portfolio status: {self.status!r}")

        # 2) 안전 불변.
        if self.zero_fallback_used is not False:
            raise ValueError("zero_fallback_used must be False (no 0원 fallback)")
        if self.is_order_signal is not False:
            raise ValueError("is_order_signal must be False")
        if self.auto_apply_allowed is not False:
            raise ValueError("auto_apply_allowed must be False")
        if self.is_live_authorization is not False:
            raise ValueError("is_live_authorization must be False")
        if self.contains_secret is not False:
            raise ValueError("contains_secret must be False")
        if self.is_paper_only is not True:
            raise ValueError("is_paper_only must be True (live 잔고 표시 금지)")

        # 3) **조회 실패 → 값 None 강제** (0 으로 채우면 ValueError).
        no_value = (
            self.status in _NO_VALUE_STATUSES
            or self.source in _NO_VALUE_SOURCES
        )
        if no_value:
            for name in ("cash", "total_asset", "position_count",
                         "starting_cash", "position_value", "unrealized_pnl",
                         "invested", "realized_pnl"):
                if getattr(self, name) is not None:
                    raise ValueError(
                        f"{name} must be None when status={self.status} / "
                        f"source={self.source} (조회 실패를 0원/숫자로 표시 금지)"
                    )
            if self.positions:
                raise ValueError(
                    "positions must be empty when status/source indicates failure"
                )

    @property
    def value_available(self) -> bool:
        """cash / total_asset 를 숫자로 표시해도 되는지."""
        return self.cash is not None and self.total_asset is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source":             self.source,
            "status":             self.status,
            "reason_code":        self.reason_code,
            "message_ko":         self.message_ko,
            "cash":               self.cash,
            "total_asset":        self.total_asset,
            "position_count":     self.position_count,
            "positions":          [dict(p) for p in self.positions],
            "last_updated":       self.last_updated,
            "starting_cash":      self.starting_cash,
            "position_value":     self.position_value,
            "unrealized_pnl":     self.unrealized_pnl,
            "invested":           self.invested,
            "realized_pnl":       self.realized_pnl,
            "attempted_source":   self.attempted_source,
            "detail_messages":    list(self.detail_messages),
            "value_available":    self.value_available,
            "zero_fallback_used": False,
            "is_order_signal":    False,
            "auto_apply_allowed": False,
            "is_live_authorization": False,
            "contains_secret":    False,
            "is_paper_only":      True,
        }


def unavailable_snapshot(
    *,
    attempted_source: str,
    status: PortfolioStatus,
    reason_code: str,
    message_ko: str | None = None,
    detail_messages: Sequence[str] = (),
    last_updated: str | None = None,
) -> PortfolioSourceSnapshot:
    """조회 실패/미설정 snapshot — source=UNAVAILABLE, 값 전부 None (0 아님)."""
    return PortfolioSourceSnapshot(
        source=PortfolioSource.UNAVAILABLE.value,
        status=status.value,
        reason_code=reason_code,
        message_ko=message_ko if message_ko is not None else _MESSAGES_KO.get(reason_code, ""),
        attempted_source=str(attempted_source),
        detail_messages=tuple(detail_messages),
        last_updated=last_updated,
    )


def mixed_blocked_snapshot(sources: Sequence[str]) -> PortfolioSourceSnapshot:
    """서로 다른 source 를 한 카드에 섞으려 할 때 차단하는 snapshot."""
    label = ", ".join(sorted({str(s) for s in sources}))
    return PortfolioSourceSnapshot(
        source=PortfolioSource.MIXED_BLOCKED.value,
        status=PortfolioStatus.ERROR.value,
        reason_code=PORTFOLIO_MIXED_SOURCE_BLOCKED,
        message_ko=_MESSAGES_KO[PORTFOLIO_MIXED_SOURCE_BLOCKED] + f" (sources: {label})",
        detail_messages=(f"blocked_sources={label}",),
    )


def assert_single_source(
    snapshots: Sequence[PortfolioSourceSnapshot],
) -> PortfolioSourceSnapshot | None:
    """여러 snapshot 이 *서로 다른* source 의 값을 동시에 들고 있으면 MIXED_BLOCKED.

    한 카드에서 cash/total_asset 을 *합산* 하려 할 때 호출. 값을 가진(value_available)
    snapshot 의 source 가 2개 이상이면 차단 snapshot 반환, 아니면 None.
    """
    sources = {
        s.source for s in snapshots
        if s.value_available and s.source not in _NO_VALUE_SOURCES
    }
    if len(sources) > 1:
        return mixed_blocked_snapshot(tuple(sources))
    return None


def _default_paper_builder(db, *, last_prices=None, now=None):
    # lazy import — build_portfolio_state 는 broker 를 import 하지 않지만,
    # 본 모듈을 DB 없이도 import 가능하게 유지하기 위해 함수 안에서 import.
    from app.auto_paper.portfolio_state import build_portfolio_state
    return build_portfolio_state(db, last_prices=last_prices, now=now)


def build_paper_simulated_snapshot(
    db,
    *,
    last_prices: dict[str, int] | None = None,
    now: Optional[datetime] = None,
    builder: Optional[Callable[..., Any]] = None,
) -> PortfolioSourceSnapshot:
    """내부 Paper 모의 포트폴리오 snapshot (source=PAPER_SIMULATED).

    조회 실패 시 0원 fallback 대신 source=UNAVAILABLE + cash/total_asset=None.
    """
    builder = builder or _default_paper_builder
    try:
        snap = builder(db, last_prices=last_prices, now=now)
    except Exception as exc:  # noqa: BLE001 — 실패를 0원으로 표시하지 않게 흡수.
        return unavailable_snapshot(
            attempted_source=PortfolioSource.PAPER_SIMULATED.value,
            status=PortfolioStatus.API_UNAVAILABLE,
            reason_code=PORTFOLIO_API_FAILED_NO_ZERO_FALLBACK,
            detail_messages=(f"{type(exc).__name__}",),
            last_updated=_now_iso(now),
        )

    positions = _sanitize_positions(getattr(snap, "positions", None))
    return PortfolioSourceSnapshot(
        source=PortfolioSource.PAPER_SIMULATED.value,
        status=PortfolioStatus.OK.value,
        reason_code=PORTFOLIO_SOURCE_PAPER_SIMULATED,
        message_ko=_MESSAGES_KO[PORTFOLIO_SOURCE_PAPER_SIMULATED],
        cash=int(snap.current_cash),
        total_asset=int(snap.total_equity),
        position_count=int(snap.position_count),
        positions=positions,
        last_updated=getattr(snap, "last_event_at", None) or _now_iso(now),
        starting_cash=int(snap.starting_cash),
        position_value=int(snap.total_position_value),
        unrealized_pnl=int(snap.total_unrealized_pnl),
        invested=int(getattr(snap, "invested_krw", 0)),
        realized_pnl=int(getattr(snap, "realized_pnl", 0)),
        attempted_source=PortfolioSource.PAPER_SIMULATED.value,
    )


def build_kis_paper_account_snapshot(
    *,
    credentials_present: bool = True,
    balance_fetcher: Optional[Callable[[], dict[str, Any]]] = None,
    now: Optional[datetime] = None,
) -> PortfolioSourceSnapshot:
    """KIS 모의 계좌 포트폴리오 snapshot (source=KIS_PAPER_ACCOUNT).

    `balance_fetcher` 는 KIS *모의* 계좌 잔고를 read-only 로 조회하는 콜백(선택).
    본 모듈은 broker 를 import 하지 않으므로, 실제 조회는 호출자가 주입한다.

    - 자격 미설정 → CREDENTIALS_MISSING (cash=None, 0 아님).
    - fetcher 미주입 → NOT_CONFIGURED (cash=None, 0 아님).
    - fetcher 예외 → API_UNAVAILABLE (cash=None, 0 아님).
    - 성공 → KIS_PAPER_ACCOUNT (값 carry, secret 키 제거).
    """
    if not credentials_present:
        return unavailable_snapshot(
            attempted_source=PortfolioSource.KIS_PAPER_ACCOUNT.value,
            status=PortfolioStatus.CREDENTIALS_MISSING,
            reason_code=PORTFOLIO_CREDENTIALS_MISSING,
            last_updated=_now_iso(now),
        )
    if balance_fetcher is None:
        return unavailable_snapshot(
            attempted_source=PortfolioSource.KIS_PAPER_ACCOUNT.value,
            status=PortfolioStatus.NOT_CONFIGURED,
            reason_code=PORTFOLIO_KIS_PAPER_NOT_CONFIGURED,
            last_updated=_now_iso(now),
        )
    try:
        data = balance_fetcher() or {}
    except Exception as exc:  # noqa: BLE001 — 실패를 0원으로 표시하지 않게 흡수.
        return unavailable_snapshot(
            attempted_source=PortfolioSource.KIS_PAPER_ACCOUNT.value,
            status=PortfolioStatus.API_UNAVAILABLE,
            reason_code=PORTFOLIO_API_FAILED_NO_ZERO_FALLBACK,
            detail_messages=(f"{type(exc).__name__}",),
            last_updated=_now_iso(now),
        )

    cash = data.get("cash")
    total_asset = data.get("total_asset")
    # 값이 없으면 0 으로 채우지 않고 UNAVAILABLE 처리.
    if cash is None or total_asset is None:
        return unavailable_snapshot(
            attempted_source=PortfolioSource.KIS_PAPER_ACCOUNT.value,
            status=PortfolioStatus.API_UNAVAILABLE,
            reason_code=PORTFOLIO_API_FAILED_NO_ZERO_FALLBACK,
            last_updated=_now_iso(now),
        )

    positions = _sanitize_positions(data.get("positions"))
    return PortfolioSourceSnapshot(
        source=PortfolioSource.KIS_PAPER_ACCOUNT.value,
        status=PortfolioStatus.OK.value,
        reason_code=PORTFOLIO_SOURCE_KIS_PAPER_ACCOUNT,
        message_ko=_MESSAGES_KO[PORTFOLIO_SOURCE_KIS_PAPER_ACCOUNT],
        cash=int(cash),
        total_asset=int(total_asset),
        position_count=(
            int(data["position_count"]) if data.get("position_count") is not None
            else len(positions)
        ),
        positions=positions,
        last_updated=data.get("last_updated") or _now_iso(now),
        position_value=(int(data["position_value"]) if data.get("position_value") is not None else None),
        unrealized_pnl=(int(data["unrealized_pnl"]) if data.get("unrealized_pnl") is not None else None),
        attempted_source=PortfolioSource.KIS_PAPER_ACCOUNT.value,
    )


@dataclass(frozen=True)
class PortfolioSourceReport:
    """Paper + KIS Paper snapshot 을 *섞지 않고* 함께 carry 하는 리포트."""

    paper_simulated: PortfolioSourceSnapshot
    kis_paper_account: PortfolioSourceSnapshot
    primary_source: str
    generated_at: str
    is_order_signal: bool = False
    is_live_authorization: bool = False
    contains_secret: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_source":      self.primary_source,
            "generated_at":        self.generated_at,
            "snapshots": [
                self.paper_simulated.to_dict(),
                self.kis_paper_account.to_dict(),
            ],
            "paper_simulated":     self.paper_simulated.to_dict(),
            "kis_paper_account":   self.kis_paper_account.to_dict(),
            "is_order_signal":     False,
            "is_live_authorization": False,
            "contains_secret":     False,
            "advisory_note": (
                "현금 / 총자산 / 포지션 값은 같은 source 에서만 표시되며, 조회 실패는 "
                "0원이 아니라 '확인 불가' 로 표시됩니다. 실제 (실전) 계좌 잔고가 아닙니다."
            ),
        }


def build_portfolio_source_report(
    db,
    *,
    kis_credentials_present: bool = False,
    kis_balance_fetcher: Optional[Callable[[], dict[str, Any]]] = None,
    last_prices: dict[str, int] | None = None,
    now: Optional[datetime] = None,
    paper_builder: Optional[Callable[..., Any]] = None,
) -> PortfolioSourceReport:
    """Paper + KIS Paper snapshot 을 각각 산출 (*섞지 않음*)."""
    paper = build_paper_simulated_snapshot(
        db, last_prices=last_prices, now=now, builder=paper_builder,
    )
    kis = build_kis_paper_account_snapshot(
        credentials_present=kis_credentials_present,
        balance_fetcher=kis_balance_fetcher,
        now=now,
    )
    # primary 는 값이 있는 Paper 모의 — 없으면 UNAVAILABLE.
    primary = paper.source if paper.value_available else PortfolioSource.UNAVAILABLE.value
    return PortfolioSourceReport(
        paper_simulated=paper,
        kis_paper_account=kis,
        primary_source=primary,
        generated_at=_now_iso(now),
    )


__all__ = [
    "PortfolioSource",
    "PortfolioStatus",
    "PORTFOLIO_SOURCE_PAPER_SIMULATED",
    "PORTFOLIO_SOURCE_KIS_PAPER_ACCOUNT",
    "PORTFOLIO_SOURCE_UNAVAILABLE",
    "PORTFOLIO_API_FAILED_NO_ZERO_FALLBACK",
    "PORTFOLIO_CREDENTIALS_MISSING",
    "PORTFOLIO_STALE_DATA",
    "PORTFOLIO_MIXED_SOURCE_BLOCKED",
    "PORTFOLIO_ACCOUNT_VALUE_NOT_DISPLAYED",
    "PORTFOLIO_SECRET_REDACTED",
    "PORTFOLIO_KIS_PAPER_NOT_CONFIGURED",
    "PortfolioSourceSnapshot",
    "PortfolioSourceReport",
    "unavailable_snapshot",
    "mixed_blocked_snapshot",
    "assert_single_source",
    "build_paper_simulated_snapshot",
    "build_kis_paper_account_snapshot",
    "build_portfolio_source_report",
]
