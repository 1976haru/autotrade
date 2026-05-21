"""Paper preflight diagnostics — *"왜 주문이 0건인가"* 단일 진단 모듈.

자동매매를 정규장 시간에 실행했는데 매수/매도가 1건도 발생하지 않을 때,
운영자가 화면 한 곳에서 모든 원인을 *읽을 수 있게* 만든다. 본 모듈은 *순수
함수* — broker / OrderExecutor / route_order / KIS / 외부 HTTP / AI SDK
import 0건. settings 도 *입력 DTO* 로만 받아 evaluator 가 직접 .env 를
읽지 않는다 (운영자 입력값과 실제값 혼선 방지).

진단 항목 (사용자 요청서):
  - 현재 운용 모드
  - frontend ↔ backend mode 일치 여부
  - 관심종목 수 / universe source
  - market data provider / 가용 여부
  - strategy loop 연결 여부 / 자동봇 backend loop 실행 여부
  - permission gate 상태
  - paper execution 허용 여부 / live execution 차단 여부
  - 마지막 후보 / 신호 / 주문 / 차단 사유

차단 사유 enum: `PaperBlockReason` — frontend 가 같은 라벨을 표시.

CLAUDE.md 절대 원칙 (테스트로 lock):
- broker / OrderExecutor / route_order import 0건
- KIS / Anthropic / OpenAI / httpx / requests import 0건
- app.core.config.get_settings import 0건 (caller 가 DTO 로 주입)
- app.brokers / app.execution / app.kis_paper.engine import 0건
- settings.enable_*_trading mutate 0건
- PaperDiagnosticsReport.is_order_signal / is_live_authorization = False 영구
- is_paper_safe_only = True 영구 — 본 리포트는 PAPER 진단 advisory
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# ============================================================================
# Blocking reasons enum — 사용자 요청서 매트릭스
# ============================================================================


class PaperBlockReason(StrEnum):
    """주문이 0건인 *주요* 차단 사유. 우선순위 = 진단 흐름의 위에서 아래."""

    NONE                          = "NONE"
    NO_UNIVERSE                   = "NO_UNIVERSE"
    USING_FALLBACK_UNIVERSE       = "USING_FALLBACK_UNIVERSE"
    NO_MARKET_DATA                = "NO_MARKET_DATA"
    MOCK_MARKET_DATA_ONLY         = "MOCK_MARKET_DATA_ONLY"
    STRATEGY_ENGINE_NOT_CONNECTED = "STRATEGY_ENGINE_NOT_CONNECTED"
    AUTO_BOT_NOT_RUNNING          = "AUTO_BOT_NOT_RUNNING"
    NO_CANDIDATE                  = "NO_CANDIDATE"
    NO_STRATEGY_SIGNAL            = "NO_STRATEGY_SIGNAL"
    BLOCKED_BY_RISK_MANAGER       = "BLOCKED_BY_RISK_MANAGER"
    BLOCKED_BY_PERMISSION_GATE    = "BLOCKED_BY_PERMISSION_GATE"
    PAPER_EXECUTION_DISABLED      = "PAPER_EXECUTION_DISABLED"
    MARKET_CLOSED                 = "MARKET_CLOSED"
    LIVE_DISABLED_SAFE            = "LIVE_DISABLED_SAFE"
    AI_EXECUTION_DISABLED_SAFE    = "AI_EXECUTION_DISABLED_SAFE"
    MODE_MISMATCH                 = "MODE_MISMATCH"


# 한국어 사람 친화 메시지 매핑 — frontend 가 그대로 표시.
_HUMAN_KO: dict[PaperBlockReason, str] = {
    PaperBlockReason.NONE: (
        "전 단계 점검 통과 — 주문 0건의 직접적 차단 사유는 발견되지 않았습니다 "
        "(신호 부재 가능성)."
    ),
    PaperBlockReason.NO_UNIVERSE: (
        "관심종목이 없고 fallback universe 도 사용할 수 없어 후보 종목 0건. "
        "Watchlist 등록 또는 fallback 활성화가 필요합니다."
    ),
    PaperBlockReason.USING_FALLBACK_UNIVERSE: (
        "관심종목이 없어 시가총액 상위 50개 기본 Universe (fallback) 를 사용합니다. "
        "본 목록은 PAPER 테스트용이며 투자 추천이 아닙니다."
    ),
    PaperBlockReason.NO_MARKET_DATA: (
        "market data provider 가 시세를 제공하지 못해 후보 데이터가 없습니다."
    ),
    PaperBlockReason.MOCK_MARKET_DATA_ONLY: (
        "시장 데이터 provider 가 mock 이며, 실 시세 기반 후보가 없습니다. "
        "MARKET_DATA_PROVIDER=yfinance 등으로 전환하면 실 시세 기반 신호가 생성됩니다."
    ),
    PaperBlockReason.STRATEGY_ENGINE_NOT_CONNECTED: (
        "전략 엔진이 자동봇과 연결되지 않아 신호가 생성되지 않았습니다."
    ),
    PaperBlockReason.AUTO_BOT_NOT_RUNNING: (
        "자동봇 backend loop 가 RUNNING 상태가 아니라 tick 이 발생하지 않았습니다."
    ),
    PaperBlockReason.NO_CANDIDATE: (
        "전략이 매수/매도 후보를 생성하지 않았습니다 (시장 조건 미달)."
    ),
    PaperBlockReason.NO_STRATEGY_SIGNAL: (
        "후보는 생성되었지만 어떤 전략도 entry/exit 신호를 발사하지 않았습니다."
    ),
    PaperBlockReason.BLOCKED_BY_RISK_MANAGER: (
        "RiskManager 가 신호를 차단해 주문이 생성되지 않았습니다."
    ),
    PaperBlockReason.BLOCKED_BY_PERMISSION_GATE: (
        "PermissionGate 가 신호를 차단해 가상 주문이 생성되지 않았습니다."
    ),
    PaperBlockReason.PAPER_EXECUTION_DISABLED: (
        "현재 PAPER 모드이지만 가상 실행이 차단되어 주문이 생성되지 않았습니다."
    ),
    PaperBlockReason.MARKET_CLOSED: (
        "한국장 마감 / 주말이라 신규 주문이 생성되지 않습니다."
    ),
    PaperBlockReason.LIVE_DISABLED_SAFE: (
        "실거래(LIVE) 가 안전하게 비활성화되어 있습니다 (ENABLE_LIVE_TRADING=false). "
        "이는 정상 안전 상태이며 PAPER / SIMULATION 검증을 차단하지 않습니다."
    ),
    PaperBlockReason.AI_EXECUTION_DISABLED_SAFE: (
        "AI 자동 실행이 안전하게 비활성화되어 있습니다 (ENABLE_AI_EXECUTION=false). "
        "이는 정상 안전 상태이며 PAPER / SIMULATION 검증을 차단하지 않습니다."
    ),
    PaperBlockReason.MODE_MISMATCH: (
        "frontend 표시 모드와 backend runtime mode 가 일치하지 않습니다 — "
        "운영자가 모드를 변경한 후 backend 가 반영되지 않았을 수 있습니다."
    ),
}


def human_message_ko(reason: PaperBlockReason) -> str:
    """한국어 사람 친화 메시지 lookup — UI 가 그대로 표시 가능."""
    return _HUMAN_KO.get(reason, reason.value)


# ============================================================================
# Input DTOs — settings 직접 import 금지를 위한 carrier
# ============================================================================


@dataclass(frozen=True)
class SafetyFlagsInput:
    """현재 .env 안전 flag 라벨 — 운영자 / API 가 명시 전달.

    본 모듈이 settings 를 *직접 읽지 않도록* DTO 로만 주입.
    """
    default_mode:                 str  = "SIMULATION"
    enable_live_trading:          bool = False
    enable_ai_execution:          bool = False
    enable_futures_live_trading:  bool = False
    kis_is_paper:                 bool = True
    market_data_provider:         str  = "mock"


@dataclass(frozen=True)
class AutoBotLoopInput:
    """자동봇 loop 의 상태 라벨 — `AutoPaperLoop.status()` 의 압축 carry."""

    state:                  str  = "PAUSED"     # RUNNING / PAUSED / STOPPED / EMERGENCY_STOP / WAITING_MARKET / MARKET_CLOSED
    is_running:             bool = False
    cycle_count:            int  = 0
    last_consumed:          bool = False
    last_decision_count:    int  = 0
    last_decision_action:   str | None = None
    last_ledger_events:     int  = 0
    last_decision_log_count:int  = 0
    last_error:             str | None = None
    # *strategy engine* 이 loop tick 에 plug 되어 있는지 — handler 등록 여부.
    strategy_engine_connected: bool = False


@dataclass(frozen=True)
class PermissionGateInput:
    """PermissionGate 라벨 — frontend / API 가 명시 전달.

    `paper_virtual_execution_allowed=False` 면 PAPER 가상 주문 차단 사유 carry.
    """

    paper_virtual_execution_allowed: bool = True
    live_execution_blocked:          bool = True
    last_block_reason:               str | None = None
    note:                            str | None = None


@dataclass(frozen=True)
class FrontendModeInput:
    """frontend 가 *현재 화면에 표시 중* 인 운용 모드 — backend 와 일치 검증."""

    displayed_mode: str | None = None


# ============================================================================
# Output dataclass
# ============================================================================


@dataclass(frozen=True)
class PaperDiagnosticsReport:
    """PAPER 진단 리포트 — *advisory*, broker / route_order 호출 0건.

    `is_paper_safe_only=True` / `is_order_signal=False` / `is_live_authorization=
    False` 영구 (dataclass __post_init__ 가드).
    """

    primary_block_reason:    PaperBlockReason
    blocking_reasons:        tuple[PaperBlockReason, ...]
    warnings:                tuple[PaperBlockReason, ...]
    summary_ko:              str
    universe_source:         str
    universe_count:          int
    universe_fallback_used:  bool
    universe_warning_ko:     str
    market_data_provider:    str
    default_mode:            str
    enable_live_trading:     bool
    enable_ai_execution:     bool
    enable_futures_live_trading: bool
    kis_is_paper:            bool
    auto_bot_state:          str
    auto_bot_running:        bool
    strategy_engine_connected: bool
    last_decision_count:     int
    last_ledger_events:      int
    last_decision_log_count: int
    paper_virtual_execution_allowed: bool
    live_execution_blocked:  bool
    frontend_mode:           str | None
    mode_mismatch:           bool
    blocking_messages_ko:    tuple[str, ...]
    warning_messages_ko:     tuple[str, ...]
    next_actions_ko:         tuple[str, ...]
    metadata:                dict[str, Any] = field(default_factory=dict)

    is_order_signal:         bool = False
    is_live_authorization:   bool = False
    is_paper_safe_only:      bool = True

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("PaperDiagnosticsReport.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError(
                "PaperDiagnosticsReport.is_live_authorization must be False"
            )
        if self.is_paper_safe_only is not True:
            raise ValueError(
                "PaperDiagnosticsReport.is_paper_safe_only must be True"
            )
        if self.universe_count < 0:
            raise ValueError(
                f"universe_count must be >= 0, got {self.universe_count}"
            )

    @property
    def has_blocking(self) -> bool:
        return self.primary_block_reason != PaperBlockReason.NONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_block_reason":  self.primary_block_reason.value,
            "blocking_reasons":      [r.value for r in self.blocking_reasons],
            "warnings":              [r.value for r in self.warnings],
            "summary_ko":            self.summary_ko,
            "universe_source":       self.universe_source,
            "universe_count":        int(self.universe_count),
            "universe_fallback_used": self.universe_fallback_used,
            "universe_warning_ko":   self.universe_warning_ko,
            "market_data_provider":  self.market_data_provider,
            "default_mode":          self.default_mode,
            "enable_live_trading":   self.enable_live_trading,
            "enable_ai_execution":   self.enable_ai_execution,
            "enable_futures_live_trading": self.enable_futures_live_trading,
            "kis_is_paper":          self.kis_is_paper,
            "auto_bot_state":        self.auto_bot_state,
            "auto_bot_running":      self.auto_bot_running,
            "strategy_engine_connected": self.strategy_engine_connected,
            "last_decision_count":   int(self.last_decision_count),
            "last_ledger_events":    int(self.last_ledger_events),
            "last_decision_log_count": int(self.last_decision_log_count),
            "paper_virtual_execution_allowed": self.paper_virtual_execution_allowed,
            "live_execution_blocked": self.live_execution_blocked,
            "frontend_mode":         self.frontend_mode,
            "mode_mismatch":         self.mode_mismatch,
            "blocking_messages_ko":  list(self.blocking_messages_ko),
            "warning_messages_ko":   list(self.warning_messages_ko),
            "next_actions_ko":       list(self.next_actions_ko),
            "has_blocking":          self.has_blocking,
            "metadata":              dict(self.metadata),
            "is_order_signal":       self.is_order_signal,
            "is_live_authorization": self.is_live_authorization,
            "is_paper_safe_only":    self.is_paper_safe_only,
        }


# ============================================================================
# Evaluator
# ============================================================================


def _normalize_mode(raw: str | None) -> str | None:
    if raw is None:
        return None
    return str(raw).strip().upper() or None


def evaluate_paper_diagnostics(
    *,
    universe_source:        str,
    universe_count:         int,
    universe_fallback_used: bool = False,
    universe_warning_ko:    str  = "",
    safety:                 SafetyFlagsInput,
    auto_bot:               AutoBotLoopInput,
    permission:             PermissionGateInput,
    frontend:               FrontendModeInput | None = None,
) -> PaperDiagnosticsReport:
    """PAPER preflight 진단 — *blocking* + *warning* 분리 추정.

    분류:
    - blocking: 신호 → 주문 흐름을 *완전히* 막는 사유 (primary 1개 선정).
    - warnings: 막지는 않지만 운영자가 알아야 할 사유 (fallback / safe-off / etc).

    primary 우선순위 (위에서 아래):
      MODE_MISMATCH > NO_UNIVERSE > MARKET_CLOSED > AUTO_BOT_NOT_RUNNING >
      STRATEGY_ENGINE_NOT_CONNECTED > PAPER_EXECUTION_DISABLED >
      BLOCKED_BY_PERMISSION_GATE > NO_MARKET_DATA > NO_CANDIDATE >
      NO_STRATEGY_SIGNAL > NONE.
    """
    # 모드 일치 검증.
    backend_mode = _normalize_mode(safety.default_mode)
    frontend_mode = _normalize_mode(frontend.displayed_mode) if frontend else None
    mode_mismatch = bool(
        frontend_mode is not None
        and backend_mode is not None
        and frontend_mode != backend_mode
    )

    blocking: list[PaperBlockReason] = []
    warnings: list[PaperBlockReason] = []

    # ── Universe ──
    if int(universe_count) <= 0:
        blocking.append(PaperBlockReason.NO_UNIVERSE)
    elif universe_fallback_used:
        # fallback 은 *경고* — 후보 흐름은 살아 있다.
        warnings.append(PaperBlockReason.USING_FALLBACK_UNIVERSE)

    # ── Market clock ──
    if auto_bot.state in ("MARKET_CLOSED",):
        blocking.append(PaperBlockReason.MARKET_CLOSED)

    # ── Auto-bot loop ──
    if not auto_bot.is_running and auto_bot.state not in (
        "WAITING_MARKET", "MARKET_CLOSED",
    ):
        blocking.append(PaperBlockReason.AUTO_BOT_NOT_RUNNING)

    # ── Strategy engine ──
    if not auto_bot.strategy_engine_connected:
        blocking.append(PaperBlockReason.STRATEGY_ENGINE_NOT_CONNECTED)

    # ── PermissionGate / Paper exec ──
    if not permission.paper_virtual_execution_allowed:
        blocking.append(PaperBlockReason.PAPER_EXECUTION_DISABLED)
    if permission.last_block_reason:
        blocking.append(PaperBlockReason.BLOCKED_BY_PERMISSION_GATE)

    # ── Market data ──
    provider = (safety.market_data_provider or "").strip().lower()
    if not provider:
        blocking.append(PaperBlockReason.NO_MARKET_DATA)
    elif provider == "mock":
        # mock 은 실 시세 기반 신호가 약함 — 경고로만 carry (모의 흐름 자체는 동작).
        warnings.append(PaperBlockReason.MOCK_MARKET_DATA_ONLY)

    # ── 마지막 cycle 결과 (consumer) ──
    if auto_bot.is_running and auto_bot.cycle_count > 0:
        if auto_bot.last_decision_count == 0 and auto_bot.last_consumed is False:
            # consumer 가 한 번도 비결정을 만들지 못한 경우.
            blocking.append(PaperBlockReason.NO_CANDIDATE)
        elif (
            auto_bot.last_decision_count > 0
            and auto_bot.last_ledger_events == 0
        ):
            blocking.append(PaperBlockReason.NO_STRATEGY_SIGNAL)

    # ── 모드 불일치 (가장 위 우선순위 — 운영자가 즉시 인지해야 함) ──
    if mode_mismatch:
        blocking.insert(0, PaperBlockReason.MODE_MISMATCH)

    # ── 안전 flag 라벨 — *경고* 만 (정상 안전 상태) ──
    if not safety.enable_live_trading:
        warnings.append(PaperBlockReason.LIVE_DISABLED_SAFE)
    if not safety.enable_ai_execution:
        warnings.append(PaperBlockReason.AI_EXECUTION_DISABLED_SAFE)

    # ── Primary 선정 ──
    priority = (
        PaperBlockReason.MODE_MISMATCH,
        PaperBlockReason.NO_UNIVERSE,
        PaperBlockReason.MARKET_CLOSED,
        PaperBlockReason.AUTO_BOT_NOT_RUNNING,
        PaperBlockReason.STRATEGY_ENGINE_NOT_CONNECTED,
        PaperBlockReason.PAPER_EXECUTION_DISABLED,
        PaperBlockReason.BLOCKED_BY_PERMISSION_GATE,
        PaperBlockReason.NO_MARKET_DATA,
        PaperBlockReason.NO_CANDIDATE,
        PaperBlockReason.NO_STRATEGY_SIGNAL,
    )
    primary = PaperBlockReason.NONE
    for r in priority:
        if r in blocking:
            primary = r
            break

    # blocking 리스트는 우선순위 정렬 + 중복 제거.
    blocking_unique = tuple(
        r for r in priority if r in blocking
    )
    warnings_unique: tuple[PaperBlockReason, ...] = tuple(
        # warnings 는 등장 순서 보존.
        dict.fromkeys(warnings)
    )

    # 메시지 + 다음 단계.
    blocking_msgs = tuple(human_message_ko(r) for r in blocking_unique)
    warning_msgs = tuple(human_message_ko(r) for r in warnings_unique)

    next_actions: list[str] = []
    if primary == PaperBlockReason.NO_UNIVERSE:
        next_actions.append(
            "관심종목을 1개 이상 등록하거나 fallback universe 를 활성화하세요."
        )
    if PaperBlockReason.USING_FALLBACK_UNIVERSE in warnings_unique:
        next_actions.append(
            "정식 관심종목 등록을 권장합니다 (fallback 은 PAPER 테스트용)."
        )
    if primary == PaperBlockReason.MARKET_CLOSED:
        next_actions.append(
            "한국장 평일 09:00 ~ 15:30 KST 시간대에 다시 시작해 보세요."
        )
    if primary == PaperBlockReason.AUTO_BOT_NOT_RUNNING:
        next_actions.append(
            "자동봇 시작 버튼을 눌러 backend loop 를 RUNNING 으로 전환하세요."
        )
    if primary == PaperBlockReason.STRATEGY_ENGINE_NOT_CONNECTED:
        next_actions.append(
            "전략 엔진을 자동봇 backend loop 에 plug 하세요 "
            "(handler / consumer 미등록)."
        )
    if primary == PaperBlockReason.PAPER_EXECUTION_DISABLED:
        next_actions.append(
            "PermissionGate 의 paper_virtual_execution_allowed 설정을 점검하세요."
        )
    if primary == PaperBlockReason.NO_MARKET_DATA:
        next_actions.append(
            "MARKET_DATA_PROVIDER 환경변수와 provider 연결 상태를 점검하세요."
        )
    if PaperBlockReason.MOCK_MARKET_DATA_ONLY in warnings_unique:
        next_actions.append(
            "실 시세 기반 신호를 보려면 MARKET_DATA_PROVIDER=yfinance 등으로 "
            "교체하세요 (mock 은 후보 데이터가 부족)."
        )
    if primary == PaperBlockReason.NO_CANDIDATE:
        next_actions.append(
            "전략이 시장 조건 미달로 후보를 만들지 않았습니다 — universe / "
            "시장 시간 / 데이터 freshness 를 함께 점검하세요."
        )
    if primary == PaperBlockReason.NO_STRATEGY_SIGNAL:
        next_actions.append(
            "후보는 있으나 신호가 없습니다 — 전략 파라미터 또는 시장 조건 점검."
        )
    if mode_mismatch:
        next_actions.insert(
            0,
            f"frontend 모드 ({frontend_mode}) ≠ backend 모드 ({backend_mode}). "
            "frontend 새로고침 또는 backend 모드 재설정 필요.",
        )

    if primary == PaperBlockReason.NONE:
        summary = (
            "PAPER 자동매매 사전 점검 통과 — 주문 0건의 직접적 차단 사유는 "
            "없습니다."
        )
        if warnings_unique:
            summary += f" (경고 {len(warnings_unique)}건)"
    else:
        summary = (
            f"PAPER 자동매매 주문 0건 — 주요 사유: {primary.value} "
            f"({human_message_ko(primary)})"
        )

    return PaperDiagnosticsReport(
        primary_block_reason=primary,
        blocking_reasons=blocking_unique,
        warnings=warnings_unique,
        summary_ko=summary,
        universe_source=str(universe_source or ""),
        universe_count=int(universe_count),
        universe_fallback_used=bool(universe_fallback_used),
        universe_warning_ko=str(universe_warning_ko or ""),
        market_data_provider=str(safety.market_data_provider or ""),
        default_mode=str(safety.default_mode or ""),
        enable_live_trading=bool(safety.enable_live_trading),
        enable_ai_execution=bool(safety.enable_ai_execution),
        enable_futures_live_trading=bool(safety.enable_futures_live_trading),
        kis_is_paper=bool(safety.kis_is_paper),
        auto_bot_state=str(auto_bot.state),
        auto_bot_running=bool(auto_bot.is_running),
        strategy_engine_connected=bool(auto_bot.strategy_engine_connected),
        last_decision_count=int(auto_bot.last_decision_count),
        last_ledger_events=int(auto_bot.last_ledger_events),
        last_decision_log_count=int(auto_bot.last_decision_log_count),
        paper_virtual_execution_allowed=bool(
            permission.paper_virtual_execution_allowed
        ),
        live_execution_blocked=bool(permission.live_execution_blocked),
        frontend_mode=frontend_mode,
        mode_mismatch=mode_mismatch,
        blocking_messages_ko=blocking_msgs,
        warning_messages_ko=warning_msgs,
        next_actions_ko=tuple(next_actions),
    )


__all__ = [
    "PaperBlockReason",
    "SafetyFlagsInput",
    "AutoBotLoopInput",
    "PermissionGateInput",
    "FrontendModeInput",
    "PaperDiagnosticsReport",
    "evaluate_paper_diagnostics",
    "human_message_ko",
]
