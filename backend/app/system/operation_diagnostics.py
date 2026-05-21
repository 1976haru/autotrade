"""Operator-facing operation diagnostics — *"지금 시스템이 잘 동작 중인가"*
를 단일 리포트로 답한다.

EXE 운영자가 화면 한 곳에서:
- 전체 상태 (HEALTHY / WARN / ERROR / STOPPED)
- 현재 mode + 안전 flag 라벨
- backend / market data / universe / strategy engine / auto-bot / RiskManager /
  PermissionGate / paper execution / live execution 상태
- 오늘 후보 / 신호 / 가상주문 / 차단 카운트
- "오늘 주문 0건 원인" primary reason + next_actions
- 한국어 conclusion + next_actions 권장 다음 단계
- desktop launcher / .env / logs 디렉토리 점검

본 모듈은 *advisory* — broker / OrderExecutor / route_order 호출 0건. settings
는 caller 가 *DTO* 로 주입 (운영자 입력값 ↔ 실제값 혼선 차단). 어떤 mutation
도 발행하지 않는다.

CLAUDE.md 절대 원칙 (테스트로 lock):
- broker / OrderExecutor / route_order import 0건
- KIS / Anthropic / OpenAI / httpx / requests import 0건
- app.brokers / app.execution / app.kis_paper.engine import 0건
- app.core.config.get_settings import 0건 (DTO 주입)
- settings.enable_*_trading mutation 0건
- DiagnosticsReport.is_order_signal / is_live_authorization = False 영구
- safe_for_ui = True, contains_secret = False 영구
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# ============================================================================
# Status + reason enums
# ============================================================================


class OverallStatus(StrEnum):
    HEALTHY = "HEALTHY"
    WARN    = "WARN"
    ERROR   = "ERROR"
    STOPPED = "STOPPED"


class ZeroOrderReason(StrEnum):
    """오늘 주문 0건의 *주요* 원인. 우선순위 순."""
    NONE                          = "NONE"               # 주문 있음
    BACKEND_OFFLINE               = "BACKEND_OFFLINE"
    DESKTOP_ENVIRONMENT_ERROR     = "DESKTOP_ENVIRONMENT_ERROR"
    MODE_MISMATCH                 = "MODE_MISMATCH"
    EMERGENCY_STOP                = "EMERGENCY_STOP"
    MARKET_CLOSED                 = "MARKET_CLOSED"
    NO_UNIVERSE                   = "NO_UNIVERSE"
    USING_FALLBACK_UNIVERSE       = "USING_FALLBACK_UNIVERSE"
    NO_MARKET_DATA                = "NO_MARKET_DATA"
    MOCK_MARKET_DATA_ONLY         = "MOCK_MARKET_DATA_ONLY"
    AUTO_BOT_NOT_RUNNING          = "AUTO_BOT_NOT_RUNNING"
    STRATEGY_ENGINE_NOT_CONNECTED = "STRATEGY_ENGINE_NOT_CONNECTED"
    NO_CANDIDATE                  = "NO_CANDIDATE"
    NO_STRATEGY_SIGNAL            = "NO_STRATEGY_SIGNAL"
    BLOCKED_BY_RISK_MANAGER       = "BLOCKED_BY_RISK_MANAGER"
    BLOCKED_BY_PERMISSION_GATE    = "BLOCKED_BY_PERMISSION_GATE"
    PAPER_EXECUTION_DISABLED      = "PAPER_EXECUTION_DISABLED"
    INSUFFICIENT_PAPER_CASH       = "INSUFFICIENT_PAPER_CASH"
    LIVE_DISABLED_SAFE            = "LIVE_DISABLED_SAFE"
    AI_EXECUTION_DISABLED_SAFE    = "AI_EXECUTION_DISABLED_SAFE"


_ZERO_ORDER_HUMAN_KO: dict[ZeroOrderReason, str] = {
    ZeroOrderReason.NONE: (
        "오늘 주문이 정상 생성되었습니다."
    ),
    ZeroOrderReason.BACKEND_OFFLINE: (
        "백엔드 프로세스가 응답하지 않습니다 — desktop launcher 가 backend "
        "를 띄웠는지 확인하세요."
    ),
    ZeroOrderReason.DESKTOP_ENVIRONMENT_ERROR: (
        "데스크톱 실행 환경에 문제가 있습니다 — .env / 로그 폴더 / 포트 충돌 "
        "확인이 필요합니다."
    ),
    ZeroOrderReason.MODE_MISMATCH: (
        "frontend 표시 모드와 backend runtime mode 가 일치하지 않습니다."
    ),
    ZeroOrderReason.EMERGENCY_STOP: (
        "긴급정지 상태입니다 — 운영자가 reset 후 재시작해야 주문이 생성됩니다."
    ),
    ZeroOrderReason.MARKET_CLOSED: (
        "한국장 마감 / 주말이라 신규 주문이 생성되지 않습니다."
    ),
    ZeroOrderReason.NO_UNIVERSE: (
        "관심종목이 없고 fallback universe 도 사용할 수 없어 후보 종목 0건."
    ),
    ZeroOrderReason.USING_FALLBACK_UNIVERSE: (
        "관심종목이 없어 시가총액 상위 50개 기본 Universe 를 사용 중입니다 — "
        "PAPER 테스트용이며 투자 추천이 아닙니다."
    ),
    ZeroOrderReason.NO_MARKET_DATA: (
        "시장 데이터 provider 가 응답하지 않아 후보 데이터가 없습니다."
    ),
    ZeroOrderReason.MOCK_MARKET_DATA_ONLY: (
        "시장 데이터 provider 가 mock 이라 실 시세 기반 신호가 약합니다 — "
        "MARKET_DATA_PROVIDER=yfinance 등으로 교체하세요."
    ),
    ZeroOrderReason.AUTO_BOT_NOT_RUNNING: (
        "자동봇 backend loop 가 RUNNING 상태가 아닙니다 — 시작 버튼을 누르세요."
    ),
    ZeroOrderReason.STRATEGY_ENGINE_NOT_CONNECTED: (
        "전략 엔진이 자동봇과 연결되지 않았습니다 — handler / consumer 미등록."
    ),
    ZeroOrderReason.NO_CANDIDATE: (
        "전략이 매수/매도 후보를 생성하지 않았습니다 (시장 조건 미달)."
    ),
    ZeroOrderReason.NO_STRATEGY_SIGNAL: (
        "후보는 있으나 어떤 전략도 entry/exit 신호를 발사하지 않았습니다."
    ),
    ZeroOrderReason.BLOCKED_BY_RISK_MANAGER: (
        "RiskManager 가 신호를 차단했습니다 — 차단 사유를 audit log 에서 확인하세요."
    ),
    ZeroOrderReason.BLOCKED_BY_PERMISSION_GATE: (
        "PermissionGate 가 신호를 차단해 가상 주문이 생성되지 않았습니다."
    ),
    ZeroOrderReason.PAPER_EXECUTION_DISABLED: (
        "현재 PAPER 모드이나 가상 실행이 차단되어 주문이 생성되지 않습니다."
    ),
    ZeroOrderReason.INSUFFICIENT_PAPER_CASH: (
        "남은 Paper 현금이 부족해 BUY 가 차단되었습니다 — Paper 시드머니를 "
        "리셋하거나 종목당 한도를 조정하세요."
    ),
    ZeroOrderReason.LIVE_DISABLED_SAFE: (
        "실거래(LIVE) 가 안전하게 비활성화되어 있습니다 (정상 안전 상태)."
    ),
    ZeroOrderReason.AI_EXECUTION_DISABLED_SAFE: (
        "AI 자동 실행이 안전하게 비활성화되어 있습니다 (정상 안전 상태)."
    ),
}


def zero_order_reason_human_ko(reason: ZeroOrderReason) -> str:
    return _ZERO_ORDER_HUMAN_KO.get(reason, reason.value)


# ============================================================================
# Input DTOs — caller (API endpoint) 가 settings + state 라벨을 명시 주입
# ============================================================================


@dataclass(frozen=True)
class SafetyFlagsInput:
    """.env 안전 flag 라벨 — caller 명시 주입 (settings 직접 import 0건)."""
    default_mode:                 str  = "SIMULATION"
    enable_live_trading:          bool = False
    enable_ai_execution:          bool = False
    enable_futures_live_trading:  bool = False
    kis_is_paper:                 bool = True
    market_data_provider:         str  = "mock"


@dataclass(frozen=True)
class BackendStatusInput:
    """Backend 자체 상태 — health endpoint / migration / DB 등."""
    backend_ready:    bool = True
    db_ready:         bool = True
    migration_state:  str  = "COMPLETED"


@dataclass(frozen=True)
class UniverseInput:
    """현재 universe 해결 결과 — `app.universe.default_universe` 가 carry."""
    source:         str  = "USER_DEFINED"
    count:          int  = 0
    fallback_used:  bool = False
    warning_ko:     str  = ""


@dataclass(frozen=True)
class MarketDataInput:
    """Market data provider 가용 / 마지막 fetch 결과."""
    provider:           str  = "mock"
    last_fetch_ok:      bool = True
    last_fetch_at:      str | None = None
    stale_symbols:      int  = 0


@dataclass(frozen=True)
class AutoBotInput:
    """자동봇 loop 상태."""
    state:                     str  = "PAUSED"
    is_running:                bool = False
    cycle_count:               int  = 0
    last_consumed:             bool = False
    last_decision_count:       int  = 0
    last_ledger_events:        int  = 0
    last_decision_log_count:   int  = 0
    last_error:                str | None = None
    strategy_engine_connected: bool = False


@dataclass(frozen=True)
class PermissionInput:
    """PermissionGate / paper execution 정책 라벨."""
    paper_virtual_execution_allowed: bool = True
    live_execution_blocked:          bool = True
    last_block_reason:               str | None = None
    risk_manager_last_block_reason:  str | None = None


@dataclass(frozen=True)
class PaperCashInput:
    """P-07 CapitalState snapshot 의 부분집합."""
    available_cash_krw: int = 0
    insufficient_today: bool = False


@dataclass(frozen=True)
class TodaySummaryInput:
    """오늘(KST) 누적 카운터 — caller 가 audit / ledger 에서 집계."""
    candidate_count:           int = 0
    signal_count:              int = 0
    approved_candidate_count:  int = 0
    rejected_candidate_count:  int = 0
    virtual_order_count:       int = 0
    blocked_order_count:       int = 0
    error_count:               int = 0
    warning_count:             int = 0


@dataclass(frozen=True)
class DesktopEnvInput:
    """EXE / 데스크톱 실행환경 점검 라벨 — secret 0건."""
    is_desktop:             bool = False
    backend_process_alive:  bool = True
    backend_port_reachable: bool = True
    env_file_present:       bool = True
    logs_dir_writable:      bool = True
    app_data_dir_writable:  bool = True
    launcher_ok:            bool = True
    notes:                  tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FrontendModeInput:
    displayed_mode: str | None = None


# ============================================================================
# Output dataclass
# ============================================================================


@dataclass(frozen=True)
class DiagnosticsReport:
    """단일 진단 리포트 — UI 가 그대로 표시 가능한 *safe* payload.

    `is_order_signal=False` / `is_live_authorization=False` / `safe_for_ui=True`
    / `contains_secret=False` 영구 (dataclass __post_init__ 가드).
    """

    overall_status:          OverallStatus
    conclusion_ko:           str
    next_actions_ko:         tuple[str, ...]

    # Mode + safety flags
    default_mode:            str
    enable_live_trading:     bool
    enable_ai_execution:     bool
    enable_futures_live_trading: bool
    kis_is_paper:            bool
    market_data_provider:    str
    frontend_mode:           str | None
    mode_mismatch:           bool

    # Backend
    backend_ready:           bool
    db_ready:                bool
    migration_state:         str

    # Universe
    universe_source:         str
    universe_count:          int
    universe_fallback_used:  bool
    universe_warning_ko:     str

    # Market data
    market_data_last_fetch_ok: bool
    market_data_last_fetch_at: str | None
    market_data_stale_symbols: int

    # Auto bot + strategy engine
    auto_bot_state:                   str
    auto_bot_running:                 bool
    auto_bot_cycle_count:             int
    auto_bot_last_decision_count:     int
    auto_bot_last_ledger_events:      int
    auto_bot_last_error:              str | None
    strategy_engine_connected:        bool

    # Permission / risk / paper exec
    paper_virtual_execution_allowed:  bool
    live_execution_blocked:           bool
    permission_last_block_reason:     str | None
    risk_manager_last_block_reason:   str | None
    paper_cash_available_krw:         int
    paper_cash_insufficient_today:    bool

    # Today summary
    today_candidate_count:           int
    today_signal_count:              int
    today_approved_candidate_count:  int
    today_rejected_candidate_count:  int
    today_virtual_order_count:       int
    today_blocked_order_count:       int
    today_error_count:               int
    today_warning_count:             int

    # Zero-order analysis
    has_orders_today:           bool
    zero_order_primary_reason:  ZeroOrderReason
    zero_order_primary_message: str
    zero_order_secondary_reasons: tuple[ZeroOrderReason, ...]
    zero_order_pipeline_stages: tuple[dict[str, Any], ...]

    # Desktop / EXE
    is_desktop:             bool
    desktop_checks:         tuple[dict[str, Any], ...]
    desktop_notes:          tuple[str, ...]

    # Recent events summary
    event_summary:          dict[str, Any]

    # Invariants
    safe_for_ui:            bool = True
    contains_secret:        bool = False
    is_order_signal:        bool = False
    is_live_authorization:  bool = False

    def __post_init__(self) -> None:
        if self.safe_for_ui is not True:
            raise ValueError("DiagnosticsReport.safe_for_ui must be True")
        if self.contains_secret is not False:
            raise ValueError("DiagnosticsReport.contains_secret must be False")
        if self.is_order_signal is not False:
            raise ValueError("DiagnosticsReport.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError(
                "DiagnosticsReport.is_live_authorization must be False"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status":          self.overall_status.value,
            "conclusion_ko":           self.conclusion_ko,
            "next_actions_ko":         list(self.next_actions_ko),
            "default_mode":            self.default_mode,
            "enable_live_trading":     self.enable_live_trading,
            "enable_ai_execution":     self.enable_ai_execution,
            "enable_futures_live_trading": self.enable_futures_live_trading,
            "kis_is_paper":            self.kis_is_paper,
            "market_data_provider":    self.market_data_provider,
            "frontend_mode":           self.frontend_mode,
            "mode_mismatch":           self.mode_mismatch,
            "backend_ready":           self.backend_ready,
            "db_ready":                self.db_ready,
            "migration_state":         self.migration_state,
            "universe_source":         self.universe_source,
            "universe_count":          int(self.universe_count),
            "universe_fallback_used":  self.universe_fallback_used,
            "universe_warning_ko":     self.universe_warning_ko,
            "market_data_last_fetch_ok":  self.market_data_last_fetch_ok,
            "market_data_last_fetch_at":  self.market_data_last_fetch_at,
            "market_data_stale_symbols":  int(self.market_data_stale_symbols),
            "auto_bot_state":          self.auto_bot_state,
            "auto_bot_running":        self.auto_bot_running,
            "auto_bot_cycle_count":    int(self.auto_bot_cycle_count),
            "auto_bot_last_decision_count":  int(self.auto_bot_last_decision_count),
            "auto_bot_last_ledger_events":   int(self.auto_bot_last_ledger_events),
            "auto_bot_last_error":     self.auto_bot_last_error,
            "strategy_engine_connected":     self.strategy_engine_connected,
            "paper_virtual_execution_allowed": self.paper_virtual_execution_allowed,
            "live_execution_blocked":  self.live_execution_blocked,
            "permission_last_block_reason":  self.permission_last_block_reason,
            "risk_manager_last_block_reason": self.risk_manager_last_block_reason,
            "paper_cash_available_krw":      int(self.paper_cash_available_krw),
            "paper_cash_insufficient_today": self.paper_cash_insufficient_today,
            "today_candidate_count":         int(self.today_candidate_count),
            "today_signal_count":            int(self.today_signal_count),
            "today_approved_candidate_count":int(self.today_approved_candidate_count),
            "today_rejected_candidate_count":int(self.today_rejected_candidate_count),
            "today_virtual_order_count":     int(self.today_virtual_order_count),
            "today_blocked_order_count":     int(self.today_blocked_order_count),
            "today_error_count":             int(self.today_error_count),
            "today_warning_count":           int(self.today_warning_count),
            "has_orders_today":              self.has_orders_today,
            "zero_order_primary_reason":     self.zero_order_primary_reason.value,
            "zero_order_primary_message":    self.zero_order_primary_message,
            "zero_order_secondary_reasons":  [
                r.value for r in self.zero_order_secondary_reasons
            ],
            "zero_order_pipeline_stages":    [
                dict(s) for s in self.zero_order_pipeline_stages
            ],
            "is_desktop":              self.is_desktop,
            "desktop_checks":          [dict(c) for c in self.desktop_checks],
            "desktop_notes":           list(self.desktop_notes),
            "event_summary":           dict(self.event_summary),
            "safe_for_ui":             self.safe_for_ui,
            "contains_secret":         self.contains_secret,
            "is_order_signal":         self.is_order_signal,
            "is_live_authorization":   self.is_live_authorization,
        }


# ============================================================================
# Zero-order analyzer
# ============================================================================


# 우선순위 — 위에서 아래로 첫 매칭이 primary.
_ZERO_ORDER_PRIORITY: tuple[ZeroOrderReason, ...] = (
    ZeroOrderReason.BACKEND_OFFLINE,
    ZeroOrderReason.DESKTOP_ENVIRONMENT_ERROR,
    ZeroOrderReason.MODE_MISMATCH,
    ZeroOrderReason.EMERGENCY_STOP,
    ZeroOrderReason.NO_UNIVERSE,
    ZeroOrderReason.MARKET_CLOSED,
    ZeroOrderReason.AUTO_BOT_NOT_RUNNING,
    ZeroOrderReason.STRATEGY_ENGINE_NOT_CONNECTED,
    ZeroOrderReason.PAPER_EXECUTION_DISABLED,
    ZeroOrderReason.BLOCKED_BY_PERMISSION_GATE,
    ZeroOrderReason.BLOCKED_BY_RISK_MANAGER,
    ZeroOrderReason.INSUFFICIENT_PAPER_CASH,
    ZeroOrderReason.NO_MARKET_DATA,
    ZeroOrderReason.NO_CANDIDATE,
    ZeroOrderReason.NO_STRATEGY_SIGNAL,
)


def analyze_zero_order_reason(
    *,
    backend:    BackendStatusInput,
    universe:   UniverseInput,
    market:     MarketDataInput,
    auto_bot:   AutoBotInput,
    permission: PermissionInput,
    cash:       PaperCashInput,
    today:      TodaySummaryInput,
    desktop:    DesktopEnvInput,
    safety:     SafetyFlagsInput,
    frontend:   FrontendModeInput,
) -> tuple[ZeroOrderReason, list[ZeroOrderReason], list[dict[str, Any]]]:
    """오늘 주문 0건의 *주요* + *부차* 원인 + pipeline stage 매트릭스 추정.

    Returns:
        (primary, secondary_list, pipeline_stages):
            - primary: 가장 우선순위 높은 reason (없으면 NONE)
            - secondary: 그 외 동시에 active 인 reason 들
            - pipeline_stages: 각 stage 의 ok/blocked + 메시지 carry
    """
    # 모드 일치 검증.
    fe = (frontend.displayed_mode or "").strip().upper() or None
    be = (safety.default_mode or "").strip().upper() or None
    mode_mismatch = bool(fe and be and fe != be)

    blocking: list[ZeroOrderReason] = []

    # 1. Backend / desktop env.
    if not backend.backend_ready:
        blocking.append(ZeroOrderReason.BACKEND_OFFLINE)
    if desktop.is_desktop and (
        not desktop.backend_process_alive
        or not desktop.backend_port_reachable
        or not desktop.env_file_present
        or not desktop.logs_dir_writable
        or not desktop.app_data_dir_writable
        or not desktop.launcher_ok
    ):
        blocking.append(ZeroOrderReason.DESKTOP_ENVIRONMENT_ERROR)

    # 2. Mode mismatch (운영자가 즉시 인지해야 함).
    if mode_mismatch:
        blocking.append(ZeroOrderReason.MODE_MISMATCH)

    # 3. Emergency stop.
    if auto_bot.state == "EMERGENCY_STOP":
        blocking.append(ZeroOrderReason.EMERGENCY_STOP)

    # 4. Universe.
    if universe.count <= 0:
        blocking.append(ZeroOrderReason.NO_UNIVERSE)

    # 5. Market clock.
    if auto_bot.state == "MARKET_CLOSED":
        blocking.append(ZeroOrderReason.MARKET_CLOSED)

    # 6. Auto bot loop.
    if not auto_bot.is_running and auto_bot.state not in (
        "WAITING_MARKET", "MARKET_CLOSED", "EMERGENCY_STOP",
    ):
        blocking.append(ZeroOrderReason.AUTO_BOT_NOT_RUNNING)

    # 7. Strategy engine plug.
    if not auto_bot.strategy_engine_connected:
        blocking.append(ZeroOrderReason.STRATEGY_ENGINE_NOT_CONNECTED)

    # 8. Paper exec policy.
    if not permission.paper_virtual_execution_allowed:
        blocking.append(ZeroOrderReason.PAPER_EXECUTION_DISABLED)
    if permission.last_block_reason:
        blocking.append(ZeroOrderReason.BLOCKED_BY_PERMISSION_GATE)
    if permission.risk_manager_last_block_reason:
        blocking.append(ZeroOrderReason.BLOCKED_BY_RISK_MANAGER)

    # 9. Cash.
    if cash.insufficient_today:
        blocking.append(ZeroOrderReason.INSUFFICIENT_PAPER_CASH)

    # 10. Market data.
    provider = (safety.market_data_provider or "").strip().lower()
    if not provider or not market.last_fetch_ok:
        blocking.append(ZeroOrderReason.NO_MARKET_DATA)

    # 11. Candidate / signal (loop 가 RUNNING + cycle 발생 후에만 의미).
    if auto_bot.is_running and auto_bot.cycle_count > 0:
        if (auto_bot.last_decision_count == 0
                and not auto_bot.last_consumed
                and today.candidate_count == 0):
            blocking.append(ZeroOrderReason.NO_CANDIDATE)
        elif (auto_bot.last_decision_count > 0
                and auto_bot.last_ledger_events == 0
                and today.signal_count == 0):
            blocking.append(ZeroOrderReason.NO_STRATEGY_SIGNAL)

    # ── primary ──
    primary = ZeroOrderReason.NONE
    has_orders = today.virtual_order_count > 0
    if not has_orders:
        for r in _ZERO_ORDER_PRIORITY:
            if r in blocking:
                primary = r
                break

    secondary = [r for r in blocking if r != primary]

    # ── pipeline stages ──
    pipeline = [
        {
            "stage":   "BACKEND",
            "ok":      backend.backend_ready,
            "message": "백엔드 ready" if backend.backend_ready else "백엔드 응답 없음",
        },
        {
            "stage":   "UNIVERSE",
            "ok":      universe.count > 0,
            "message": (
                f"universe {universe.source} · {universe.count}개"
                if universe.count > 0
                else "universe 0건"
            ),
        },
        {
            "stage":   "MARKET_DATA",
            "ok":      bool(provider) and market.last_fetch_ok,
            "message": (
                f"provider={provider or '—'} / last_ok={market.last_fetch_ok}"
            ),
        },
        {
            "stage":   "AUTO_BOT",
            "ok":      auto_bot.is_running,
            "message": (
                f"loop {auto_bot.state} · cycle={auto_bot.cycle_count}"
            ),
        },
        {
            "stage":   "STRATEGY_ENGINE",
            "ok":      auto_bot.strategy_engine_connected,
            "message": (
                "전략 엔진 연결됨" if auto_bot.strategy_engine_connected
                else "전략 엔진 미연동"
            ),
        },
        {
            "stage":   "PERMISSION_GATE",
            "ok":      (
                permission.paper_virtual_execution_allowed
                and permission.last_block_reason is None
            ),
            "message": (
                "PAPER 가상 실행 허용"
                if permission.paper_virtual_execution_allowed
                else "PAPER 가상 실행 차단"
            ),
        },
        {
            "stage":   "RISK_MANAGER",
            "ok":      permission.risk_manager_last_block_reason is None,
            "message": (
                permission.risk_manager_last_block_reason
                or "RiskManager 통과"
            ),
        },
        {
            "stage":   "PAPER_ORDER",
            "ok":      today.virtual_order_count > 0,
            "message": (
                f"오늘 가상주문 {today.virtual_order_count}건"
            ),
        },
    ]
    return primary, secondary, pipeline


# ============================================================================
# Conclusion + next_actions builder
# ============================================================================


def _build_conclusion(
    overall: OverallStatus,
    primary: ZeroOrderReason,
    today:   TodaySummaryInput,
) -> str:
    if today.virtual_order_count > 0 and overall == OverallStatus.HEALTHY:
        return (
            "정상: PAPER 모드에서 후보/신호/가상주문 흐름이 작동 중입니다 "
            f"(오늘 가상주문 {today.virtual_order_count}건)."
        )
    if overall == OverallStatus.STOPPED:
        return "정지: 백엔드 또는 자동봇이 응답하지 않습니다."
    if overall == OverallStatus.ERROR:
        return (
            "오류: " + zero_order_reason_human_ko(primary)
        )
    if overall == OverallStatus.WARN:
        return (
            "주의: " + zero_order_reason_human_ko(primary)
        )
    if primary == ZeroOrderReason.NONE:
        return (
            "정상: PAPER 자동매매 사전 점검 통과 — 직접적 차단 사유 없음."
        )
    return zero_order_reason_human_ko(primary)


def _build_next_actions(
    primary:  ZeroOrderReason,
    secondary: list[ZeroOrderReason],
    universe: UniverseInput,
    desktop:  DesktopEnvInput,
) -> list[str]:
    actions: list[str] = []
    seen: set[ZeroOrderReason] = set()

    def add(r: ZeroOrderReason, msg: str) -> None:
        if r in seen:
            return
        seen.add(r)
        actions.append(msg)

    if primary == ZeroOrderReason.BACKEND_OFFLINE:
        add(primary, "데스크톱 launcher 가 백엔드를 다시 띄우도록 EXE 를 재시작하세요.")
    if primary == ZeroOrderReason.DESKTOP_ENVIRONMENT_ERROR:
        add(primary, ".env 파일 / 로그 폴더 / 포트 점유 상태를 점검하세요.")
    if primary == ZeroOrderReason.MODE_MISMATCH:
        add(primary, "frontend 새로고침 또는 backend 모드 재설정으로 모드 일치를 확인하세요.")
    if primary == ZeroOrderReason.EMERGENCY_STOP:
        add(primary, "긴급정지를 reset 한 뒤 자동봇을 다시 시작하세요.")
    if primary == ZeroOrderReason.NO_UNIVERSE:
        add(primary, "설정 > 관심종목에서 종목을 등록하거나 기본 Universe 를 활성화하세요.")
    if primary == ZeroOrderReason.MARKET_CLOSED:
        add(primary, "한국장 평일 09:00~15:30 KST 시간대에 다시 시작해 보세요.")
    if primary == ZeroOrderReason.AUTO_BOT_NOT_RUNNING:
        add(primary, "자동봇 시작 버튼을 눌러 loop 가 RUNNING 으로 전환되는지 확인하세요.")
    if primary == ZeroOrderReason.STRATEGY_ENGINE_NOT_CONNECTED:
        add(primary, "전략 엔진을 자동봇 backend loop 에 plug 하세요 (handler/consumer 미등록).")
    if primary == ZeroOrderReason.PAPER_EXECUTION_DISABLED:
        add(primary, "PAPER 모드에서 virtual execution 허용 정책을 확인하세요.")
    if primary == ZeroOrderReason.BLOCKED_BY_PERMISSION_GATE:
        add(primary, "PermissionGate 의 last_block_reason 을 점검하세요.")
    if primary == ZeroOrderReason.BLOCKED_BY_RISK_MANAGER:
        add(primary, "RiskManager 의 last_block_reason 을 audit log 에서 확인하세요.")
    if primary == ZeroOrderReason.INSUFFICIENT_PAPER_CASH:
        add(primary, "Paper 시드머니를 늘리거나 종목당 한도를 낮춰 누적 매수가 가능한지 확인하세요.")
    if primary == ZeroOrderReason.NO_MARKET_DATA:
        add(primary, "시장 데이터 provider 연결 상태를 확인하세요.")
    if primary == ZeroOrderReason.NO_CANDIDATE:
        add(primary, "전략 파라미터 / 시장 조건이 후보를 만들 수 있는지 확인하세요.")
    if primary == ZeroOrderReason.NO_STRATEGY_SIGNAL:
        add(primary, "후보는 있으나 신호가 없는 상태 — 전략 파라미터 또는 시간 조건을 점검하세요.")

    # 보조 권고 — fallback universe + 안전 flag 안내.
    if universe.fallback_used:
        actions.append(
            "정식 관심종목 등록을 권장합니다 (fallback 은 PAPER 테스트용)."
        )
    if desktop.is_desktop and not desktop.env_file_present:
        actions.append(
            ".env 파일이 없습니다. API 키 설정이 필요합니다 (실거래는 비활성화 유지)."
        )
    return actions


# ============================================================================
# Main evaluator
# ============================================================================


def evaluate_operation_diagnostics(
    *,
    backend:    BackendStatusInput,
    safety:     SafetyFlagsInput,
    universe:   UniverseInput,
    market:     MarketDataInput,
    auto_bot:   AutoBotInput,
    permission: PermissionInput,
    cash:       PaperCashInput,
    today:      TodaySummaryInput,
    desktop:    DesktopEnvInput,
    frontend:   FrontendModeInput,
    event_summary: dict[str, Any] | None = None,
) -> DiagnosticsReport:
    """단일 진단 리포트 — 모든 입력은 DTO 로 명시 주입 (settings 직접 import 0건)."""

    primary, secondary, pipeline = analyze_zero_order_reason(
        backend=backend, universe=universe, market=market,
        auto_bot=auto_bot, permission=permission, cash=cash,
        today=today, desktop=desktop, safety=safety, frontend=frontend,
    )

    # ── overall status ──
    has_orders = today.virtual_order_count > 0
    blocking_critical = primary in (
        ZeroOrderReason.BACKEND_OFFLINE,
        ZeroOrderReason.DESKTOP_ENVIRONMENT_ERROR,
        ZeroOrderReason.EMERGENCY_STOP,
        ZeroOrderReason.STRATEGY_ENGINE_NOT_CONNECTED,
        ZeroOrderReason.PAPER_EXECUTION_DISABLED,
        ZeroOrderReason.BLOCKED_BY_RISK_MANAGER,
        ZeroOrderReason.BLOCKED_BY_PERMISSION_GATE,
        ZeroOrderReason.MODE_MISMATCH,
    )
    warn_only = primary in (
        ZeroOrderReason.USING_FALLBACK_UNIVERSE,
        ZeroOrderReason.MOCK_MARKET_DATA_ONLY,
        ZeroOrderReason.MARKET_CLOSED,
        ZeroOrderReason.AUTO_BOT_NOT_RUNNING,
        ZeroOrderReason.NO_UNIVERSE,
        ZeroOrderReason.NO_CANDIDATE,
        ZeroOrderReason.NO_STRATEGY_SIGNAL,
        ZeroOrderReason.NO_MARKET_DATA,
        ZeroOrderReason.INSUFFICIENT_PAPER_CASH,
    )

    if not backend.backend_ready:
        overall = OverallStatus.STOPPED
    elif has_orders and not blocking_critical and not warn_only:
        overall = OverallStatus.HEALTHY
    elif blocking_critical:
        overall = OverallStatus.ERROR
    elif primary != ZeroOrderReason.NONE:
        overall = OverallStatus.WARN
    elif today.error_count > 0:
        overall = OverallStatus.ERROR
    elif universe.fallback_used or today.warning_count > 0:
        overall = OverallStatus.WARN
    else:
        overall = OverallStatus.HEALTHY

    fe_mode = (
        (frontend.displayed_mode or "").strip().upper() or None
    )
    be_mode = (safety.default_mode or "").strip().upper() or None
    mode_mismatch = bool(fe_mode and be_mode and fe_mode != be_mode)

    conclusion = _build_conclusion(overall, primary, today)
    next_actions = _build_next_actions(primary, secondary, universe, desktop)

    # ── Desktop check matrix (UI 친화 dict) ──
    desktop_checks: list[dict[str, Any]] = []
    if desktop.is_desktop:
        desktop_checks = [
            {"name": "backend_process_alive", "ok": desktop.backend_process_alive,
             "message_ko": "백엔드 프로세스 실행 중"
                if desktop.backend_process_alive
                else "백엔드 프로세스가 실행 중이 아닙니다."},
            {"name": "backend_port_reachable", "ok": desktop.backend_port_reachable,
             "message_ko": "백엔드 포트 접근 가능"
                if desktop.backend_port_reachable
                else "백엔드는 실행 중이나 frontend에서 접근할 수 없습니다."},
            {"name": "env_file_present", "ok": desktop.env_file_present,
             "message_ko": ".env 파일 존재 (내용은 표시하지 않습니다)"
                if desktop.env_file_present
                else ".env 파일이 없습니다. API 키 설정이 필요합니다."},
            {"name": "logs_dir_writable", "ok": desktop.logs_dir_writable,
             "message_ko": "로그 폴더 쓰기 가능"
                if desktop.logs_dir_writable
                else "로그 폴더에 쓰기 권한이 없습니다."},
            {"name": "app_data_dir_writable", "ok": desktop.app_data_dir_writable,
             "message_ko": "앱 데이터 폴더 쓰기 가능"
                if desktop.app_data_dir_writable
                else "앱 데이터 폴더에 쓰기 권한이 없습니다."},
            {"name": "launcher_ok", "ok": desktop.launcher_ok,
             "message_ko": "desktop mode 에서 backend launcher 가 정상 작동했습니다."
                if desktop.launcher_ok
                else "desktop launcher 가 비정상 종료되었습니다."},
        ]

    return DiagnosticsReport(
        overall_status=overall,
        conclusion_ko=conclusion,
        next_actions_ko=tuple(next_actions),
        default_mode=str(safety.default_mode or ""),
        enable_live_trading=bool(safety.enable_live_trading),
        enable_ai_execution=bool(safety.enable_ai_execution),
        enable_futures_live_trading=bool(safety.enable_futures_live_trading),
        kis_is_paper=bool(safety.kis_is_paper),
        market_data_provider=str(safety.market_data_provider or ""),
        frontend_mode=fe_mode,
        mode_mismatch=mode_mismatch,
        backend_ready=bool(backend.backend_ready),
        db_ready=bool(backend.db_ready),
        migration_state=str(backend.migration_state or ""),
        universe_source=str(universe.source or ""),
        universe_count=int(universe.count),
        universe_fallback_used=bool(universe.fallback_used),
        universe_warning_ko=str(universe.warning_ko or ""),
        market_data_last_fetch_ok=bool(market.last_fetch_ok),
        market_data_last_fetch_at=market.last_fetch_at,
        market_data_stale_symbols=int(market.stale_symbols),
        auto_bot_state=str(auto_bot.state),
        auto_bot_running=bool(auto_bot.is_running),
        auto_bot_cycle_count=int(auto_bot.cycle_count),
        auto_bot_last_decision_count=int(auto_bot.last_decision_count),
        auto_bot_last_ledger_events=int(auto_bot.last_ledger_events),
        auto_bot_last_error=auto_bot.last_error,
        strategy_engine_connected=bool(auto_bot.strategy_engine_connected),
        paper_virtual_execution_allowed=bool(permission.paper_virtual_execution_allowed),
        live_execution_blocked=bool(permission.live_execution_blocked),
        permission_last_block_reason=permission.last_block_reason,
        risk_manager_last_block_reason=permission.risk_manager_last_block_reason,
        paper_cash_available_krw=int(cash.available_cash_krw),
        paper_cash_insufficient_today=bool(cash.insufficient_today),
        today_candidate_count=int(today.candidate_count),
        today_signal_count=int(today.signal_count),
        today_approved_candidate_count=int(today.approved_candidate_count),
        today_rejected_candidate_count=int(today.rejected_candidate_count),
        today_virtual_order_count=int(today.virtual_order_count),
        today_blocked_order_count=int(today.blocked_order_count),
        today_error_count=int(today.error_count),
        today_warning_count=int(today.warning_count),
        has_orders_today=has_orders,
        zero_order_primary_reason=primary,
        zero_order_primary_message=zero_order_reason_human_ko(primary),
        zero_order_secondary_reasons=tuple(secondary),
        zero_order_pipeline_stages=tuple(pipeline),
        is_desktop=bool(desktop.is_desktop),
        desktop_checks=tuple(desktop_checks),
        desktop_notes=tuple(desktop.notes),
        event_summary=dict(event_summary or {}),
    )


__all__ = [
    "OverallStatus",
    "ZeroOrderReason",
    "zero_order_reason_human_ko",
    "SafetyFlagsInput",
    "BackendStatusInput",
    "UniverseInput",
    "MarketDataInput",
    "AutoBotInput",
    "PermissionInput",
    "PaperCashInput",
    "TodaySummaryInput",
    "DesktopEnvInput",
    "FrontendModeInput",
    "DiagnosticsReport",
    "analyze_zero_order_reason",
    "evaluate_operation_diagnostics",
]
