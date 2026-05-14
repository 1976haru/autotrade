"""Release Readiness Report (#92) — *advisory* meta-aggregator.

운영자가 "지금 새 릴리스 태그를 찍어도 되는가 / 다음 promotion 단계로 검토
가능한가"를 판단할 수 있는 *단일 advisory 리포트*. 기존 governance gates
(#72 Paper / #73 Live Manual / #74 AI Assist / #75 AI Execution) +
#80/#91 Pre-market Check + #77 Alpha Decay + #90 Desktop EXE 빌드 상태 +
recent activity metrics 를 *입력 DTO로 carry* 받아 종합 verdict 를 만든다.

본 모듈은 *어떤 gate evaluator 도 직접 호출하지 않는다* — 호출자(API endpoint,
CLI, dashboard)가 각 gate 의 결과를 *라벨 / boolean 으로 요약*해서 전달한다.
이로써 release_readiness 는 *순수한 read-only meta-aggregator* 가 되어 broker /
DB / 외부 시스템과 완전 분리.

CLAUDE.md 절대 원칙 — 본 모듈은 외부 시스템과 완전 분리된 *순수 함수*:

- broker / executor / 외부 HTTP / AI SDK / settings reader 직접 사용 0건.
- 다른 governance gate evaluator 함수 직접 호출 0건 — 호출자가 결과를 라벨로
  carry 받아 전달.
- 데이터베이스 쓰기 작업 0건 (모든 정적 grep 가드는 test 모듈에 정의).
- 안전 flag (실거래 / AI 자동실행 / 선물 LIVE) 변경 0건.
- ReleaseReadinessResult 생성 시 invariants ValueError 강제:
  is_live_authorization / auto_apply_allowed / is_order_signal /
  live_flag_changed / mode_changed 모두 False 만 허용.

verdict 4단계:

- `READY_TO_TAG`         : 모든 required PASS + WARN 0건 → 운영자 *검토 가능*
- `READY_WITH_CAVEATS`   : 모든 required PASS + WARN 1건 이상 → 검토 가능하지만 주의
- `DO_NOT_TAG`           : required FAIL 1건 이상 → 태그 금지
- `INSUFFICIENT_DATA`    : required UNKNOWN 만 있고 FAIL 0건 → 데이터 보강 필요

**READY_TO_TAG 는 *실거래 활성화 / 자동 promotion 이 아니다*** — 운영자가 본
리포트를 *직접 확인*하고, 별도 PR (release tag + GitHub release + 베타테스터
공지)를 거쳐서만 진행. 본 게이트는 그 *판단의 보조 자료*만 제공한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ---------- enums ----------


class ReleaseReadinessVerdict(StrEnum):
    """Release Readiness 4단계 verdict.

    READY_TO_TAG 는 *실거래 활성화가 아니다* — 운영자가 별도 PR 로 release
    tag 를 찍을지 판단할 수 있다는 의미만.
    """
    READY_TO_TAG        = "READY_TO_TAG"
    READY_WITH_CAVEATS  = "READY_WITH_CAVEATS"
    DO_NOT_TAG          = "DO_NOT_TAG"
    INSUFFICIENT_DATA   = "INSUFFICIENT_DATA"


class ReadinessSeverity(StrEnum):
    """단일 점검 항목 severity. BUY/SELL/HOLD 값 0개 — 본 모듈은 주문 신호가 아니다."""
    PASS     = "PASS"
    WARN     = "WARN"
    FAIL     = "FAIL"
    SKIP     = "SKIP"
    UNKNOWN  = "UNKNOWN"


class ReadinessCategory(StrEnum):
    """10 카테고리 — 카테고리별 색상 / 그룹핑 용도."""
    SAFETY_FLAGS      = "safety_flags"
    GOVERNANCE_GATES  = "governance_gates"
    PRE_MARKET        = "pre_market"
    STRATEGY_HEALTH   = "strategy_health"
    DESKTOP_BUILD     = "desktop_build"
    SYSTEM_HYGIENE    = "system_hygiene"
    DOCUMENTATION     = "documentation"
    DATA_FRESHNESS    = "data_freshness"
    RECENT_ACTIVITY   = "recent_activity"
    OPERATOR          = "operator"


class ReleaseKind(StrEnum):
    """릴리스 종류. required 매트릭스가 단계별로 강화된다.

    - BETA       : 베타테스터 공유용 (최소 요건)
    - RC         : 안정화 후보 (governance gates / 전략 건강 / pre-market 추가)
    - STABLE     : 정식 릴리스 (Live Manual Gate / 운영자 명시 opt-in 추가)
    """
    BETA    = "BETA"
    RC      = "RC"
    STABLE  = "STABLE"


# ---------- thresholds ----------


@dataclass(frozen=True)
class ReleaseReadinessThresholds:
    """Release Readiness 임계. 운영자가 평가 시 override 가능."""
    # System hygiene.
    audit_warn_days:                   int = 30   # 마지막 시스템 hygiene audit 30일 초과 시 WARN
    audit_fail_days:                   int = 90   # 90일 초과 시 FAIL

    # Recent activity (최근 7일).
    max_loss_limit_violations_7d:      int = 0
    max_emergency_stop_events_7d_warn: int = 2
    max_audit_missing_7d:              int = 0

    # Test coverage (선택적 carry).
    min_test_pass_rate_pct_warn:       float = 95.0
    min_test_pass_rate_pct_fail:       float = 80.0

    # Alpha decay (#77).
    max_alpha_disable_candidates_warn: int = 0   # > 0 면 WARN
    max_alpha_disable_candidates_fail: int = 3   # > 3 면 FAIL (운영 전략 중 다수가 disable 후보)

    # 베타 단계: operator opt-in 불필요. RC / STABLE 은 필요.
    require_operator_opt_in_for_rc:    bool = True
    require_operator_opt_in_for_stable: bool = True


# ---------- input DTO ----------


@dataclass(frozen=True)
class ReleaseReadinessInput:
    """Release Readiness 평가 입력.

    호출자가 각 gate / report / 시스템 상태를 *라벨 / boolean* 으로 carry.
    본 모듈은 어떤 gate evaluator 도 직접 호출하지 않는다 — 호출자가 별도로
    호출 후 결과를 전달.

    모든 필드는 *현재값 carry* — secret 원문 0건, broker 호출 0건.
    """
    # ---- 대상 릴리스 정보 ----
    target_release_tag:                str         = "v0.0.0-unspecified"
    release_kind:                      str         = ReleaseKind.BETA.value
    strict:                            bool        = False

    # ---- 안전 flag 상태 (현재값 carry, 본 모듈은 변경 X) ----
    kis_is_paper:                      bool        = True
    enable_live_trading:               bool        = False
    enable_ai_execution:               bool        = False
    enable_futures_live_trading:       bool        = False

    # ---- governance gate verdict carry ----
    # 각 gate evaluator 의 verdict 라벨 (예: "PASS", "FAIL", "CAUTION", "UNKNOWN",
    # "READY_FOR_REVIEW", "BLOCKED"). 미평가 시 None.
    paper_gate_verdict:                str | None  = None
    live_manual_gate_verdict:          str | None  = None
    ai_assist_gate_verdict:            str | None  = None
    ai_execution_gate_verdict:         str | None  = None

    # ---- pre-market (#80 / #91) ----
    pre_market_verdict:                str | None  = None
    pre_market_start_allowed:          bool | None = None

    # ---- strategy health (#77 alpha decay) ----
    alpha_decay_worst_status:          str | None  = None   # HEALTHY/WATCH/DECAY_WARNING/DISABLE_CANDIDATE
    alpha_decay_disable_candidate_count: int       = 0
    alpha_decay_strategies_evaluated:  int         = 0

    # ---- desktop build (#90 / 90-A) ----
    desktop_sidecar_built:             bool | None = None   # backend/dist/autotrade-backend.exe 존재
    desktop_installer_built:           bool | None = None   # *.msi / *-setup.exe 존재

    # ---- system hygiene (#88) ----
    last_system_audit_at:              datetime | None = None
    repository_hygiene_pass:           bool | None = None

    # ---- documentation ----
    documentation_coverage_ok:         bool | None = None

    # ---- data freshness (#20) ----
    data_freshness_ok:                 bool | None = None
    market_data_provider:              str         = "mock"

    # ---- recent activity (최근 7일) ----
    recent_paper_trade_count_7d:       int         = 0
    recent_paper_active_days_7d:       int         = 0
    recent_loss_limit_violations_7d:   int         = 0
    recent_emergency_stop_events_7d:   int         = 0
    recent_audit_missing_7d:           int         = 0
    recent_test_pass_rate_pct:         float | None = None
    recent_test_total_count:           int         = 0

    # ---- operator explicit opt-in ----
    operator_explicit_opt_in:          bool        = False
    operator_note:                     str         = ""   # max 500 chars


# ---------- item DTO ----------


@dataclass(frozen=True)
class ReleaseReadinessItem:
    """단일 점검 항목 결과."""
    name:        str
    category:    ReadinessCategory
    severity:    ReadinessSeverity
    required:    bool        = True
    message:     str         = ""
    detail:      dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":     self.name,
            "category": self.category.value,
            "severity": self.severity.value,
            "required": self.required,
            "message":  self.message,
            "detail":   dict(self.detail),
        }


# ---------- result DTO ----------


@dataclass
class ReleaseReadinessResult:
    """Release Readiness 평가 결과.

    invariants (코드 단 강제):
    - `is_live_authorization=False` 항상. READY_TO_TAG 가 *실거래 활성화 / promotion
      자동 허가가 아님* 강제.
    - `auto_apply_allowed=False` 항상. 본 리포트가 어떤 .env / settings 도 자동
      적용하지 않음 강제.
    - `is_order_signal=False` 항상.
    - `live_flag_changed=False` / `mode_changed=False` 항상.
    """
    target_release_tag:      str
    release_kind:            str
    verdict:                 ReleaseReadinessVerdict
    items:                   list[ReleaseReadinessItem] = field(default_factory=list)
    failed_required:         list[str] = field(default_factory=list)
    warnings:                list[str] = field(default_factory=list)
    required_actions:        list[str] = field(default_factory=list)
    operator_note:           str       = ""
    is_live_authorization:   bool      = False
    auto_apply_allowed:      bool      = False
    is_order_signal:         bool      = False
    live_flag_changed:       bool      = False
    mode_changed:            bool      = False
    generated_at:            datetime  = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        if self.is_live_authorization is not False:
            raise ValueError(
                "ReleaseReadinessResult.is_live_authorization must be False. "
                "READY_TO_TAG verdict means 'operator-reviewable', NOT "
                "'authorize live trading or promotion'."
            )
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "ReleaseReadinessResult.auto_apply_allowed must be False. "
                "This report never auto-modifies .env / settings."
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "ReleaseReadinessResult.is_order_signal must be False — this "
                "report does not produce BUY/SELL/HOLD signals."
            )
        if self.live_flag_changed is not False:
            raise ValueError(
                "ReleaseReadinessResult.live_flag_changed must be False — "
                "this report does not mutate safety flags."
            )
        if self.mode_changed is not False:
            raise ValueError(
                "ReleaseReadinessResult.mode_changed must be False — this "
                "report does not change operation mode."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_release_tag":     self.target_release_tag,
            "release_kind":           self.release_kind,
            "verdict":                self.verdict.value,
            "items":                  [it.to_dict() for it in self.items],
            "failed_required":        list(self.failed_required),
            "warnings":               list(self.warnings),
            "required_actions":       list(self.required_actions),
            "operator_note":          self.operator_note,
            "is_live_authorization":  self.is_live_authorization,
            "auto_apply_allowed":     self.auto_apply_allowed,
            "is_order_signal":        self.is_order_signal,
            "live_flag_changed":      self.live_flag_changed,
            "mode_changed":           self.mode_changed,
            "generated_at":           self.generated_at.isoformat(),
        }


# ---------- required matrix ----------


def _required_for_kind(kind: str) -> dict[str, bool]:
    """release_kind 별 required 매트릭스.

    - BETA       : 안전 flag + pre-market + desktop hygiene 최소.
    - RC         : + Paper Gate + Strategy Health + 운영자 opt-in.
    - STABLE     : + Live Manual Gate + recent activity 강화.
    """
    kind = kind.upper()
    base = {
        # 안전 flag 4종 — 모든 단계에서 필수.
        "kis_is_paper_safety":          True,
        "enable_live_trading_safety":   True,
        "enable_ai_execution_safety":   True,
        "enable_futures_safety":        True,
        # pre-market verdict.
        "pre_market_check":             True,
        # 시스템 hygiene.
        "system_audit_recency":         True,
        "repository_hygiene":           True,
        # 문서.
        "documentation":                True,
        # 데이터 freshness (mock 외에는 필수).
        "data_freshness":               False,
        # 최근 활동.
        "recent_loss_limit":            True,
        "recent_audit_missing":         True,
        "recent_emergency_stop":        False,  # WARN only
        # desktop build 상태 — BETA 단계에서는 선택 (실패 시 WARN 만).
        "desktop_sidecar_build":        False,
        "desktop_installer_build":      False,
        # 운영자 opt-in.
        "operator_opt_in":              False,
        # governance gates — BETA 에서는 선택.
        "paper_gate":                   False,
        "live_manual_gate":             False,
        "ai_assist_gate":               False,
        "ai_execution_gate":            False,
        # strategy health (#77).
        "alpha_decay":                  False,
        # test pass rate.
        "test_pass_rate":               False,
    }
    if kind == ReleaseKind.RC.value:
        return {
            **base,
            "paper_gate":                True,
            "alpha_decay":               True,
            "operator_opt_in":           True,
            "desktop_sidecar_build":     True,
            "test_pass_rate":            True,
        }
    if kind == ReleaseKind.STABLE.value:
        return {
            **base,
            "paper_gate":                True,
            "live_manual_gate":          True,
            "alpha_decay":               True,
            "operator_opt_in":           True,
            "desktop_sidecar_build":     True,
            "desktop_installer_build":   True,
            "data_freshness":            True,
            "test_pass_rate":            True,
            "recent_emergency_stop":     True,
        }
    # BETA / 기타 — 보수적으로 base.
    return base


# ---------- evaluator ----------


_GATE_PASS_LABELS = frozenset({"PASS", "READY_FOR_REVIEW", "READY_TO_START",
                               "WARN_BUT_START_ALLOWED"})
_GATE_FAIL_LABELS = frozenset({"FAIL", "BLOCKED", "DO_NOT_START"})
_GATE_CAUTION_LABELS = frozenset({"CAUTION"})


def _gate_label_to_severity(label: str | None) -> ReadinessSeverity:
    """gate verdict 라벨을 ReadinessSeverity 로 매핑."""
    if not label:
        return ReadinessSeverity.UNKNOWN
    upper = label.upper()
    if upper in _GATE_PASS_LABELS:
        return ReadinessSeverity.PASS
    if upper in _GATE_FAIL_LABELS:
        return ReadinessSeverity.FAIL
    if upper in _GATE_CAUTION_LABELS:
        return ReadinessSeverity.WARN
    return ReadinessSeverity.UNKNOWN


def evaluate_release_readiness(
    inp: ReleaseReadinessInput,
    thresholds: ReleaseReadinessThresholds | None = None,
) -> ReleaseReadinessResult:
    """Release Readiness 평가 — 입력 DTO → 결과 DTO.

    외부 시스템 영향 0건. 본 함수는 결정 트리만 수행하며, 호출자가 markdown
    리포트로 변환하거나 운영자에게 surface 한다.
    """
    th = thresholds or ReleaseReadinessThresholds()
    kind = (inp.release_kind or ReleaseKind.BETA.value).upper()
    if kind not in {k.value for k in ReleaseKind}:
        kind = ReleaseKind.BETA.value
    req = _required_for_kind(kind)
    items: list[ReleaseReadinessItem] = []

    def add(name: str, cat: ReadinessCategory, sev: ReadinessSeverity,
            message: str = "", **detail: Any) -> None:
        items.append(ReleaseReadinessItem(
            name=name, category=cat, severity=sev,
            required=bool(req.get(name, False)),
            message=message, detail=detail,
        ))

    # ---- 1. SAFETY_FLAGS ----
    add("kis_is_paper_safety", ReadinessCategory.SAFETY_FLAGS,
        ReadinessSeverity.PASS if inp.kis_is_paper else ReadinessSeverity.FAIL,
        "KIS_IS_PAPER=true 안전" if inp.kis_is_paper
        else "KIS_IS_PAPER=false — 모의투자 강제 해제됨, 릴리스 금지")
    add("enable_live_trading_safety", ReadinessCategory.SAFETY_FLAGS,
        ReadinessSeverity.PASS if not inp.enable_live_trading
        else ReadinessSeverity.FAIL,
        "ENABLE_LIVE_TRADING=false 안전" if not inp.enable_live_trading
        else "ENABLE_LIVE_TRADING=true — 베타 / RC / Stable 어느 단계에서도 금지")
    add("enable_ai_execution_safety", ReadinessCategory.SAFETY_FLAGS,
        ReadinessSeverity.PASS if not inp.enable_ai_execution
        else ReadinessSeverity.FAIL,
        "ENABLE_AI_EXECUTION=false 안전" if not inp.enable_ai_execution
        else "ENABLE_AI_EXECUTION=true — 본 게이트는 AI 자동 실행 미허용")
    add("enable_futures_safety", ReadinessCategory.SAFETY_FLAGS,
        ReadinessSeverity.PASS if not inp.enable_futures_live_trading
        else ReadinessSeverity.FAIL,
        "ENABLE_FUTURES_LIVE_TRADING=false 안전"
        if not inp.enable_futures_live_trading
        else "ENABLE_FUTURES_LIVE_TRADING=true — 선물 LIVE 영구 금지 (#76)")

    # ---- 2. GOVERNANCE_GATES ----
    add("paper_gate", ReadinessCategory.GOVERNANCE_GATES,
        _gate_label_to_severity(inp.paper_gate_verdict),
        f"Paper Gate(#72) verdict: {inp.paper_gate_verdict or 'UNKNOWN'}",
        verdict=inp.paper_gate_verdict)
    add("live_manual_gate", ReadinessCategory.GOVERNANCE_GATES,
        _gate_label_to_severity(inp.live_manual_gate_verdict),
        f"Live Manual Gate(#73) verdict: {inp.live_manual_gate_verdict or 'UNKNOWN'}",
        verdict=inp.live_manual_gate_verdict)
    add("ai_assist_gate", ReadinessCategory.GOVERNANCE_GATES,
        _gate_label_to_severity(inp.ai_assist_gate_verdict),
        f"AI Assist Gate(#74) verdict: {inp.ai_assist_gate_verdict or 'UNKNOWN'}",
        verdict=inp.ai_assist_gate_verdict)
    add("ai_execution_gate", ReadinessCategory.GOVERNANCE_GATES,
        _gate_label_to_severity(inp.ai_execution_gate_verdict),
        f"AI Execution Activation Gate(#75) verdict: "
        f"{inp.ai_execution_gate_verdict or 'UNKNOWN'}",
        verdict=inp.ai_execution_gate_verdict)

    # ---- 3. PRE_MARKET (#80 / #91) ----
    pm_sev = _gate_label_to_severity(inp.pre_market_verdict)
    add("pre_market_check", ReadinessCategory.PRE_MARKET, pm_sev,
        f"Pre-market Checklist verdict: {inp.pre_market_verdict or 'UNKNOWN'} "
        f"(start_allowed={inp.pre_market_start_allowed})",
        verdict=inp.pre_market_verdict,
        start_allowed=inp.pre_market_start_allowed)

    # ---- 4. STRATEGY_HEALTH (#77 alpha decay) ----
    if inp.alpha_decay_strategies_evaluated <= 0:
        ad_sev = ReadinessSeverity.UNKNOWN
        ad_msg = "Alpha Decay(#77) 미평가 — 전략 0개 평가"
    elif inp.alpha_decay_disable_candidate_count > th.max_alpha_disable_candidates_fail:
        ad_sev = ReadinessSeverity.FAIL
        ad_msg = (
            f"Alpha Decay(#77) DISABLE_CANDIDATE 전략 "
            f"{inp.alpha_decay_disable_candidate_count}개 — "
            f"임계 {th.max_alpha_disable_candidates_fail} 초과"
        )
    elif inp.alpha_decay_disable_candidate_count > th.max_alpha_disable_candidates_warn:
        ad_sev = ReadinessSeverity.WARN
        ad_msg = (
            f"Alpha Decay(#77) DISABLE_CANDIDATE 전략 "
            f"{inp.alpha_decay_disable_candidate_count}개 — 운영자 검토 권장"
        )
    elif inp.alpha_decay_worst_status in ("DECAY_WARNING", "DISABLE_CANDIDATE"):
        ad_sev = ReadinessSeverity.WARN
        ad_msg = f"Alpha Decay(#77) worst status: {inp.alpha_decay_worst_status}"
    else:
        ad_sev = ReadinessSeverity.PASS
        ad_msg = (
            f"Alpha Decay(#77) {inp.alpha_decay_strategies_evaluated}개 전략 평가 "
            f"— worst status: {inp.alpha_decay_worst_status or 'HEALTHY'}"
        )
    add("alpha_decay", ReadinessCategory.STRATEGY_HEALTH, ad_sev, ad_msg,
        worst_status=inp.alpha_decay_worst_status,
        disable_candidate_count=inp.alpha_decay_disable_candidate_count,
        strategies_evaluated=inp.alpha_decay_strategies_evaluated)

    # ---- 5. DESKTOP_BUILD (#90 / 90-A) ----
    if inp.desktop_sidecar_built is None:
        sc_sev = ReadinessSeverity.UNKNOWN
        sc_msg = "backend sidecar (autotrade-backend.exe) 빌드 상태 미확인"
    elif inp.desktop_sidecar_built:
        sc_sev = ReadinessSeverity.PASS
        sc_msg = "backend sidecar 빌드 완료"
    else:
        sc_sev = ReadinessSeverity.WARN
        sc_msg = (
            "backend sidecar 미빌드 — `scripts/build_backend_sidecar.ps1` 실행 필요. "
            "베타 단계에서는 brower mode + start_kis_paper_test_windows.bat 대체 가능."
        )
    add("desktop_sidecar_build", ReadinessCategory.DESKTOP_BUILD, sc_sev, sc_msg,
        built=inp.desktop_sidecar_built)

    if inp.desktop_installer_built is None:
        ins_sev = ReadinessSeverity.UNKNOWN
        ins_msg = "Windows installer (MSI / setup.exe) 빌드 상태 미확인"
    elif inp.desktop_installer_built:
        ins_sev = ReadinessSeverity.PASS
        ins_msg = "Windows installer 빌드 완료"
    else:
        ins_sev = ReadinessSeverity.WARN
        ins_msg = (
            "Windows installer 미빌드 — Rust toolchain + MSVC Build Tools 갖춰진 "
            "빌드 머신에서 `scripts/build_windows_installer.ps1` 실행 또는 GitHub "
            "Actions desktop-release.yml 활성화 필요."
        )
    add("desktop_installer_build", ReadinessCategory.DESKTOP_BUILD, ins_sev,
        ins_msg, built=inp.desktop_installer_built)

    # ---- 6. SYSTEM_HYGIENE (#88) ----
    now_ts = datetime.now(timezone.utc)
    if inp.last_system_audit_at is None:
        sa_sev = ReadinessSeverity.UNKNOWN
        sa_msg = "마지막 시스템 hygiene audit 일시 입력 없음"
        audit_age_days: int | None = None
    else:
        audit_at = inp.last_system_audit_at
        if audit_at.tzinfo is None:
            audit_at = audit_at.replace(tzinfo=timezone.utc)
        audit_age_days = max(0, (now_ts - audit_at).days)
        if audit_age_days >= th.audit_fail_days:
            sa_sev = ReadinessSeverity.FAIL
            sa_msg = (
                f"시스템 hygiene audit {audit_age_days}일 경과 — 임계 "
                f"{th.audit_fail_days}일 초과, 릴리스 전 audit 재실행 필요"
            )
        elif audit_age_days >= th.audit_warn_days:
            sa_sev = ReadinessSeverity.WARN
            sa_msg = (
                f"시스템 hygiene audit {audit_age_days}일 경과 — 임계 "
                f"{th.audit_warn_days}일 초과, audit 재실행 권장"
            )
        else:
            sa_sev = ReadinessSeverity.PASS
            sa_msg = f"시스템 hygiene audit {audit_age_days}일 전 (최신)"
    add("system_audit_recency", ReadinessCategory.SYSTEM_HYGIENE, sa_sev, sa_msg,
        audit_age_days=audit_age_days)

    if inp.repository_hygiene_pass is None:
        rh_sev = ReadinessSeverity.UNKNOWN
        rh_msg = "Repository hygiene 테스트 결과 입력 없음"
    elif inp.repository_hygiene_pass:
        rh_sev = ReadinessSeverity.PASS
        rh_msg = "Repository hygiene 테스트 PASS (#88)"
    else:
        rh_sev = ReadinessSeverity.FAIL
        rh_msg = (
            "Repository hygiene 테스트 FAIL — .gitignore / requirements.txt / "
            ".env.example / workflow YAML / 등 검사 위반"
        )
    add("repository_hygiene", ReadinessCategory.SYSTEM_HYGIENE, rh_sev, rh_msg,
        passed=inp.repository_hygiene_pass)

    # ---- 7. DOCUMENTATION ----
    if inp.documentation_coverage_ok is None:
        doc_sev = ReadinessSeverity.UNKNOWN
        doc_msg = "필수 문서 (CLAUDE.md / 정책 문서) 존재 여부 미확인"
    elif inp.documentation_coverage_ok:
        doc_sev = ReadinessSeverity.PASS
        doc_msg = "필수 정책 / 가이드 문서 존재 OK"
    else:
        doc_sev = ReadinessSeverity.FAIL
        doc_msg = "필수 문서 누락 — CLAUDE.md / docs/*_policy.md 점검 필요"
    add("documentation", ReadinessCategory.DOCUMENTATION, doc_sev, doc_msg)

    # ---- 8. DATA_FRESHNESS (#20) ----
    if inp.data_freshness_ok is None:
        df_sev = ReadinessSeverity.UNKNOWN
        df_msg = "Data freshness 입력 없음"
    elif inp.data_freshness_ok:
        df_sev = ReadinessSeverity.PASS
        df_msg = f"Data freshness OK (provider={inp.market_data_provider})"
    else:
        df_sev = ReadinessSeverity.FAIL
        df_msg = (
            f"Data freshness 위반 (provider={inp.market_data_provider}) — "
            "stale 종목 점검 필요"
        )
    add("data_freshness", ReadinessCategory.DATA_FRESHNESS, df_sev, df_msg,
        provider=inp.market_data_provider)

    # ---- 9. RECENT_ACTIVITY (최근 7일) ----
    if inp.recent_loss_limit_violations_7d > th.max_loss_limit_violations_7d:
        rll_sev = ReadinessSeverity.FAIL
        rll_msg = (
            f"최근 7일 일일 손실한도 위반 "
            f"{inp.recent_loss_limit_violations_7d}건 — 릴리스 전 원인 분석 필요"
        )
    else:
        rll_sev = ReadinessSeverity.PASS
        rll_msg = "최근 7일 일일 손실한도 위반 0건"
    add("recent_loss_limit", ReadinessCategory.RECENT_ACTIVITY, rll_sev, rll_msg,
        violations_7d=inp.recent_loss_limit_violations_7d)

    if inp.recent_audit_missing_7d > th.max_audit_missing_7d:
        ram_sev = ReadinessSeverity.FAIL
        ram_msg = (
            f"최근 7일 OrderAuditLog 누락 {inp.recent_audit_missing_7d}건 — "
            "audit 무결성 위반"
        )
    else:
        ram_sev = ReadinessSeverity.PASS
        ram_msg = "최근 7일 audit 누락 0건"
    add("recent_audit_missing", ReadinessCategory.RECENT_ACTIVITY, ram_sev,
        ram_msg, missing_7d=inp.recent_audit_missing_7d)

    if inp.recent_emergency_stop_events_7d > th.max_emergency_stop_events_7d_warn:
        rem_sev = ReadinessSeverity.WARN
        rem_msg = (
            f"최근 7일 emergency_stop 발생 "
            f"{inp.recent_emergency_stop_events_7d}건 — 패턴 검토 권장"
        )
    else:
        rem_sev = ReadinessSeverity.PASS
        rem_msg = (
            f"최근 7일 emergency_stop {inp.recent_emergency_stop_events_7d}건"
        )
    add("recent_emergency_stop", ReadinessCategory.RECENT_ACTIVITY, rem_sev,
        rem_msg, events_7d=inp.recent_emergency_stop_events_7d)

    if inp.recent_test_pass_rate_pct is None:
        rt_sev = ReadinessSeverity.UNKNOWN
        rt_msg = "테스트 통과율 입력 없음"
    elif inp.recent_test_pass_rate_pct < th.min_test_pass_rate_pct_fail:
        rt_sev = ReadinessSeverity.FAIL
        rt_msg = (
            f"테스트 통과율 {inp.recent_test_pass_rate_pct:.1f}% — 임계 "
            f"{th.min_test_pass_rate_pct_fail:.0f}% 미만"
        )
    elif inp.recent_test_pass_rate_pct < th.min_test_pass_rate_pct_warn:
        rt_sev = ReadinessSeverity.WARN
        rt_msg = (
            f"테스트 통과율 {inp.recent_test_pass_rate_pct:.1f}% — 임계 "
            f"{th.min_test_pass_rate_pct_warn:.0f}% 미만"
        )
    else:
        rt_sev = ReadinessSeverity.PASS
        rt_msg = (
            f"테스트 통과율 {inp.recent_test_pass_rate_pct:.1f}% "
            f"({inp.recent_test_total_count}개 케이스)"
        )
    add("test_pass_rate", ReadinessCategory.RECENT_ACTIVITY, rt_sev, rt_msg,
        pass_rate_pct=inp.recent_test_pass_rate_pct,
        total_count=inp.recent_test_total_count)

    # ---- 10. OPERATOR explicit opt-in ----
    if inp.operator_explicit_opt_in:
        op_sev = ReadinessSeverity.PASS
        op_msg = "운영자 명시 opt-in 기록됨"
    else:
        # BETA 에서는 not-required → SKIP. RC/STABLE 에서는 required → FAIL.
        op_sev = (
            ReadinessSeverity.FAIL
            if req.get("operator_opt_in", False)
            else ReadinessSeverity.SKIP
        )
        op_msg = (
            "운영자 명시 opt-in 없음 — RC / Stable 단계에서는 필수"
            if op_sev is ReadinessSeverity.FAIL
            else "운영자 opt-in 미입력 — BETA 단계 SKIP"
        )
    add("operator_opt_in", ReadinessCategory.OPERATOR, op_sev, op_msg)

    # ---- aggregate ----
    failed_required = [
        it.name for it in items
        if it.required and it.severity is ReadinessSeverity.FAIL
    ]
    warnings = [
        f"{it.name}: {it.message}" for it in items
        if it.severity is ReadinessSeverity.WARN
    ]

    # strict 모드 — required UNKNOWN 도 FAIL 취급.
    if inp.strict:
        unknown_required = [
            it.name for it in items
            if it.required and it.severity is ReadinessSeverity.UNKNOWN
        ]
        failed_required.extend(unknown_required)

    # required UNKNOWN 만 있고 FAIL 0건 → INSUFFICIENT_DATA.
    required_unknown_count = sum(
        1 for it in items
        if it.required and it.severity is ReadinessSeverity.UNKNOWN
    )

    if failed_required:
        verdict = ReleaseReadinessVerdict.DO_NOT_TAG
    elif (
        required_unknown_count > 0
        and not inp.strict  # strict 모드는 위에서 이미 FAIL 처리.
        # 모든 required 가 UNKNOWN 인 극단 case 만 INSUFFICIENT_DATA. PASS 가
        # 1건 이상 있으면 정상 verdict 분기.
        and required_unknown_count == sum(1 for it in items if it.required)
    ):
        verdict = ReleaseReadinessVerdict.INSUFFICIENT_DATA
    elif warnings:
        verdict = ReleaseReadinessVerdict.READY_WITH_CAVEATS
    else:
        verdict = ReleaseReadinessVerdict.READY_TO_TAG

    actions: list[str] = []
    if failed_required:
        actions.append("required FAIL 항목을 모두 해결 후 재평가.")
    if warnings:
        actions.append("WARN 항목은 운영자 검토 권장.")
    if (
        verdict is ReleaseReadinessVerdict.READY_TO_TAG
        or verdict is ReleaseReadinessVerdict.READY_WITH_CAVEATS
    ):
        actions.append(
            "READY 라벨은 *릴리스 자동 허가가 아닙니다* — 운영자가 직접 별도 "
            "PR / git tag / GitHub Release 생성으로 진행."
        )
    if verdict is ReleaseReadinessVerdict.INSUFFICIENT_DATA:
        actions.append(
            "required 항목 데이터를 채워서 재평가 (또는 strict=true 로 보수적 "
            "DO_NOT_TAG 처리)."
        )

    return ReleaseReadinessResult(
        target_release_tag=inp.target_release_tag,
        release_kind=kind,
        verdict=verdict,
        items=items,
        failed_required=failed_required,
        warnings=warnings,
        required_actions=actions,
        operator_note=(inp.operator_note or "")[:500],
    )


# ---------- markdown ----------


def render_markdown_report(result: ReleaseReadinessResult) -> str:
    """Markdown 리포트. 운영자 / 베타테스터 / PR 본문에 첨부 가능.

    출력에 secret 원문 0건 (테스트로 lock).
    """
    lines: list[str] = []
    lines.append(f"# Release Readiness Report — {result.target_release_tag}")
    lines.append("")
    lines.append(f"_생성: {result.generated_at.isoformat()}_")
    lines.append(f"_release_kind: **{result.release_kind}**_")
    lines.append("")
    lines.append(
        "> ⚠️ 본 보고서는 *릴리스 가능 여부 advisory* 입니다. **READY_TO_TAG 라벨이 "
        "실거래 활성화 / 자동 promotion 을 의미하지 않습니다** — 운영자가 본 "
        "리포트를 직접 확인 후, 별도 PR / git tag / GitHub Release 생성으로 진행."
    )
    lines.append("")
    lines.append(f"## 판정: **{result.verdict.value}**")
    lines.append("")
    if result.failed_required:
        lines.append("## 실패 항목 (required FAIL)")
        for n in result.failed_required:
            lines.append(f"- 🛑 `{n}`")
        lines.append("")
    if result.warnings:
        lines.append("## 경고 (WARN)")
        for w in result.warnings:
            lines.append(f"- ⚠️ {w}")
        lines.append("")
    if result.required_actions:
        lines.append("## 필요 조치")
        for a in result.required_actions:
            lines.append(f"- 📝 {a}")
        lines.append("")

    lines.append("## 항목 상세")
    lines.append("")
    lines.append("| name | category | severity | required | message |")
    lines.append("|---|---|---|---|---|")
    for it in result.items:
        # message 내부 pipe 는 escape — 표 깨짐 방지.
        msg = it.message.replace("|", "\\|")
        lines.append(
            f"| `{it.name}` | {it.category.value} | {it.severity.value} |"
            f" {it.required} | {msg} |"
        )
    if result.operator_note:
        lines.append("")
        lines.append("## 운영자 메모")
        # operator_note 는 *plaintext* 만 — markdown injection 방어 위해 inline.
        lines.append(f"> {result.operator_note}")
    lines.append("")
    lines.append(
        "---\n본 보고서는 *판단 보조 자료* 입니다. 본 게이트는 어떤 .env / "
        "settings / broker / DB 도 변경하지 않습니다."
    )
    return "\n".join(lines)
