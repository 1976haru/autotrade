"""체크리스트 11-00 — EXE 빌드 전 최종 통합 검증 Gate (FinalPrebuildIntegratedGate).

health / config / KIS credentials / DB / Universe / Portfolio / Agent / Order / Stress /
Backtest / Walk-forward / Intraday real-data / Live safety / UI·API / 문서 / CI·PR 상태를
한 번에 점검해 **"빌드해도 되는 상태인지"** 를 최종 판정한다 — BUILD_READY /
BUILD_READY_WITH_WARNINGS / BUILD_BLOCKED.

본 모듈은 *순수 합성 함수* 다 — 호출자가 각 섹션 결과를 입력 DTO 로 전달하면, 본 함수가
BLOCKED/WARN 매트릭스를 적용해 종합 판정만 한다. broker / OrderExecutor / route_order / KIS
주문 API 를 import·호출하지 않으며, 무거운 테스트/서브프로세스를 직접 실행하지 않는다(그건
CLI/호출자 책임). **결과가 좋아도 자동 적용 / 실전 전환 / 주문 0건.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"

BUILD_READY = "BUILD_READY"
BUILD_READY_WITH_WARNINGS = "BUILD_READY_WITH_WARNINGS"
BUILD_BLOCKED = "BUILD_BLOCKED"

_USER_LINE = {
    BUILD_READY: "EXE 빌드 진행 가능",
    BUILD_READY_WITH_WARNINGS: "EXE 빌드 가능하나 WARN 확인 필요",
    BUILD_BLOCKED: "EXE 빌드 보류",
}


@dataclass(frozen=True)
class GateInputs:
    # ---- 안전 플래그 (현재값; 호출자가 입력 DTO 로 전달) ----
    default_mode: str = "PAPER"
    enable_live_trading: bool = False
    enable_ai_execution: bool = False
    enable_futures_live_trading: bool = False
    kis_is_paper: bool = True
    # ---- 품질 (호출자가 실행 후 라벨 전달; fast 모드는 SKIP) ----
    backend_quality: str = SKIP        # PASS/WARN/FAIL/SKIP
    ruff_pass: bool | None = None
    pytest_collection_ok: bool | None = None
    frontend_quality: str = SKIP
    frontend_build_ok: bool | None = None
    # ---- 보안 ----
    security_findings: int = 0
    secret_exposure: bool = False
    dotenv_tracked: bool = False
    security_scanned: bool = True      # False = fast/API 모드 미실행 → WARN
    # ---- health / DB ----
    backend_health_ok: bool | None = None
    db_status: str = "UNKNOWN"         # OK/FAIL/UNKNOWN
    preflight_status: str = "UNKNOWN"  # OK/WARN/FAIL/UNKNOWN
    # ---- config ----
    # (default_mode/kis_is_paper 위 안전 플래그 재사용)
    # ---- KIS credentials ----
    kis_credentials_present: bool | None = None
    require_kis_credentials: bool = False
    kis_live_order_path_blocked: bool = True   # True = NotImplementedError 가드 유지
    broker_direct_call_found: bool = False
    # ---- 데이터/파이프라인 섹션 (라벨; 없으면 SKIP) ----
    universe_empty_no_fallback: bool = False
    portfolio_source_mixed: bool = False
    agent_pipeline_status: str = SKIP
    order_quality_status: str = SKIP
    # ---- 빌드 게이트 라벨 (report 파일에서; 없으면 SKIP) ----
    build01_verdict: str | None = None         # build_ready True/False or verdict str
    build02a_ready: bool | None = None
    build02b_ready: bool | None = None
    ui_api_manifest_status: str = SKIP
    # ---- 전략 검증 (advisory — 빌드 차단 아님) ----
    backtest_status: str = SKIP
    walk_forward_status: str = SKIP
    stress_fail_count: int | None = None
    real_daily_verdict: str | None = None
    intraday_final_judgement: str | None = None   # PROMISING/WORTH_MORE_RESEARCH/...
    paper_sample_count: int = 0
    # ---- live safety / UI / docs / build inputs ----
    live_safety_ok: bool = True
    order_buttons_found: bool = False
    docs_runbook_ok: bool = True
    exe_build_inputs_ok: bool = True
    # ---- repo / CI ----
    git_clean: bool = True
    main_up_to_date: bool | None = None
    open_pr_blocking: bool = False
    untracked_release_risk: bool = False
    # ---- 실행 컨텍스트 (INSTALL-UX-FIX-01) ----
    # SOURCE_DEV: 소스 전용 점검(빌드 입력/문서) 누락 시 FAIL/WARN.
    # PACKAGED_RUNTIME / CI_BUILD: 소스 전용 점검은 SKIP(참고용) — 설치본 오탐 방지.
    app_runtime: str = "SOURCE_DEV"


@dataclass(frozen=True)
class Section:
    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class FinalPrebuildReport:
    generated_at: str
    overall_status: str
    user_line: str
    exe_build_allowed: bool
    ready_for_market_rehearsal: bool
    ready_for_paper_rehearsal: bool
    sections: tuple[Section, ...]
    counts: dict[str, int]
    blocked_reasons: tuple[str, ...] = ()
    warn_reasons: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()
    # invariants.
    contains_secret: bool = False
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    no_profit_guarantee: bool = True
    disclaimer: str = (
        "본 Gate 는 'EXE 빌드해도 되는 상태인지' 만 판정한다. 실전 승인이 아니며, 주문을 "
        "실행하지 않는다. 수익을 보장하지 않는다. '빌드 가능'과 '전략 유망'은 다르다."
    )
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.contains_secret or self.is_live_authorization:
            raise ValueError("unsafe invariant True")
        if self.broker_order_sent or self.order_created:
            raise ValueError("broker_order_sent / order_created must be False")
        if not self.no_profit_guarantee:
            raise ValueError("no_profit_guarantee must be True")
        if self.overall_status not in _USER_LINE:
            raise ValueError(f"invalid overall_status: {self.overall_status}")


def _b(name: str, sections: list[Section], blocked: list[str], status: str, detail: str) -> None:
    sections.append(Section(name, status, detail))
    if status == FAIL:
        blocked.append(f"{name}: {detail}")


def run_final_prebuild_gate(inp: GateInputs, *, generated_at: str | None = None) -> FinalPrebuildReport:
    gen = generated_at or datetime.now(timezone.utc).isoformat()
    sections: list[Section] = []
    blocked: list[str] = []
    warns: list[str] = []

    def add(name: str, status: str, detail: str = "") -> None:
        sections.append(Section(name, status, detail))
        if status == FAIL:
            blocked.append(f"{name}: {detail}")
        elif status == WARN:
            warns.append(f"{name}: {detail}")

    # A. REPOSITORY_STATUS
    if inp.untracked_release_risk:
        add("REPOSITORY_STATUS", FAIL, "릴리스 경로에 위험한 미추적 파일")
    elif not inp.git_clean or inp.open_pr_blocking:
        add("REPOSITORY_STATUS", WARN,
            "git 미정리 또는 닫기권고 open PR (main 차단 아님)")
    else:
        add("REPOSITORY_STATUS", PASS, "clean / main 차단 PR 없음")

    # B. BACKEND_QUALITY
    if inp.backend_quality == FAIL or inp.ruff_pass is False or inp.pytest_collection_ok is False:
        add("BACKEND_QUALITY", FAIL, "ruff/pytest 실패")
    elif inp.backend_quality == PASS:
        add("BACKEND_QUALITY", PASS, "ruff+pytest 통과")
    else:
        add("BACKEND_QUALITY", WARN, "fast 모드 — 전체 pytest 미실행(CI/--full 확인)")

    # C. FRONTEND_QUALITY
    if inp.frontend_quality == FAIL or inp.frontend_build_ok is False:
        add("FRONTEND_QUALITY", FAIL, "frontend lint/test/build 실패")
    elif inp.frontend_quality == PASS:
        add("FRONTEND_QUALITY", PASS, "lint+test+build 통과")
    else:
        add("FRONTEND_QUALITY", WARN, "fast 모드 — build 미실행(CI/--full 확인)")

    # D. SECURITY_SECRET_SCAN
    if inp.secret_exposure or inp.security_findings > 0 or inp.dotenv_tracked:
        add("SECURITY_SECRET_SCAN", FAIL,
            f"findings={inp.security_findings} secret={inp.secret_exposure} env_tracked={inp.dotenv_tracked}")
    elif not inp.security_scanned:
        add("SECURITY_SECRET_SCAN", WARN, "fast/API 모드 — security_scan 미실행(CLI 확인)")
    else:
        add("SECURITY_SECRET_SCAN", PASS, "0 findings / .env 미추적")

    # E. HEALTH_PREFLIGHT
    if inp.db_status == "FAIL":
        add("HEALTH_PREFLIGHT", FAIL, "DB 연결/초기화 실패")
    elif inp.backend_health_ok is False or inp.preflight_status == FAIL:
        add("HEALTH_PREFLIGHT", FAIL, "health/preflight FAIL")
    elif inp.db_status == "UNKNOWN" or inp.preflight_status in ("UNKNOWN", WARN):
        add("HEALTH_PREFLIGHT", WARN, "health/preflight 일부 미확인")
    else:
        add("HEALTH_PREFLIGHT", PASS, "health/DB/preflight OK")

    # F. CONFIG_ENV + safety flags (BLOCKED 조건)
    safe = (not inp.enable_live_trading and not inp.enable_ai_execution
            and not inp.enable_futures_live_trading and inp.kis_is_paper)
    if not safe:
        add("CONFIG_ENV", FAIL,
            f"안전 플래그 위반 (LIVE={inp.enable_live_trading}/AI={inp.enable_ai_execution}/"
            f"FUT={inp.enable_futures_live_trading}/KIS_PAPER={inp.kis_is_paper})")
    elif inp.default_mode not in ("SIMULATION", "PAPER", "LIVE_SHADOW"):
        add("CONFIG_ENV", WARN, f"DEFAULT_MODE={inp.default_mode}")
    else:
        add("CONFIG_ENV", PASS, "LIVE/AI/FUTURES=false, KIS_IS_PAPER=true")

    # DB
    add("DB", PASS if inp.db_status == "OK" else (FAIL if inp.db_status == "FAIL" else WARN),
        f"db_status={inp.db_status}")

    # G. KIS_CREDENTIALS
    if inp.kis_credentials_present is True:
        add("KIS_CREDENTIALS", PASS, "자격 present")
    elif inp.require_kis_credentials:
        add("KIS_CREDENTIALS", FAIL, "자격 missing (require-kis-credentials 모드)")
    else:
        add("KIS_CREDENTIALS", WARN, "자격 미설정 — 장중 리허설 전 해결 필요")

    # L. KIS_PAPER_ORDER_PATH (live 주문 경로 차단 확인)
    if inp.broker_direct_call_found:
        add("KIS_PAPER_ORDER_PATH", FAIL, "broker/OrderExecutor/route_order 직접 호출 발견")
    elif not inp.kis_live_order_path_blocked:
        add("KIS_PAPER_ORDER_PATH", FAIL, "KIS live 주문 가능 경로 발견")
    else:
        add("KIS_PAPER_ORDER_PATH", PASS, "live 주문 차단(NotImplementedError) 유지")

    # I. UNIVERSE
    add("UNIVERSE", FAIL if inp.universe_empty_no_fallback else PASS,
        "후보군 0 + fallback 없음" if inp.universe_empty_no_fallback else "fallback OK")

    # J. PORTFOLIO
    add("PORTFOLIO", FAIL if inp.portfolio_source_mixed else PASS,
        "source mixed" if inp.portfolio_source_mixed else "source 단일/분리 OK")

    # K. AGENT_PIPELINE
    add("AGENT_PIPELINE", inp.agent_pipeline_status if inp.agent_pipeline_status in (PASS, WARN, FAIL) else WARN,
        f"status={inp.agent_pipeline_status}")

    # M. ORDER_QUALITY_FEEDBACK
    add("ORDER_QUALITY_FEEDBACK", inp.order_quality_status if inp.order_quality_status in (PASS, WARN, FAIL) else SKIP,
        f"status={inp.order_quality_status}")

    # BUILD-01 / 02A / 02B-0
    def _verdict_section(name: str, val: Any) -> None:
        if val is None:
            add(name, WARN, "리포트 없음(fast — 미실행)")
        elif val in (True, "PASS", "BUILD_READY", "build_ready"):
            add(name, PASS, "build_ready/PASS")
        elif val is False or val in ("FAIL", "BLOCKED"):
            add(name, FAIL, f"{val}")
        else:
            add(name, WARN, f"{val}")
    _verdict_section("BUILD_01_PROGRAM_INTEGRITY", inp.build01_verdict)
    _verdict_section("BUILD_02A_PREMARKET", inp.build02a_ready)
    _verdict_section("BUILD_02B_KIS_PAPER_AUDIT", inp.build02b_ready)

    # N. BACKTEST / O. WALK_FORWARD / P. STRESS (advisory)
    add("BACKTEST", inp.backtest_status if inp.backtest_status in (PASS, WARN, FAIL) else SKIP,
        f"status={inp.backtest_status}")
    add("WALK_FORWARD", inp.walk_forward_status if inp.walk_forward_status in (PASS, WARN, FAIL) else SKIP,
        f"status={inp.walk_forward_status}")
    if inp.stress_fail_count is None:
        add("STRESS", SKIP, "미실행")
    elif inp.stress_fail_count > 0:
        add("STRESS", FAIL, f"stress FAIL {inp.stress_fail_count}건")
    else:
        add("STRESS", PASS, "stress FAIL 0")

    # Q. REAL_DAILY_DATA (advisory — 빌드 차단 아님)
    if inp.real_daily_verdict is None:
        add("REAL_DAILY_DATA", SKIP, "미실행")
    else:
        add("REAL_DAILY_DATA", WARN if inp.real_daily_verdict not in ("STRONG_CANDIDATE",) else PASS,
            f"verdict={inp.real_daily_verdict} (advisory — 빌드 차단 아님)")

    # R. INTRADAY_REAL_DATA (advisory — 전략 운영 확장 WARN, 빌드 차단 아님)
    if inp.intraday_final_judgement is None:
        add("INTRADAY_REAL_DATA", SKIP, "미실행")
    elif inp.intraday_final_judgement == "PROMISING_FOR_PAPER_TEST":
        add("INTRADAY_REAL_DATA", PASS, "PROMISING_FOR_PAPER_TEST (실전 아님, Paper 권고)")
    elif inp.intraday_final_judgement == "BLOCKED_BY_DATA":
        add("INTRADAY_REAL_DATA", WARN, "BLOCKED_BY_DATA — 실데이터 부족(빌드 차단 아님)")
    else:
        add("INTRADAY_REAL_DATA", WARN,
            f"{inp.intraday_final_judgement} — 전략 운영 확장 전 튜닝 권고(빌드 차단 아님)")

    # S. LIVE_SAFETY
    add("LIVE_SAFETY", PASS if inp.live_safety_ok else FAIL,
        "실전 OFF + path gated" if inp.live_safety_ok else "live safety 위반")

    # T. UI_API
    if inp.order_buttons_found:
        add("UI_API", FAIL, "주문/실전/승인 버튼 발견")
    elif inp.ui_api_manifest_status == FAIL:
        add("UI_API", FAIL, "UI/API manifest FAIL")
    elif inp.ui_api_manifest_status == PASS:
        add("UI_API", PASS, "manifest PASS / 금지 버튼 0")
    else:
        add("UI_API", WARN, "manifest 미확인")

    # 설치본(PACKAGED)/CI 에서는 소스 전용 점검 누락을 FAIL/WARN 이 아니라 SKIP(참고용)으로.
    _source_dev = inp.app_runtime == "SOURCE_DEV"
    _src_note = "빌드 전 소스 환경 점검 항목입니다. 설치본에서는 참고용입니다."

    # U. DOCS_RUNBOOK (소스 전용 — 설치본은 docs 미번들 정상)
    if inp.docs_runbook_ok:
        add("DOCS_RUNBOOK", PASS, "문서/Runbook OK")
    elif _source_dev:
        add("DOCS_RUNBOOK", WARN, "문서 일부 누락")
    else:
        add("DOCS_RUNBOOK", SKIP, f"문서 미번들 — {_src_note}")

    # V. EXE_BUILD_INPUTS (소스 전용 — 설치본/CI 런타임은 src-tauri 소스 부재 정상)
    if inp.exe_build_inputs_ok:
        add("EXE_BUILD_INPUTS", PASS, "빌드 필수 파일 present")
    elif _source_dev:
        add("EXE_BUILD_INPUTS", FAIL, "빌드 필수 파일 누락")
    else:
        add("EXE_BUILD_INPUTS", SKIP, f"소스(src-tauri) 미포함 — {_src_note}")

    # Paper sample (WARN if 0)
    if inp.paper_sample_count <= 0:
        warns.append("PAPER_SAMPLE: Paper 거래 표본 0건 — 실전 검토 불가")

    counts = {
        PASS: sum(1 for s in sections if s.status == PASS),
        WARN: sum(1 for s in sections if s.status == WARN),
        FAIL: sum(1 for s in sections if s.status == FAIL),
        SKIP: sum(1 for s in sections if s.status == SKIP),
    }
    if blocked:
        overall = BUILD_BLOCKED
    elif warns:
        overall = BUILD_READY_WITH_WARNINGS
    else:
        overall = BUILD_READY

    exe_allowed = overall != BUILD_BLOCKED
    ready_market = exe_allowed and inp.kis_credentials_present is True
    ready_paper = exe_allowed and inp.db_status != "FAIL"

    next_actions: list[str] = []
    if overall == BUILD_BLOCKED:
        next_actions.append("BLOCKER 해결 후 재실행 (빌드 금지).")
    if inp.kis_credentials_present is not True:
        next_actions.append("장중 리허설 전 KIS 모의 자격(KIS_IS_PAPER=true) 입력.")
    if inp.paper_sample_count <= 0:
        next_actions.append("Paper 모의 표본 축적(실전 검토 전제).")
    if inp.intraday_final_judgement and inp.intraday_final_judgement != "PROMISING_FOR_PAPER_TEST":
        next_actions.append("실데이터 분봉 전략 튜닝(빌드와 별개 — 전략 운영 확장 전).")
    if exe_allowed:
        next_actions.append("EXE 빌드 진행 가능 (위 WARN 검토 후).")

    return FinalPrebuildReport(
        generated_at=gen, overall_status=overall, user_line=_USER_LINE[overall],
        exe_build_allowed=exe_allowed, ready_for_market_rehearsal=ready_market,
        ready_for_paper_rehearsal=ready_paper, sections=tuple(sections), counts=counts,
        blocked_reasons=tuple(blocked), warn_reasons=tuple(warns),
        next_actions=tuple(next_actions))


def to_dict(r: FinalPrebuildReport) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "overall_status": r.overall_status,
        "user_line": r.user_line, "exe_build_allowed": r.exe_build_allowed,
        "ready_for_market_rehearsal": r.ready_for_market_rehearsal,
        "ready_for_paper_rehearsal": r.ready_for_paper_rehearsal,
        "sections": [{"name": s.name, "status": s.status, "detail": s.detail} for s in r.sections],
        "counts": r.counts, "blocked_reasons": list(r.blocked_reasons),
        "warn_reasons": list(r.warn_reasons), "next_actions": list(r.next_actions),
        "contains_secret": r.contains_secret, "is_live_authorization": r.is_live_authorization,
        "broker_order_sent": r.broker_order_sent, "order_created": r.order_created,
        "no_profit_guarantee": r.no_profit_guarantee, "disclaimer": r.disclaimer,
    }


def render_markdown(r: FinalPrebuildReport) -> str:
    c = r.counts
    lines = [
        "# 체크리스트 11-00 — EXE 빌드 전 통합 검증 Gate",
        "",
        f"> **현재 상태는 {r.user_line}.**" + (
            f" 이유: {r.blocked_reasons[0]}" if r.overall_status == BUILD_BLOCKED and r.blocked_reasons else ""),
        "",
        "> 실전 승인 아님 · 주문 실행 아님 · 수익 보장 아님. '빌드 가능'과 '전략 유망'은 다르다.",
        "",
        f"- overall_status: **{r.overall_status}** · exe_build_allowed: **{r.exe_build_allowed}**",
        f"- ready_for_market_rehearsal: {r.ready_for_market_rehearsal} · "
        f"ready_for_paper_rehearsal: {r.ready_for_paper_rehearsal}",
        f"- PASS {c.get(PASS,0)} · WARN {c.get(WARN,0)} · FAIL {c.get(FAIL,0)} · SKIP {c.get(SKIP,0)}",
        "",
        "## 섹션",
        "| status | section | detail |",
        "|---|---|---|",
    ]
    for s in r.sections:
        lines.append(f"| {s.status} | {s.name} | {s.detail} |")
    lines += ["", "## BLOCKER", *([f"- {b}" for b in r.blocked_reasons] or ["- (없음)"]),
              "", "## WARN", *([f"- {w}" for w in r.warn_reasons] or ["- (없음)"]),
              "", "## 다음 단계", *([f"- {n}" for n in r.next_actions] or ["- (없음)"]),
              "", f"> {r.disclaimer}"]
    return "\n".join(lines) + "\n"
