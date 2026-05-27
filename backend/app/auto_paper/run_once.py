"""강제 진단 run-once — AI Paper 자동매매 파이프라인 *전체* 를 1회 실행.

자동매매가 "진짜 연결되어 있는지" 를 장 상황 / 시세 도착 여부에만 의존하지
않고 *언제든* 검증할 수 있는 진단 함수. 실제 코드의 universe → 시세 검증 →
전략 신호 → 수량 계산(P-08) → Paper 현금(P-07) → 권한(PermissionGate paper
정책) → 가상 주문 후보 단계를 *모두 통과* 시키며, 어느 단계에서 멈추든 *정확한
reason_code* 를 남긴다.

핵심 목표 (사용자 요청서 §8):
- "버튼을 눌렀는데 아무 일도 안 일어나는 상태" 제거 — run-once 는 항상 결과 +
  reason_code 를 반환하고 ledger 에 기록한다 (*거래 0건은 가능, 기록 0건은 불가*).
- 매수하지 못해도 *왜 못 했는지* 가 반드시 남는다.

절대 금지 (CLAUDE.md 절대 원칙 1~5 + 사용자 요청서):
- **broker live order 호출 0건** — 본 모듈은 `app.brokers.*` /
  `app.execution.executor` / `app.execution.order_router` / `OrderExecutor` /
  `route_order` 를 *어떤 경로로도 import 하지 않는다* (정적 grep 가드).
- KIS / Anthropic / OpenAI / httpx / requests import 0건.
- 안전 flag (`ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `KIS_IS_PAPER`)
  mutate 0건.
- `RunOnceResult.is_order_signal=False` / `is_live_authorization=False` /
  `auto_apply_allowed=False` 영구.
- dry_run=True 면 ledger 에 *실제 체결처럼* 반영하지 않는다 (NO_OP heartbeat
  로만 기록). dry_run=False 라도 *Paper 가상 주문 후보* 까지만 — 실거래 불가.
- mock 시세는 *진단 모드에서만* 사용. KIS provider 실패 시 mock 으로 silent
  swap 하지 않는다 — `force_mock_market_data=True` 명시 시에만 mock 생성.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterable

from app.auto_paper.capital_state import (
    CashCheckVerdict,
    check_buy_cash_sufficient,
)
from app.auto_paper.events import DecisionAction, PaperFillStatus
from app.auto_paper.ledger import record_paper_event
from app.auto_paper.position_sizer import (
    QuantityByPriceVerdict,
    compute_paper_quantity_by_price,
)
from app.market.freshness import (
    ABNORMAL_PRICE_MOVE,
    DEFAULT_MAX_AGE_SECONDS,
    DEFAULT_MAX_CHANGE_PCT,
    INVALID_PRICE,
    PRICE_FRESHNESS_OK,
    PRICE_MISSING,
    PRICE_STALE,
    check_price_freshness,
)
from app.scheduler.market_clock import current_market_phase
from app.universe.default_universe import get_default_universe


# ─────────────────────────────────────────────────────────────────────────────
# Reason / result codes
# ─────────────────────────────────────────────────────────────────────────────


class RunOnceResultCode(StrEnum):
    """run-once 진단 최종 결과 코드 — 사용자 요청서 §2/§8 reason 매핑.

    *성공* 2종 (VIRTUAL_ORDER_CANDIDATE_CREATED / PAPER_DRY_RUN_OK) 외에는 모두
    "왜 매수하지 못했는가" 의 구체적 사유. BUY/SELL/HOLD signal 값 0개.
    """

    # 성공.
    VIRTUAL_ORDER_CANDIDATE_CREATED = "VIRTUAL_ORDER_CANDIDATE_CREATED"
    PAPER_DRY_RUN_OK                = "PAPER_DRY_RUN_OK"

    # 후보 / 전략 단계.
    STRATEGY_ENGINE_NOT_CONNECTED   = "STRATEGY_ENGINE_NOT_CONNECTED"
    NO_CANDIDATE                    = "NO_CANDIDATE"
    NO_STRATEGY_SIGNAL              = "NO_STRATEGY_SIGNAL"

    # 시세 단계.
    NO_MARKET_DATA                  = "NO_MARKET_DATA"
    INVALID_PRICE                   = "INVALID_PRICE"
    PRICE_STALE                     = "PRICE_STALE"
    ABNORMAL_PRICE_MOVE             = "ABNORMAL_PRICE_MOVE"

    # 수량 / 현금 단계.
    MIN_LOT_NOT_AFFORDABLE          = "MIN_LOT_NOT_AFFORDABLE"
    INSUFFICIENT_PAPER_CASH         = "INSUFFICIENT_PAPER_CASH"

    # 권한 단계.
    BLOCKED_BY_PERMISSION_GATE      = "BLOCKED_BY_PERMISSION_GATE"
    PAPER_EXECUTION_DISABLED        = "PAPER_EXECUTION_DISABLED"

    # 기타.
    UNKNOWN_ERROR                   = "UNKNOWN_ERROR"


_SUCCESS_CODES = frozenset({
    RunOnceResultCode.VIRTUAL_ORDER_CANDIDATE_CREATED,
    RunOnceResultCode.PAPER_DRY_RUN_OK,
})


_RESULT_MESSAGE_KO: dict[RunOnceResultCode, str] = {
    RunOnceResultCode.VIRTUAL_ORDER_CANDIDATE_CREATED:
        "Paper 가상 주문 후보가 정상 생성되었습니다 (실거래 아님).",
    RunOnceResultCode.PAPER_DRY_RUN_OK:
        "dry-run 통과 — 파이프라인 전 단계 정상 (ledger 체결 반영 안 함).",
    RunOnceResultCode.STRATEGY_ENGINE_NOT_CONNECTED:
        "전략 엔진이 연결되지 않아 후보를 평가할 수 없습니다.",
    RunOnceResultCode.NO_CANDIDATE:
        "후보 종목이 없어 자동매매가 진행되지 않았습니다.",
    RunOnceResultCode.NO_STRATEGY_SIGNAL:
        "후보는 있으나 전략이 매수 신호를 내지 않았습니다.",
    RunOnceResultCode.NO_MARKET_DATA:
        "현재가 데이터가 없어 매수를 진행할 수 없습니다.",
    RunOnceResultCode.INVALID_PRICE:
        "현재가가 0 이하 / 비정상이라 매수를 차단했습니다.",
    RunOnceResultCode.PRICE_STALE:
        "현재가가 오래되어(stale) 매수를 차단했습니다.",
    RunOnceResultCode.ABNORMAL_PRICE_MOVE:
        "가격 급등락이 감지되어 매수를 차단했습니다.",
    RunOnceResultCode.MIN_LOT_NOT_AFFORDABLE:
        "종목당 투자금으로 1주도 살 수 없어 매수를 진행하지 않았습니다 (고가주).",
    RunOnceResultCode.INSUFFICIENT_PAPER_CASH:
        "남은 Paper 현금이 부족하여 매수를 차단했습니다.",
    RunOnceResultCode.BLOCKED_BY_PERMISSION_GATE:
        "PermissionGate 가 가상 실행을 차단했습니다.",
    RunOnceResultCode.PAPER_EXECUTION_DISABLED:
        "PAPER 가상 실행이 비활성화되어 주문 후보를 생성하지 않았습니다.",
    RunOnceResultCode.UNKNOWN_ERROR:
        "알 수 없는 오류가 발생했습니다 (stage 와 상세 사유 확인).",
}


def result_message_ko(code: RunOnceResultCode) -> str:
    return _RESULT_MESSAGE_KO.get(code, code.value)


# ─────────────────────────────────────────────────────────────────────────────
# Stage trace + result dataclasses
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PipelineStage:
    """단일 파이프라인 stage 결과 — UI 가 그대로 표시 가능한 safe payload."""

    stage:       str
    ok:          bool
    reason_code: str
    message:     str
    detail:      dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage":       self.stage,
            "ok":          bool(self.ok),
            "reason_code": self.reason_code,
            "message":     self.message,
            "detail":      dict(self.detail),
        }


@dataclass(frozen=True)
class RunOnceResult:
    """run-once 진단 결과 — *advisory*, broker 호출 0건.

    `is_order_signal=False` / `is_live_authorization=False` /
    `auto_apply_allowed=False` 영구 (dataclass `__post_init__` ValueError 가드).
    """

    result_code:    RunOnceResultCode
    ok:             bool                 # 성공 (가상 후보 생성 / dry-run 통과) 여부
    dry_run:        bool
    symbol:         str | None
    price:          float | None
    quantity:       int
    notional_krw:   int
    reason_message: str
    stages:         tuple[PipelineStage, ...]
    universe_source: str
    universe_count:  int
    market_data_provider: str
    market_data_is_mock:  bool
    market_phase:    str
    recorded_event_id: str | None        = None
    metadata:        dict[str, Any]      = field(default_factory=dict)
    # V2: 판단에 쓰인 시세 출처 — "mock" / "yfinance" / "kis". 진단 파이프라인은
    # 기본 mock. KIS 실시간 경로는 driver_bridge 스캔이 담당하며 본 값은 표시용.
    price_source:    str                 = "mock"
    price_is_stale:  bool                = False

    # 절대 invariant.
    is_order_signal:       bool = False
    is_live_authorization: bool = False
    auto_apply_allowed:    bool = False
    broker_order_sent:     bool = False   # 영구 False — broker 호출 0건 증명

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("RunOnceResult.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("RunOnceResult.is_live_authorization must be False")
        if self.auto_apply_allowed is not False:
            raise ValueError("RunOnceResult.auto_apply_allowed must be False")
        if self.broker_order_sent is not False:
            raise ValueError("RunOnceResult.broker_order_sent must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_code":          self.result_code.value,
            "ok":                   bool(self.ok),
            "dry_run":              bool(self.dry_run),
            "symbol":               self.symbol,
            "price":                self.price,
            "quantity":             int(self.quantity),
            "notional_krw":         int(self.notional_krw),
            "reason_message":       self.reason_message,
            "stages":               [s.to_dict() for s in self.stages],
            "universe_source":      self.universe_source,
            "universe_count":       int(self.universe_count),
            "market_data_provider": self.market_data_provider,
            "market_data_is_mock":  bool(self.market_data_is_mock),
            "price_source":         self.price_source,
            "price_is_stale":       bool(self.price_is_stale),
            "market_phase":         self.market_phase,
            "recorded_event_id":    self.recorded_event_id,
            "metadata":             dict(self.metadata),
            "is_order_signal":       self.is_order_signal,
            "is_live_authorization": self.is_live_authorization,
            "auto_apply_allowed":    self.auto_apply_allowed,
            "broker_order_sent":     self.broker_order_sent,
            "advisory_disclaimer": (
                "본 결과는 PAPER 진단 — broker / route_order / OrderExecutor "
                "호출 0건. 가상 주문 후보까지만 생성되며 실거래는 어떤 경로로도 "
                "진행되지 않습니다."
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Mock market data (진단 모드 전용 — 결정론적)
# ─────────────────────────────────────────────────────────────────────────────


def mock_price_for(symbol: str) -> float:
    """진단 모드 전용 결정론적 mock 현재가.

    symbol 코드의 해시 기반으로 50,000~150,000 KRW 사이 *고정* 가격을 만든다 —
    동일 symbol 은 항상 같은 mock 가격 (테스트 재현성). 실 시세가 아니며 진단
    파이프라인 통과 검증용.
    """
    digits = "".join(ch for ch in str(symbol) if ch.isdigit())
    seed = int(digits) if digits else (abs(hash(symbol)) % 100_000)
    return float(50_000 + (seed % 100_001))   # 50,000 ~ 150,000


# ─────────────────────────────────────────────────────────────────────────────
# Paper 권한 정책 (PermissionGate paper-side) — broker import 0건
# ─────────────────────────────────────────────────────────────────────────────


def evaluate_paper_execution_permission(
    *,
    dry_run: bool,
    paper_virtual_execution_enabled: bool = True,
    force_block: bool = False,
) -> tuple[bool, RunOnceResultCode, str]:
    """PAPER 가상 실행 권한 평가 — *live 는 항상 차단*.

    정책 (사용자 요청서 §9):
      - LIVE 주문 / broker 주문은 *무조건 차단* (본 함수는 broker 를 호출하지
        않으므로 구조적으로 불가능 — `is_live_authorization=False`).
      - AI 자동주문이 OFF 여도 diagnostic dry-run / Paper 가상 후보 생성은
        허용 가능 (Paper 가상 실행은 AI 주문 권한과 별개).
      - `paper_virtual_execution_enabled=False` → PAPER_EXECUTION_DISABLED.
      - `force_block=True` (운영자/테스트가 명시 차단) → BLOCKED_BY_PERMISSION_GATE.

    Returns:
        (allowed, result_code, message). allowed=True 면 result_code 는
        PAPER_DRY_RUN_OK / VIRTUAL_ORDER_CANDIDATE_CREATED 중 caller 가 dry_run
        으로 결정.
    """
    if force_block:
        return (
            False,
            RunOnceResultCode.BLOCKED_BY_PERMISSION_GATE,
            "PermissionGate 가 가상 실행을 명시적으로 차단했습니다.",
        )
    if not paper_virtual_execution_enabled:
        return (
            False,
            RunOnceResultCode.PAPER_EXECUTION_DISABLED,
            "PAPER 가상 실행이 비활성화되어 있습니다.",
        )
    if dry_run:
        return (True, RunOnceResultCode.PAPER_DRY_RUN_OK,
                "PAPER dry-run 허용 — 가상 후보 미생성, 파이프라인 검증만.")
    return (True, RunOnceResultCode.VIRTUAL_ORDER_CANDIDATE_CREATED,
            "PAPER 가상 주문 후보 생성 허용 (실거래 아님).")


# ─────────────────────────────────────────────────────────────────────────────
# Main entry — run_paper_pipeline_once
# ─────────────────────────────────────────────────────────────────────────────


def run_paper_pipeline_once(
    *,
    symbol:                 str | None             = None,
    price:                  float | int | None     = None,
    quantity:               int | None             = None,
    force_mock_market_data: bool                   = False,
    dry_run:                bool                   = True,
    # universe.
    user_symbols:           Iterable[str] | None   = None,
    # 전략 단계 시뮬레이션 입력.
    strategy:               str                    = "diagnostic",
    strategy_engine_connected: bool                = True,
    signal_present:         bool                   = True,
    confidence:             float | None           = 0.75,
    # 수량 / 현금.
    per_symbol_cap_krw:     int | None             = None,
    available_cash_krw:     int | None             = None,
    # 시세 검증.
    market_data_provider:   str                    = "mock",
    price_timestamp:        datetime | str | None  = None,
    reference_price:        float | int | None     = None,
    max_age_seconds:        int                    = DEFAULT_MAX_AGE_SECONDS,
    max_change_pct:         float                  = DEFAULT_MAX_CHANGE_PCT,
    # 권한.
    paper_virtual_execution_enabled: bool          = True,
    force_block_permission: bool                   = False,
    # 기록.
    record:                 bool                   = True,
    now:                    datetime | None        = None,
) -> RunOnceResult:
    """AI Paper 자동매매 파이프라인을 1회 강제 실행 — 항상 reason_code 반환.

    단계 (첫 차단 단계에서 멈추고 그 reason_code 반환):
      1. UNIVERSE        — 후보 종목군 해결 (user_symbols 또는 fallback 50)
      2. STRATEGY        — 전략 엔진 연결 / 후보 / 신호 여부
      3. MARKET_DATA     — 현재가 확보 (force_mock 시 mock 생성)
      4. PRICE_VALIDATION— 신선도 / 비정상 / 급등락 검사 (#P-14)
      5. SIZING          — 종목당 한도 기준 수량 계산 (#P-08)
      6. CASH            — 남은 Paper 현금 충분성 (#P-07)
      7. PERMISSION      — PAPER 가상 실행 권한 (live 항상 차단)
      8. FINAL           — dry_run → PAPER_DRY_RUN_OK / 아니면 가상 후보 생성

    어느 단계에서 멈추든 결과를 ledger 에 NO_OP heartbeat 로 기록 (record=True).
    *거래 0건은 가능하나 기록 0건은 불가* — 본 함수는 항상 결과를 남긴다.

    broker / route_order / OrderExecutor 호출 0건. 실거래 어떤 경로로도 불가.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    stages: list[PipelineStage] = []
    phase = current_market_phase(now)

    # 결과를 만들고 (옵션) 기록하는 helper.
    def _finalize(
        code:         RunOnceResultCode,
        *,
        ok:           bool,
        sym:          str | None,
        px:           float | None,
        qty:          int,
        notional:     int,
        uni,
        is_mock:      bool,
        extra_meta:   dict[str, Any] | None = None,
    ) -> RunOnceResult:
        msg = result_message_ko(code)
        event_id: str | None = None
        if record:
            # 항상 NO_OP heartbeat 로 기록 — trade event 가 아니므로 어떤
            # loop_state 에서도 ledger state guard 에 걸리지 않는다 (dry_run /
            # 실패 / 성공 모두 동일). "기록 0건 불가" 보장.
            try:
                ev = record_paper_event(
                    loop_state="RUNNING",  # NO_OP 은 state 무관하게 허용됨
                    strategy=strategy,
                    symbol=(sym or "DIAGNOSTIC"),
                    decision_action=DecisionAction.NO_OP,
                    confidence=confidence,
                    reason=f"[run-once] {code.value}: {msg}",
                    risk_flags=([] if ok else [code.value]),
                    paper_fill_status=PaperFillStatus.NA,
                    metadata={
                        "run_once":       True,
                        "result_code":    code.value,
                        "dry_run":        bool(dry_run),
                        "quantity":       int(qty),
                        "notional_krw":   int(notional),
                        "broker_order_sent": False,
                    },
                )
                event_id = ev.event_id
            except Exception:  # noqa: BLE001 — 기록 실패가 진단 자체를 깨지 않게.
                event_id = None
        meta = {
            "strategy":                 strategy,
            "strategy_engine_connected": bool(strategy_engine_connected),
            "signal_present":           bool(signal_present),
            "per_symbol_cap_krw":       per_symbol_cap_krw,
            "available_cash_krw":       available_cash_krw,
        }
        if extra_meta:
            meta.update(extra_meta)
        return RunOnceResult(
            result_code=code,
            ok=ok,
            dry_run=bool(dry_run),
            symbol=sym,
            price=px,
            quantity=int(qty),
            notional_krw=int(notional),
            reason_message=msg,
            stages=tuple(stages),
            universe_source=uni.source.value,
            universe_count=uni.count,
            market_data_provider=str(market_data_provider),
            market_data_is_mock=bool(is_mock),
            price_source=("mock" if is_mock else str(market_data_provider)),
            market_phase=phase.value,
            recorded_event_id=event_id,
            metadata=meta,
        )

    # ── 1. UNIVERSE ───────────────────────────────────────────────────────
    uni = get_default_universe(user_symbols)
    resolved_symbol = symbol or (uni.symbols[0] if uni.count > 0 else None)
    stages.append(PipelineStage(
        stage="UNIVERSE",
        ok=uni.count > 0,
        reason_code=("OK" if uni.count > 0 else RunOnceResultCode.NO_CANDIDATE.value),
        message=f"universe {uni.source.value} · {uni.count}개",
        detail={"source": uni.source.value, "count": uni.count,
                "fallback_used": uni.fallback_used,
                "resolved_symbol": resolved_symbol},
    ))

    # ── 2. STRATEGY ───────────────────────────────────────────────────────
    if not strategy_engine_connected:
        stages.append(PipelineStage(
            stage="STRATEGY", ok=False,
            reason_code=RunOnceResultCode.STRATEGY_ENGINE_NOT_CONNECTED.value,
            message="전략 엔진 미연동",
        ))
        return _finalize(
            RunOnceResultCode.STRATEGY_ENGINE_NOT_CONNECTED,
            ok=False, sym=resolved_symbol, px=None, qty=0, notional=0,
            uni=uni, is_mock=False,
        )
    if resolved_symbol is None:
        stages.append(PipelineStage(
            stage="STRATEGY", ok=False,
            reason_code=RunOnceResultCode.NO_CANDIDATE.value,
            message="후보 종목 없음",
        ))
        return _finalize(
            RunOnceResultCode.NO_CANDIDATE,
            ok=False, sym=None, px=None, qty=0, notional=0,
            uni=uni, is_mock=False,
        )
    if not signal_present:
        stages.append(PipelineStage(
            stage="STRATEGY", ok=False,
            reason_code=RunOnceResultCode.NO_STRATEGY_SIGNAL.value,
            message="전략 신호 없음 (entry 조건 미충족)",
        ))
        return _finalize(
            RunOnceResultCode.NO_STRATEGY_SIGNAL,
            ok=False, sym=resolved_symbol, px=None, qty=0, notional=0,
            uni=uni, is_mock=False,
        )
    stages.append(PipelineStage(
        stage="STRATEGY", ok=True, reason_code="OK",
        message=f"전략 신호 있음 (confidence={confidence})",
    ))

    # ── 3. MARKET_DATA ────────────────────────────────────────────────────
    effective_price = float(price) if price is not None else None
    is_mock = False
    effective_ts = price_timestamp
    if effective_price is None:
        if force_mock_market_data:
            effective_price = mock_price_for(resolved_symbol)
            is_mock = True
            if effective_ts is None:
                effective_ts = now   # 방금 만든 mock — fresh.
            stages.append(PipelineStage(
                stage="MARKET_DATA", ok=True, reason_code="OK",
                message=f"진단 mock 현재가 {int(effective_price):,}원 생성",
                detail={"is_mock": True},
            ))
        else:
            # KIS / 실 provider 실패 시 mock 으로 silent swap 금지 — 명확히 차단.
            stages.append(PipelineStage(
                stage="MARKET_DATA", ok=False,
                reason_code=RunOnceResultCode.NO_MARKET_DATA.value,
                message=(
                    "현재가 없음 — force_mock_market_data=False 이므로 mock 으로 "
                    "대체하지 않고 차단"
                ),
                detail={"provider": market_data_provider},
            ))
            return _finalize(
                RunOnceResultCode.NO_MARKET_DATA,
                ok=False, sym=resolved_symbol, px=None, qty=0, notional=0,
                uni=uni, is_mock=False,
            )
    else:
        # 운영자가 명시한 현재가는 *지금 시점* 값으로 간주 — timestamp 미지정 시
        # now 로 채운다 (PRICE_STALE 오탐 방지). PRICE_STALE / ABNORMAL 을
        # 검증하려면 caller 가 명시적으로 과거 price_timestamp / reference_price
        # 를 전달.
        if effective_ts is None:
            effective_ts = now
        stages.append(PipelineStage(
            stage="MARKET_DATA", ok=True, reason_code="OK",
            message=f"현재가 {int(effective_price):,}원 (provider={market_data_provider})",
            detail={"is_mock": False},
        ))

    # ── 4. PRICE_VALIDATION (#P-14) ───────────────────────────────────────
    fresh = check_price_freshness(
        symbol=resolved_symbol,
        price=effective_price,
        price_timestamp=effective_ts,
        now=now,
        max_age_seconds=max_age_seconds,
        reference_price=reference_price,
        max_change_pct=max_change_pct,
        action="BUY",
    )
    if not fresh.allowed:
        # freshness reason_code → run-once reason_code 매핑.
        code = {
            INVALID_PRICE:       RunOnceResultCode.INVALID_PRICE,
            PRICE_STALE:         RunOnceResultCode.PRICE_STALE,
            ABNORMAL_PRICE_MOVE: RunOnceResultCode.ABNORMAL_PRICE_MOVE,
            PRICE_MISSING:       RunOnceResultCode.NO_MARKET_DATA,
        }.get(fresh.reason_code, RunOnceResultCode.INVALID_PRICE)
        stages.append(PipelineStage(
            stage="PRICE_VALIDATION", ok=False,
            reason_code=code.value,
            message=fresh.reason_message,
            detail={"freshness_reason_code": fresh.reason_code,
                    "age_seconds": fresh.age_seconds,
                    "price_change_pct": fresh.price_change_pct},
        ))
        return _finalize(
            code, ok=False, sym=resolved_symbol, px=effective_price,
            qty=0, notional=0, uni=uni, is_mock=is_mock,
        )
    stages.append(PipelineStage(
        stage="PRICE_VALIDATION", ok=True, reason_code=PRICE_FRESHNESS_OK,
        message=fresh.reason_message,
        detail={"age_seconds": fresh.age_seconds},
    ))

    # ── 5. SIZING (#P-08) ─────────────────────────────────────────────────
    cap = (
        int(per_symbol_cap_krw)
        if per_symbol_cap_krw is not None
        else _resolve_default_cap()
    )
    sized = compute_paper_quantity_by_price(
        action="BUY", symbol=resolved_symbol, price=effective_price,
        max_amount_krw=cap,
    )
    if quantity is not None:
        # 운영자가 수량을 명시 → 그대로 사용하되 ≥1 검증.
        requested_qty = int(quantity)
    else:
        requested_qty = int(sized.quantity)

    if sized.verdict == QuantityByPriceVerdict.MIN_LOT_NOT_AFFORDABLE or requested_qty < 1:
        stages.append(PipelineStage(
            stage="SIZING", ok=False,
            reason_code=RunOnceResultCode.MIN_LOT_NOT_AFFORDABLE.value,
            message=sized.reason_ko,
            detail={"cap_krw": cap, "sizer_verdict": sized.verdict.value},
        ))
        return _finalize(
            RunOnceResultCode.MIN_LOT_NOT_AFFORDABLE,
            ok=False, sym=resolved_symbol, px=effective_price, qty=0,
            notional=0, uni=uni, is_mock=is_mock,
            extra_meta={"per_symbol_cap_krw": cap},
        )
    notional = int(effective_price * requested_qty)
    stages.append(PipelineStage(
        stage="SIZING", ok=True, reason_code="OK",
        message=(f"{requested_qty}주 × {int(effective_price):,}원 = "
                 f"{notional:,}원 (종목당 한도 {cap:,}원)"),
        detail={"quantity": requested_qty, "notional_krw": notional,
                "cap_krw": cap},
    ))

    # ── 6. CASH (#P-07) ───────────────────────────────────────────────────
    cash = (
        int(available_cash_krw)
        if available_cash_krw is not None
        else _resolve_available_cash()
    )
    cash_res = check_buy_cash_sufficient(
        action="BUY", symbol=resolved_symbol, price=effective_price,
        quantity=requested_qty, available_cash_krw=cash,
    )
    if cash_res.verdict == CashCheckVerdict.INSUFFICIENT_PAPER_CASH:
        stages.append(PipelineStage(
            stage="CASH", ok=False,
            reason_code=RunOnceResultCode.INSUFFICIENT_PAPER_CASH.value,
            message=cash_res.reason_ko,
            detail={"required_krw": cash_res.required_krw,
                    "available_cash_krw": cash_res.available_cash_krw,
                    "shortfall_krw": cash_res.shortfall_krw},
        ))
        return _finalize(
            RunOnceResultCode.INSUFFICIENT_PAPER_CASH,
            ok=False, sym=resolved_symbol, px=effective_price,
            qty=requested_qty, notional=notional, uni=uni, is_mock=is_mock,
            extra_meta={"required_krw": cash_res.required_krw,
                        "shortfall_krw": cash_res.shortfall_krw},
        )
    if cash_res.verdict != CashCheckVerdict.ALLOWED:
        # MISSING_PRICE / INVALID_PRICE / INVALID_QUANTITY 등 — 방어적 처리.
        stages.append(PipelineStage(
            stage="CASH", ok=False,
            reason_code=RunOnceResultCode.INVALID_PRICE.value,
            message=cash_res.reason_ko,
        ))
        return _finalize(
            RunOnceResultCode.INVALID_PRICE,
            ok=False, sym=resolved_symbol, px=effective_price,
            qty=requested_qty, notional=notional, uni=uni, is_mock=is_mock,
        )
    stages.append(PipelineStage(
        stage="CASH", ok=True, reason_code="OK",
        message=cash_res.reason_ko,
        detail={"required_krw": cash_res.required_krw,
                "available_cash_krw": cash_res.available_cash_krw},
    ))

    # ── 7. PERMISSION (PermissionGate paper-side) ─────────────────────────
    allowed, perm_code, perm_msg = evaluate_paper_execution_permission(
        dry_run=dry_run,
        paper_virtual_execution_enabled=paper_virtual_execution_enabled,
        force_block=force_block_permission,
    )
    if not allowed:
        stages.append(PipelineStage(
            stage="PERMISSION", ok=False,
            reason_code=perm_code.value, message=perm_msg,
        ))
        return _finalize(
            perm_code, ok=False, sym=resolved_symbol, px=effective_price,
            qty=requested_qty, notional=notional, uni=uni, is_mock=is_mock,
        )
    stages.append(PipelineStage(
        stage="PERMISSION", ok=True, reason_code=perm_code.value,
        message=perm_msg,
        detail={"live_execution_blocked": True,
                "paper_virtual_execution_allowed": True},
    ))

    # ── 8. FINAL ──────────────────────────────────────────────────────────
    final_code = (
        RunOnceResultCode.PAPER_DRY_RUN_OK if dry_run
        else RunOnceResultCode.VIRTUAL_ORDER_CANDIDATE_CREATED
    )
    stages.append(PipelineStage(
        stage="FINAL", ok=True, reason_code=final_code.value,
        message=(
            "dry-run 통과 (가상 후보 미생성)" if dry_run
            else f"가상 주문 후보 생성: {resolved_symbol} {requested_qty}주"
        ),
        detail={"virtual_order_candidate": {
            "symbol": resolved_symbol, "side": "BUY",
            "quantity": requested_qty, "price": effective_price,
            "notional_krw": notional, "broker_order_sent": False,
        }},
    ))
    return _finalize(
        final_code, ok=True, sym=resolved_symbol, px=effective_price,
        qty=requested_qty, notional=notional, uni=uni, is_mock=is_mock,
        extra_meta={"virtual_order_candidate": {
            "symbol": resolved_symbol, "side": "BUY",
            "quantity": requested_qty, "price": effective_price,
            "notional_krw": notional,
        }},
    )


def _resolve_default_cap() -> int:
    """현재 PaperCapitalConfig 의 종목당 한도 — 미가용 시 100만원 fallback."""
    try:
        from app.auto_paper.capital_config import get_paper_capital_config
        return int(get_paper_capital_config().effective_per_symbol_cap_krw)
    except Exception:  # noqa: BLE001
        return 1_000_000


def _resolve_available_cash() -> int:
    """현재 CapitalState 의 남은 Paper 현금 — 미가용 시 1천만원 fallback."""
    try:
        from app.auto_paper.capital_state import get_capital_state
        return int(get_capital_state().snapshot().available_cash_krw)
    except Exception:  # noqa: BLE001
        return 10_000_000


__all__ = [
    "RunOnceResultCode",
    "result_message_ko",
    "PipelineStage",
    "RunOnceResult",
    "mock_price_for",
    "evaluate_paper_execution_permission",
    "run_paper_pipeline_once",
]
