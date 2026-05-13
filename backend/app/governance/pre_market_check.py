"""Pre-market Check (#80) — 장 시작 전 자동 점검.

API / DB / Broker / Data Freshness / Watchlist / Strategy / Risk / KillSwitch /
Agent / Notifications / Governance Gates 상태를 read-only 로 점검 후, 모드별
*required* check 가 하나라도 실패하면 `start_allowed=False` 반환.

CLAUDE.md 절대 원칙:
- 본 모듈은 *read-only*. broker / OrderExecutor / route_order 호출 0건.
- 본 모듈은 *주문 / 모드 / 안전 플래그를 변경하지 않는다*. 결과는 *제안*만.
- 운영자 manual ack 는 *FAIL 우회 불가* — 단지 UI 상태 / 기록용.
- `start_allowed` 는 *코드 단 결정값* — 실제 자동매매 시작은 별도 흐름
  (BotControl)이 본 결과를 참고해 결정.

invariant:
- `PreMarketCheckResult.is_order_signal=False` 항상.
- `PreMarketCheckResult.live_flag_changed=False` 항상.
- `PreMarketCheckResult.mode_changed=False` 항상.
- required FAIL 1건 이상 → `start_allowed=False` 영구.
- manual_ack=True 라도 required FAIL 이 있으면 start_allowed=False 유지.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ---------- enums ----------


class CheckCategory(StrEnum):
    """11 카테고리."""
    API           = "api"
    DB            = "db"
    BROKER        = "broker"
    DATA          = "data"
    WATCHLIST     = "watchlist"
    STRATEGY      = "strategy"
    RISK          = "risk"
    KILL_SWITCH   = "kill_switch"
    AGENT         = "agent"
    NOTIFICATION  = "notification"
    GOVERNANCE    = "governance"


class CheckStatus(StrEnum):
    PASS    = "PASS"
    WARN    = "WARN"
    FAIL    = "FAIL"
    SKIP    = "SKIP"     # mode 에 해당 없음
    UNKNOWN = "UNKNOWN"  # 데이터 부족


class PreMarketVerdict(StrEnum):
    READY_TO_START         = "READY_TO_START"
    WARN_BUT_START_ALLOWED = "WARN_BUT_START_ALLOWED"
    DO_NOT_START           = "DO_NOT_START"


# ---------- DTO ----------


@dataclass(frozen=True)
class CheckItem:
    """단일 점검 결과.

    `required=True` 인 항목이 FAIL 이면 `start_allowed=False`. WARN 은 시작을
    막지 않지만 운영자 확인 권장.
    """
    name:        str
    category:    CheckCategory
    status:      CheckStatus
    required:    bool        = True
    message:     str         = ""
    detail:      dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":     self.name,
            "category": self.category.value,
            "status":   self.status.value,
            "required": self.required,
            "message":  self.message,
            "detail":   dict(self.detail),
        }


@dataclass(frozen=True)
class PreMarketCheckInput:
    """점검 입력 — 호출자(API endpoint / CLI / collector)가 채워서 전달.

    모든 필드는 *현재값 carry* — 본 모듈은 어떤 값도 *변경하지 않는다*.
    """
    mode:                              str   = "SIMULATION"
    strict:                            bool  = False
    include_optional:                  bool  = True

    # API / DB.
    api_reachable:                     bool  = True
    db_reachable:                      bool  = True

    # Broker.
    broker_ready:                      bool | None = None
    kis_is_paper:                      bool  = True
    kis_credentials_present:           bool | None = None    # PAPER/LIVE 모드만 의미

    # Data freshness.
    market_data_provider:              str   = "mock"
    data_freshness_ok:                 bool | None = None
    stale_symbol_count:                int   = 0

    # Watchlist.
    watchlist_item_count:              int   = 0

    # Strategy.
    active_strategy_count:             int   = 0

    # Risk.
    risk_policy_configured:            bool  = True
    daily_loss_limit_configured:       bool  = True
    daily_loss_used_ratio:             float = 0.0      # 0~1
    position_limits_configured:        bool  = True

    # KillSwitch.
    emergency_stop_active:             bool  = False
    kill_switch_level:                 str   = "OFF"    # OFF/LEVEL_1/LEVEL_2/LEVEL_3

    # Agent / AI.
    ai_permission_gate_active:         bool  = True
    ai_execution_enabled:              bool  = False
    enable_live_trading:               bool  = False
    enable_futures_live_trading:       bool  = False

    # Notification.
    notification_configured:           bool  = False

    # Governance gates (pre-existing verdicts carried from #72/#73/#74/#75).
    paper_gate_pass:                   bool | None = None
    live_manual_gate_pass:             bool | None = None
    ai_assist_gate_pass:               bool | None = None
    ai_execution_gate_ready:           bool | None = None

    # Operator manual ack — UI 표시용. 본 모듈은 *FAIL 우회 불가*.
    manual_ack:                        bool  = False
    manual_ack_by:                     str   = ""
    manual_ack_note:                   str   = ""


@dataclass
class PreMarketCheckResult:
    """점검 결과.

    invariants:
    - `is_order_signal=False` 항상.
    - `live_flag_changed=False` / `mode_changed=False` 항상.
    - manual_ack=True 라도 required FAIL 이 있으면 `start_allowed=False`.
    """
    mode:                    str
    verdict:                 PreMarketVerdict
    start_allowed:           bool
    items:                   list[CheckItem] = field(default_factory=list)
    failed_required:         list[str] = field(default_factory=list)
    warnings:                list[str] = field(default_factory=list)
    required_actions:        list[str] = field(default_factory=list)
    manual_ack_recorded:     bool      = False
    manual_ack_by:           str       = ""
    manual_ack_note:         str       = ""
    is_order_signal:         bool      = False
    live_flag_changed:       bool      = False
    mode_changed:            bool      = False
    generated_at:            datetime  = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError(
                "PreMarketCheckResult.is_order_signal must be False — "
                "this gate does not produce BUY/SELL/HOLD signals."
            )
        if self.live_flag_changed is not False:
            raise ValueError(
                "PreMarketCheckResult.live_flag_changed must be False — "
                "this gate does not mutate safety flags."
            )
        if self.mode_changed is not False:
            raise ValueError(
                "PreMarketCheckResult.mode_changed must be False — "
                "this gate does not change operation mode."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode":                self.mode,
            "verdict":             self.verdict.value,
            "start_allowed":       self.start_allowed,
            "items":               [it.to_dict() for it in self.items],
            "failed_required":     list(self.failed_required),
            "warnings":            list(self.warnings),
            "required_actions":    list(self.required_actions),
            "manual_ack_recorded": self.manual_ack_recorded,
            "manual_ack_by":       self.manual_ack_by,
            "manual_ack_note":     self.manual_ack_note,
            "is_order_signal":     self.is_order_signal,
            "live_flag_changed":   self.live_flag_changed,
            "mode_changed":        self.mode_changed,
            "generated_at":        self.generated_at.isoformat(),
        }


# ---------- mode 별 required matrix ----------


_LIVE_MODES = frozenset({
    "LIVE_SHADOW", "LIVE_MANUAL_APPROVAL", "LIVE_AI_ASSIST", "LIVE_AI_EXECUTION",
})


def _is_live(mode: str) -> bool:
    return mode.upper() in _LIVE_MODES


def _required_for_mode(mode: str) -> dict[str, bool]:
    """mode → check name → required(bool).

    필수 항목 매트릭스:
    - SIMULATION    : api / db / watchlist
    - PAPER         : + broker_paper / data_freshness / risk
    - LIVE_SHADOW   : + broker_live_readonly / kill_switch
    - LIVE_MANUAL   : + risk / paper_gate_pass / live_manual_gate_pass
    - LIVE_AI_ASSIST: + ai_permission_gate / ai_assist_gate_pass
    - LIVE_AI_EXEC  : + ai_execution_gate_ready (영구 BLOCKED — 본 게이트는
                       항상 READY_FOR_REVIEW 여야 하며, 활성화는 별도)
    """
    mode = mode.upper()

    # 항상 필수 (모든 모드).
    base = {
        "api":                  True,
        "db":                   True,
        "watchlist":            True,
        "risk_policy":          True,
        "kill_switch":          True,
        "notification":         False,   # 항상 optional
    }

    if mode == "SIMULATION":
        return base
    if mode == "PAPER":
        return {
            **base,
            "broker_paper":        True,
            "data_freshness":      True,
            "daily_loss_limit":    True,
        }
    if mode == "LIVE_SHADOW":
        return {
            **base,
            "broker_live_readonly": True,
            "data_freshness":       True,
            "daily_loss_limit":     True,
        }
    if mode == "LIVE_MANUAL_APPROVAL":
        return {
            **base,
            "broker_live_readonly":   True,
            "data_freshness":         True,
            "daily_loss_limit":       True,
            "paper_gate":             True,
            "live_manual_gate":       True,
        }
    if mode == "LIVE_AI_ASSIST":
        return {
            **base,
            "broker_live_readonly":   True,
            "data_freshness":         True,
            "daily_loss_limit":       True,
            "ai_permission_gate":     True,
            "paper_gate":             True,
            "live_manual_gate":       True,
            "ai_assist_gate":         True,
        }
    if mode == "LIVE_AI_EXECUTION":
        return {
            **base,
            "broker_live_readonly":   True,
            "data_freshness":         True,
            "daily_loss_limit":       True,
            "ai_permission_gate":     True,
            "paper_gate":             True,
            "live_manual_gate":       True,
            "ai_assist_gate":         True,
            "ai_execution_gate":      True,
        }
    # 기타 / 미지정 → 보수적으로 base.
    return base


# ---------- evaluator ----------


def evaluate_pre_market_check(inp: PreMarketCheckInput) -> PreMarketCheckResult:
    """점검 실행. 외부 시스템 영향 0건.

    `start_allowed` 는 required FAIL 0건일 때만 True.
    manual_ack 는 *기록*만 — required FAIL 우회 불가.
    """
    mode = (inp.mode or "SIMULATION").upper()
    req  = _required_for_mode(mode)
    items: list[CheckItem] = []

    def add(name: str, cat: CheckCategory, status: CheckStatus,
            message: str = "", **detail: Any) -> None:
        items.append(CheckItem(
            name=name, category=cat, status=status,
            required=bool(req.get(name, False)),
            message=message, detail=detail,
        ))

    # ---- API / DB ----
    add(
        "api", CheckCategory.API,
        CheckStatus.PASS if inp.api_reachable else CheckStatus.FAIL,
        "API 응답 OK" if inp.api_reachable else "API 응답 실패",
    )
    add(
        "db", CheckCategory.DB,
        CheckStatus.PASS if inp.db_reachable else CheckStatus.FAIL,
        "DB ping OK" if inp.db_reachable else "DB ping 실패",
    )

    # ---- Broker ----
    if mode == "PAPER":
        # KIS_IS_PAPER=true + credentials 있어야 함.
        if inp.broker_ready is None:
            add("broker_paper", CheckCategory.BROKER, CheckStatus.UNKNOWN,
                "broker 상태 입력 없음 — 미확인")
        elif not inp.broker_ready:
            add("broker_paper", CheckCategory.BROKER, CheckStatus.FAIL,
                "PAPER broker 준비 안 됨")
        elif not inp.kis_is_paper:
            add("broker_paper", CheckCategory.BROKER, CheckStatus.FAIL,
                "PAPER 모드인데 KIS_IS_PAPER=false — 시작 금지")
        elif inp.kis_credentials_present is False:
            add("broker_paper", CheckCategory.BROKER, CheckStatus.FAIL,
                "KIS credential 미입력")
        else:
            add("broker_paper", CheckCategory.BROKER, CheckStatus.PASS,
                "PAPER broker 준비 OK")
    elif _is_live(mode):
        # LIVE_* 는 broker read-only 라도 준비돼야 함.
        if inp.broker_ready is None:
            add("broker_live_readonly", CheckCategory.BROKER, CheckStatus.UNKNOWN,
                "broker 상태 입력 없음 — 미확인")
        elif not inp.broker_ready:
            add("broker_live_readonly", CheckCategory.BROKER, CheckStatus.FAIL,
                "LIVE broker (read-only) 준비 안 됨")
        elif inp.kis_credentials_present is False:
            add("broker_live_readonly", CheckCategory.BROKER, CheckStatus.FAIL,
                "KIS credential 미입력")
        else:
            add("broker_live_readonly", CheckCategory.BROKER, CheckStatus.PASS,
                "LIVE broker (read-only) 준비 OK")
    else:
        # SIMULATION: broker 검사 skip.
        add("broker_sim", CheckCategory.BROKER, CheckStatus.SKIP,
            "SIMULATION mode — broker 검사 skip")

    # ---- Data Freshness ----
    if mode == "SIMULATION":
        add("data_freshness", CheckCategory.DATA, CheckStatus.SKIP,
            "SIMULATION mode — freshness 검사 skip",
            provider=inp.market_data_provider)
    else:
        if inp.data_freshness_ok is None:
            add("data_freshness", CheckCategory.DATA, CheckStatus.UNKNOWN,
                "freshness 입력 없음 — 미확인",
                provider=inp.market_data_provider)
        elif not inp.data_freshness_ok:
            add("data_freshness", CheckCategory.DATA, CheckStatus.FAIL,
                f"freshness 위반 (stale 종목 {inp.stale_symbol_count}개)",
                provider=inp.market_data_provider,
                stale_count=inp.stale_symbol_count)
        elif inp.stale_symbol_count > 0:
            add("data_freshness", CheckCategory.DATA, CheckStatus.WARN,
                f"freshness OK 이지만 stale 종목 {inp.stale_symbol_count}개 존재",
                provider=inp.market_data_provider,
                stale_count=inp.stale_symbol_count)
        else:
            add("data_freshness", CheckCategory.DATA, CheckStatus.PASS,
                "freshness OK",
                provider=inp.market_data_provider)

    # ---- Watchlist ----
    if inp.watchlist_item_count <= 0:
        add("watchlist", CheckCategory.WATCHLIST, CheckStatus.FAIL,
            "Watchlist 비어 있음 — 종목 추가 필요")
    else:
        add("watchlist", CheckCategory.WATCHLIST, CheckStatus.PASS,
            f"Watchlist {inp.watchlist_item_count}개 종목")

    # ---- Strategy ----
    is_strategy_required = mode != "SIMULATION"
    if inp.active_strategy_count <= 0:
        status = CheckStatus.WARN if not is_strategy_required else CheckStatus.FAIL
        msg = "활성 전략 0개"
    else:
        status = CheckStatus.PASS
        msg = f"활성 전략 {inp.active_strategy_count}개"
    # strategy 는 required 매트릭스에 base 로 안 들어가 있음 — 모드별로 처리.
    items.append(CheckItem(
        name="strategy", category=CheckCategory.STRATEGY, status=status,
        required=is_strategy_required, message=msg,
        detail={"count": inp.active_strategy_count},
    ))

    # ---- Risk ----
    if not inp.risk_policy_configured:
        add("risk_policy", CheckCategory.RISK, CheckStatus.FAIL,
            "RiskPolicy 미설정")
    else:
        add("risk_policy", CheckCategory.RISK, CheckStatus.PASS,
            "RiskPolicy 설정 OK")

    if mode != "SIMULATION":
        if not inp.daily_loss_limit_configured:
            add("daily_loss_limit", CheckCategory.RISK, CheckStatus.FAIL,
                "일일 손실한도 미설정")
        elif inp.daily_loss_used_ratio >= 1.0:
            add("daily_loss_limit", CheckCategory.RISK, CheckStatus.FAIL,
                f"일일 손실한도 이미 초과 ({inp.daily_loss_used_ratio:.0%})",
                used_ratio=inp.daily_loss_used_ratio)
        elif inp.daily_loss_used_ratio >= 0.8:
            add("daily_loss_limit", CheckCategory.RISK, CheckStatus.WARN,
                f"일일 손실한도 {inp.daily_loss_used_ratio:.0%} 사용 — WARN",
                used_ratio=inp.daily_loss_used_ratio)
        else:
            add("daily_loss_limit", CheckCategory.RISK, CheckStatus.PASS,
                f"일일 손실한도 {inp.daily_loss_used_ratio:.0%} 사용",
                used_ratio=inp.daily_loss_used_ratio)
    else:
        add("daily_loss_limit", CheckCategory.RISK, CheckStatus.SKIP,
            "SIMULATION mode — daily loss limit 검사 skip")

    if not inp.position_limits_configured:
        add("position_limits", CheckCategory.RISK, CheckStatus.WARN,
            "PositionLimitRule 미설정 — 운영자 확인 권장")

    # ---- KillSwitch ----
    if inp.emergency_stop_active:
        add("kill_switch", CheckCategory.KILL_SWITCH, CheckStatus.FAIL,
            f"emergency_stop 활성 ({inp.kill_switch_level}) — 시작 금지",
            level=inp.kill_switch_level)
    else:
        add("kill_switch", CheckCategory.KILL_SWITCH, CheckStatus.PASS,
            "emergency_stop OFF")

    # ---- AI Permission / Execution ----
    if mode in ("LIVE_AI_ASSIST", "LIVE_AI_EXECUTION"):
        if not inp.ai_permission_gate_active:
            add("ai_permission_gate", CheckCategory.AGENT, CheckStatus.FAIL,
                "AI Permission Gate(#39) 비활성")
        else:
            add("ai_permission_gate", CheckCategory.AGENT, CheckStatus.PASS,
                "AI Permission Gate 활성")

    if mode == "LIVE_AI_EXECUTION":
        if inp.ai_execution_gate_ready is None:
            add("ai_execution_gate", CheckCategory.AGENT, CheckStatus.UNKNOWN,
                "AI Execution Gate readiness 입력 없음")
        elif not inp.ai_execution_gate_ready:
            add("ai_execution_gate", CheckCategory.AGENT, CheckStatus.FAIL,
                "AI Execution Gate(#75) READY_FOR_REVIEW 아님 — 시작 금지")
        else:
            add("ai_execution_gate", CheckCategory.AGENT, CheckStatus.PASS,
                "AI Execution Gate READY_FOR_REVIEW")

    # ---- Notification (always optional) ----
    if not inp.notification_configured:
        add("notification", CheckCategory.NOTIFICATION, CheckStatus.WARN,
            "Notification 미설정 — 사고 발생 시 알림 미수신")
    else:
        add("notification", CheckCategory.NOTIFICATION, CheckStatus.PASS,
            "Notification 설정 OK")

    # ---- Governance gates ----
    if mode in ("LIVE_MANUAL_APPROVAL", "LIVE_AI_ASSIST", "LIVE_AI_EXECUTION"):
        if inp.paper_gate_pass is None:
            add("paper_gate", CheckCategory.GOVERNANCE, CheckStatus.UNKNOWN,
                "Paper Gate(#72) verdict 입력 없음")
        elif not inp.paper_gate_pass:
            add("paper_gate", CheckCategory.GOVERNANCE, CheckStatus.FAIL,
                "Paper Gate(#72) 미통과")
        else:
            add("paper_gate", CheckCategory.GOVERNANCE, CheckStatus.PASS,
                "Paper Gate(#72) PASS")

        if inp.live_manual_gate_pass is None:
            add("live_manual_gate", CheckCategory.GOVERNANCE, CheckStatus.UNKNOWN,
                "Live Manual Gate(#73) verdict 입력 없음")
        elif not inp.live_manual_gate_pass:
            add("live_manual_gate", CheckCategory.GOVERNANCE, CheckStatus.FAIL,
                "Live Manual Gate(#73) 미통과")
        else:
            add("live_manual_gate", CheckCategory.GOVERNANCE, CheckStatus.PASS,
                "Live Manual Gate(#73) PASS")

    if mode in ("LIVE_AI_ASSIST", "LIVE_AI_EXECUTION"):
        if inp.ai_assist_gate_pass is None:
            add("ai_assist_gate", CheckCategory.GOVERNANCE, CheckStatus.UNKNOWN,
                "AI Assist Gate(#74) verdict 입력 없음")
        elif not inp.ai_assist_gate_pass:
            add("ai_assist_gate", CheckCategory.GOVERNANCE, CheckStatus.FAIL,
                "AI Assist Gate(#74) 미통과")
        else:
            add("ai_assist_gate", CheckCategory.GOVERNANCE, CheckStatus.PASS,
                "AI Assist Gate(#74) PASS")

    # ---- 추가 LIVE invariant 경고 ----
    if mode == "LIVE_AI_EXECUTION" and not inp.ai_execution_enabled:
        items.append(CheckItem(
            name="ai_execution_flag", category=CheckCategory.AGENT,
            status=CheckStatus.FAIL, required=True,
            message="ENABLE_AI_EXECUTION=false — LIVE_AI_EXECUTION 모드 진행 불가",
        ))
    if _is_live(mode) and mode != "LIVE_SHADOW" and not inp.enable_live_trading:
        items.append(CheckItem(
            name="live_trading_flag", category=CheckCategory.AGENT,
            status=CheckStatus.FAIL, required=True,
            message="ENABLE_LIVE_TRADING=false — LIVE 모드 진행 불가",
        ))
    if inp.enable_futures_live_trading:
        items.append(CheckItem(
            name="futures_live_flag", category=CheckCategory.AGENT,
            status=CheckStatus.FAIL, required=True,
            message="ENABLE_FUTURES_LIVE_TRADING=true — 본 게이트는 선물 LIVE 미허용",
        ))

    # ---- aggregate ----
    failed_required = [it.name for it in items
                       if it.required and it.status == CheckStatus.FAIL]
    warnings = [
        f"{it.name}: {it.message}" for it in items
        if it.status == CheckStatus.WARN
    ]

    # strict 모드: UNKNOWN(required) 도 FAIL 로 취급.
    if inp.strict:
        unknown_required = [
            it.name for it in items
            if it.required and it.status == CheckStatus.UNKNOWN
        ]
        failed_required.extend(unknown_required)

    start_allowed = not failed_required

    if not start_allowed:
        verdict = PreMarketVerdict.DO_NOT_START
    elif warnings:
        verdict = PreMarketVerdict.WARN_BUT_START_ALLOWED
    else:
        verdict = PreMarketVerdict.READY_TO_START

    actions: list[str] = []
    if failed_required:
        actions.append("required FAIL 항목을 모두 해결 후 재점검.")
    if warnings:
        actions.append("WARN 항목 운영자 확인 권장.")
    if inp.manual_ack and failed_required:
        actions.append(
            "manual_ack 가 기록되었으나 required FAIL 이 있어 시작은 금지됩니다."
        )

    return PreMarketCheckResult(
        mode=mode,
        verdict=verdict,
        start_allowed=start_allowed,
        items=items,
        failed_required=failed_required,
        warnings=warnings,
        required_actions=actions,
        manual_ack_recorded=bool(inp.manual_ack),
        manual_ack_by=(inp.manual_ack_by or "")[:64],
        manual_ack_note=(inp.manual_ack_note or "")[:500],
    )


# ---------- markdown ----------


def render_markdown_report(result: PreMarketCheckResult) -> str:
    lines: list[str] = []
    lines.append(f"# Pre-market Check Report — {result.mode}")
    lines.append("")
    lines.append(f"_생성: {result.generated_at.isoformat()}_")
    lines.append("")
    lines.append("> ⚠️ 본 보고는 *자동매매 시작 전 안전 점검*입니다. "
                 "주문 / 모드 / 안전 플래그를 변경하지 않습니다.")
    lines.append("")
    lines.append(f"## 판정: **{result.verdict.value}**")
    lines.append("")
    lines.append(
        f"- start_allowed: **{result.start_allowed}**"
    )
    if result.failed_required:
        lines.append("")
        lines.append("## 실패 항목 (required FAIL)")
        for n in result.failed_required:
            lines.append(f"- 🛑 `{n}`")
    if result.warnings:
        lines.append("")
        lines.append("## 경고 (WARN)")
        for w in result.warnings:
            lines.append(f"- ⚠️ {w}")
    if result.required_actions:
        lines.append("")
        lines.append("## 필요 조치")
        for a in result.required_actions:
            lines.append(f"- 📝 {a}")
    lines.append("")
    lines.append("## 항목 상세")
    lines.append("")
    lines.append("| name | category | status | required | message |")
    lines.append("|---|---|---|---|---|")
    for it in result.items:
        lines.append(
            f"| `{it.name}` | {it.category.value} | {it.status.value} |"
            f" {it.required} | {it.message} |"
        )
    if result.manual_ack_recorded:
        lines.append("")
        lines.append("## 운영자 manual_ack")
        lines.append(f"- by: {result.manual_ack_by or '(미지정)'}")
        if result.manual_ack_note:
            lines.append(f"- note: {result.manual_ack_note}")
        lines.append(
            "- 본 ack 는 *기록*만 — required FAIL 우회 불가."
        )
    lines.append("")
    lines.append(
        "---\n본 보고서는 *판단 보조 자료*입니다. RiskManager / "
        "PermissionGate / OrderExecutor 우회 금지. 본 점검은 안전 플래그를 "
        "변경하지 않습니다."
    )
    return "\n".join(lines)
