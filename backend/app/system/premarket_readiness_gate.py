"""BUILD-02A: 장 열리기 전 사전 검증 게이트 (offline, read-only).

장이 열리기 전에도 확인 가능한 모든 항목을 자동 점검해 하나의 사전 검증 리포트로
묶는다 — 환경변수 / KIS 자격 present / Paper·Live 분리 / Universe fallback /
Portfolio source / Agent 카드 backing / BUILD-01 통합 정합성 / 문서·Runbook 존재 /
백테스트·Walk-forward·스트레스·테스트·lint·build 명령 가용성.

**본 작업은 장중 KIS 모의 *실주문* 테스트(BUILD-02B) 전 *준비 상태* 확인이다.**
실제 KIS API 를 호출하지 않으며, 실전/모의 주문을 보내지 않는다. KIS 자격은
*present 여부* 만 확인하고 원문 값은 절대 출력하지 않는다.

모드:
- **fast** (기본, API + CLI): 전부 *in-process* (기존 gate 함수 재사용, subprocess 0).
- **full** (CLI 전용): fast + 외부 명령 *plan*(ruff/pytest/npm/security_scan/report
  scripts). plan 은 본 모듈이 *생성만* 하고, 실행은 CLI 가 담당한다.

CLAUDE.md 가드 (정적 grep 으로 lock):
- broker / OrderExecutor / 단일 주문 라우터 / paper_trader / KIS 어댑터 import 0건.
- broker 주문/취소 호출 0건, DB write 0건, 외부 HTTP / AI SDK import 0건.
- `PremarketReadinessReport.is_live_authorization=False` / `broker_order_sent=False` /
  `order_created=False` / `contains_secret=False` 불변.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]

# 존재해야 하는 문서.
REQUIRED_DOCS = (
    "docs/user_manual.md", "docs/runbook.md",
    "docs/prebuild_program_integrity_gate.md", "docs/agent_stress_test.md",
    "docs/strategy_council_backtest.md", "docs/strategy_council_walk_forward.md",
    "docs/live_trading_off_policy.md",
)
# 존재해야 하는 리포트 스크립트.
REQUIRED_SCRIPTS = (
    "scripts/run_program_integrity_gate.py", "scripts/run_agent_stress_test.py",
    "scripts/run_strategy_optimization.py", "scripts/run_strategy_council_walk_forward.py",
)
# 문서에서 *단언형* 으로 쓰이면 안 되는 문구 (부정/메타 인용은 허용).
_FORBIDDEN_DOC_PHRASES = ("수익 보장", "실전 전환 승인")
# 항상 금지 (마케팅 단언).
_HARD_FORBIDDEN_DOC_PHRASES = ("월 수익 보장", "원금 보장")
# 같은 줄에 이 중 하나라도 있으면 *부정/메타* 로 보고 허용.
_NEGATION_MARKERS = ("아님", "않", "없", "0건", "금지", "미", "\"", "*")


def _doc_violations(txt: str) -> list[str]:
    """문서에서 *단언형* 금지 문구만 검출 (부정/인용 줄은 허용)."""
    found: list[str] = []
    for ph in _HARD_FORBIDDEN_DOC_PHRASES:
        if ph in txt:
            found.append(ph)
    for line in txt.splitlines():
        for ph in _FORBIDDEN_DOC_PHRASES:
            if ph in line and not any(m in line for m in _NEGATION_MARKERS):
                found.append(ph)
    return found


class PVerdict(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


class PSection(StrEnum):
    ENV_READINESS = "ENV_READINESS"
    KIS_CREDENTIALS = "KIS_CREDENTIALS"
    PAPER_LIVE_SEPARATION = "PAPER_LIVE_SEPARATION"
    UNIVERSE_FALLBACK = "UNIVERSE_FALLBACK"
    PORTFOLIO_SOURCE = "PORTFOLIO_SOURCE"
    AGENT_CARDS = "AGENT_CARDS"
    PROGRAM_INTEGRITY = "PROGRAM_INTEGRITY"
    PREFLIGHT_SMOKE = "PREFLIGHT_SMOKE"
    DOCS_RUNBOOK = "DOCS_RUNBOOK"
    REPORT_SCRIPTS = "REPORT_SCRIPTS"
    BACKEND_QUALITY = "BACKEND_QUALITY"       # full mode: ruff/pytest/security_scan
    FRONTEND_QUALITY = "FRONTEND_QUALITY"     # full mode: npm lint/test/build


# full mode 에서만 실행되는 명령(섹션).
_FULL_MODE_SECTIONS = frozenset({PSection.BACKEND_QUALITY, PSection.FRONTEND_QUALITY})

DISCLAIMER_KO = (
    "본 리포트는 장 열리기 전 사전 검증 자료입니다. 실제 KIS API 호출 0건, 실전/모의 "
    "주문 0건, 실전 승인이 아닙니다. 수익을 보장하지 않습니다. 장중 실제 KIS 모의 "
    "주문/체결 테스트는 다음 단계(BUILD-02B)에서 진행합니다."
)


@dataclass(frozen=True)
class PremarketCheckItem:
    name:        str
    verdict:     str
    reason_code: str
    detail:      str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "verdict": self.verdict,
                "reason_code": self.reason_code, "detail": self.detail}


@dataclass(frozen=True)
class PremarketCheckSection:
    section: str
    verdict: str
    items:   tuple[PremarketCheckItem, ...] = ()
    note:    str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"section": self.section, "verdict": self.verdict, "note": self.note,
                "items": [i.to_dict() for i in self.items]}


@dataclass(frozen=True)
class FullModeCommand:
    """full mode 외부 명령 plan — 본 모듈은 *생성만*, 실행은 CLI."""
    name:    str
    section: str
    argv:    tuple[str, ...]
    cwd:     str   # repo-root 상대 (예: "backend", "frontend", ".")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "section": self.section,
                "argv": list(self.argv), "cwd": self.cwd}


@dataclass(frozen=True)
class PremarketReadinessReport:
    generated_at:    str
    mode:            str
    sections:        tuple[PremarketCheckSection, ...]
    counts:          dict[str, int]
    overall_status:  str
    premarket_ready: bool
    build_ready_for_offline: bool
    ready_for_market_open_rehearsal: bool
    missing_items:   tuple[str, ...]
    warn_items:      tuple[str, ...]
    fail_items:      tuple[str, ...]
    full_mode_command_plan: tuple[dict[str, Any], ...] = ()

    is_live_authorization: bool = False
    broker_order_sent:     bool = False
    order_created:         bool = False
    contains_secret:       bool = False
    disclaimer:            str = DISCLAIMER_KO

    def __post_init__(self) -> None:
        for name in ("is_live_authorization", "broker_order_sent",
                     "order_created", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (사전 검증은 주문 0건)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":    self.generated_at,
            "mode":            self.mode,
            "sections":        [s.to_dict() for s in self.sections],
            "counts":          dict(self.counts),
            "overall_status":  self.overall_status,
            "premarket_ready": bool(self.premarket_ready),
            "build_ready_for_offline": bool(self.build_ready_for_offline),
            "ready_for_market_open_rehearsal": bool(self.ready_for_market_open_rehearsal),
            "missing_items":   list(self.missing_items),
            "warn_items":      list(self.warn_items),
            "fail_items":      list(self.fail_items),
            "full_mode_command_plan": [dict(c) for c in self.full_mode_command_plan],
            "is_live_authorization": False,
            "broker_order_sent":     False,
            "order_created":         False,
            "contains_secret":       False,
            "disclaimer":            self.disclaimer,
        }


@dataclass(frozen=True)
class PremarketInputs:
    enable_live_trading: bool = False
    enable_ai_execution: bool = False
    enable_futures_live_trading: bool = False
    kis_is_paper: bool = True
    default_mode: str = "SIMULATION"
    kis_credentials_present: Optional[bool] = None    # None → 미상 → WARN
    market_data_provider: str = "mock"
    preflight_result: Optional[dict[str, Any]] = None  # 런타임(DB) 주입 시만.
    # INSTALL-UX-FIX-01: 설치본/CI 런타임은 docs/scripts 미번들이 정상 → 누락 시 SKIP(참고용).
    app_runtime: str = "SOURCE_DEV"


def _item(name, verdict: PVerdict, code, detail) -> PremarketCheckItem:
    return PremarketCheckItem(name, verdict.value, code, detail)


def _section_verdict(items: tuple[PremarketCheckItem, ...]) -> str:
    if any(i.verdict == PVerdict.FAIL.value for i in items):
        return PVerdict.FAIL.value
    if any(i.verdict == PVerdict.WARN.value for i in items):
        return PVerdict.WARN.value
    if items and all(i.verdict == PVerdict.SKIP.value for i in items):
        return PVerdict.SKIP.value
    return PVerdict.PASS.value


# ─────────────────────────────────────────────────────────────────────────────
# fast mode 섹션 (in-process)
# ─────────────────────────────────────────────────────────────────────────────


def _sec_env(inp: PremarketInputs) -> PremarketCheckSection:
    items = [
        _item("ENABLE_LIVE_TRADING",
              PVerdict.PASS if not inp.enable_live_trading else PVerdict.FAIL,
              "ENABLE_LIVE_TRADING", f"{inp.enable_live_trading} (안전값 false)"),
        _item("ENABLE_AI_EXECUTION",
              PVerdict.PASS if not inp.enable_ai_execution else PVerdict.FAIL,
              "ENABLE_AI_EXECUTION", f"{inp.enable_ai_execution} (안전값 false)"),
        _item("ENABLE_FUTURES_LIVE_TRADING",
              PVerdict.PASS if not inp.enable_futures_live_trading else PVerdict.FAIL,
              "ENABLE_FUTURES_LIVE_TRADING", f"{inp.enable_futures_live_trading} (안전값 false)"),
        _item("KIS_IS_PAPER",
              PVerdict.PASS if inp.kis_is_paper else PVerdict.FAIL,
              "KIS_IS_PAPER", f"{inp.kis_is_paper} (안전값 true)"),
    ]
    return PremarketCheckSection(PSection.ENV_READINESS.value,
                                 _section_verdict(tuple(items)), tuple(items),
                                 note="기본 안전 플래그 점검 (실거래 OFF).")


def _sec_kis_credentials(inp: PremarketInputs) -> PremarketCheckSection:
    if inp.kis_credentials_present is None:
        v, code, detail = (PVerdict.WARN, "KIS_CREDENTIALS_UNKNOWN",
                           "KIS 자격 present 여부 미상 — 장중 리허설(BUILD-02B) 전 확인 필요.")
    elif inp.kis_credentials_present:
        v, code, detail = (PVerdict.PASS, "KIS_CREDENTIALS_PRESENT",
                           "KIS 모의 자격 present (원문 미표시).")
    else:
        v, code, detail = (PVerdict.WARN, "KIS_CREDENTIALS_MISSING",
                           "KIS 자격 미설정 — offline 빌드는 가능, 장중 리허설 전 입력 필요.")
    return PremarketCheckSection(PSection.KIS_CREDENTIALS.value, v.value,
                                 (_item("kis_credentials_present", v, code, detail),),
                                 note="present 여부만 확인 — secret/계좌 원문 0건.")


def _sec_paper_live_separation(inp: PremarketInputs) -> PremarketCheckSection:
    from app.kis.endpoints import resolve_kis_endpoint
    r = resolve_kis_endpoint(kis_is_paper=inp.kis_is_paper, explicit_live_gate_passed=False)
    sep = bool(r.paper_live_separated)
    v = PVerdict.PASS if sep else PVerdict.FAIL
    return PremarketCheckSection(PSection.PAPER_LIVE_SEPARATION.value, v.value,
                                 (_item("paper_live_separated", v, r.reason_code,
                                        f"selected_mode={r.selected_mode} separated={sep}"),),
                                 note="KIS Paper/Live 경로 분리 + fallback 금지.")


def _sec_universe() -> PremarketCheckSection:
    from app.universe.universe_status import build_universe_status
    s = build_universe_status(user_symbols=None)
    v = PVerdict.PASS if s.universe_count > 0 else PVerdict.FAIL
    return PremarketCheckSection(PSection.UNIVERSE_FALLBACK.value, v.value,
                                 (_item("universe_fallback", v, s.reason_code,
                                        f"{s.universe_source} {s.universe_count}개"),),
                                 note="관심종목 없으면 기본 50 fallback.")


def _sec_portfolio() -> PremarketCheckSection:
    from app.portfolio.portfolio_snapshot import build_paper_simulated_snapshot
    from types import SimpleNamespace

    def _fake(db=None, *, last_prices=None, now=None):
        return SimpleNamespace(
            current_cash=9_280_000, total_equity=10_000_000, starting_cash=10_000_000,
            total_position_value=720_000, total_unrealized_pnl=20_000, position_count=1,
            invested_krw=700_000, realized_pnl=0, last_event_at=None, positions=[])

    snap = build_paper_simulated_snapshot(db=None, builder=_fake)
    ok = snap.source == "PAPER_SIMULATED" and snap.value_available
    v = PVerdict.PASS if ok else PVerdict.FAIL
    return PremarketCheckSection(PSection.PORTFOLIO_SOURCE.value, v.value,
                                 (_item("portfolio_source", v, "PORTFOLIO_SOURCE",
                                        f"source={snap.source} cash≠None={snap.cash is not None}"),),
                                 note="현금/총자산 source 통일 + 0원 fallback 금지.")


def _sec_agent_cards() -> PremarketCheckSection:
    from app.agents.decision_explanation import build_decision_explanation
    from app.agents.decision_quality import compute_decision_quality
    from app.agents.order_quality_metrics import aggregate_order_quality
    from app.agents.post_trade_feedback import build_feedback_loop
    items: list[PremarketCheckItem] = []
    # 설명 카드 backing.
    exp = build_decision_explanation({"final_action": "BUY",
                                      "votes": [{"strategy": "ORB", "signal": "BUY", "score": 70}]})
    items.append(_item("explanation_card", PVerdict.PASS if exp.entry_reason else PVerdict.FAIL,
                       "AGENT_EXPLANATION", "entry/counter/exit/risk 설명 생성"))
    # 품질 점수 카드 backing.
    q = compute_decision_quality(votes=[{"strategy": "ORB", "signal": "BUY", "score": 70}],
                                 final_action="BUY", has_exit_plan=True, exit_plan_valid=True)
    items.append(_item("quality_card", PVerdict.PASS if 0 <= q.enhanced_quality_score <= 100
                       else PVerdict.FAIL, "AGENT_QUALITY", f"enhanced={q.enhanced_quality_score}"))
    # 복기 피드백 카드 backing.
    fb = build_feedback_loop({"by_review_grade": {"GOOD": 1}})
    items.append(_item("feedback_card", PVerdict.PASS if fb.auto_apply_allowed is False
                       else PVerdict.FAIL, "AGENT_FEEDBACK", "auto_apply_allowed=False"))
    # 성과 지표 카드 backing.
    oq = aggregate_order_quality([])
    items.append(_item("performance_card", PVerdict.PASS if oq.status in ("OK", "INSUFFICIENT_DATA")
                       else PVerdict.FAIL, "AGENT_PERFORMANCE", f"status={oq.status}"))
    return PremarketCheckSection(PSection.AGENT_CARDS.value, _section_verdict(tuple(items)),
                                 tuple(items),
                                 note="설명/성과/품질/피드백 카드 backing 함수 정상 (주문 버튼 0개).")


def _sec_program_integrity(inp: PremarketInputs) -> PremarketCheckSection:
    from app.system.program_integrity_gate import GateInputs, run_program_integrity_gate
    rep = run_program_integrity_gate(GateInputs(
        enable_live_trading=inp.enable_live_trading,
        enable_ai_execution=inp.enable_ai_execution,
        enable_futures_live_trading=inp.enable_futures_live_trading,
        kis_is_paper=inp.kis_is_paper, default_mode=inp.default_mode,
        kis_credentials_present=inp.kis_credentials_present))
    v = PVerdict.PASS if rep.build_ready else PVerdict.FAIL
    items = [
        _item("build_ready", v, rep.reason_code, f"build_ready={rep.build_ready}"),
        _item("integrity_fail_count",
              PVerdict.PASS if rep.counts.get("FAIL", 0) == 0 else PVerdict.FAIL,
              "INTEGRITY_FAILS", f"FAIL={rep.counts.get('FAIL', 0)}"),
    ]
    return PremarketCheckSection(PSection.PROGRAM_INTEGRITY.value, _section_verdict(tuple(items)),
                                 tuple(items), note="BUILD-01 전체 흐름 정합성 (offline/fake).")


def _sec_preflight(inp: PremarketInputs) -> PremarketCheckSection:
    pr = inp.preflight_result
    if not isinstance(pr, dict):
        return PremarketCheckSection(PSection.PREFLIGHT_SMOKE.value, PVerdict.SKIP.value,
                                     (_item("preflight", PVerdict.SKIP, "PREFLIGHT_NOT_RUN",
                                            "런타임(DB) 미주입 — API/CLI 실행 시 점검"),),
                                     note="preflight 는 런타임 DB 필요 (API endpoint 에서 주입).")
    status = str(pr.get("overall_status") or pr.get("status") or "UNKNOWN").upper()
    v = (PVerdict.PASS if status in ("PASS", "OK")
         else PVerdict.FAIL if status == "FAIL" else PVerdict.WARN)
    return PremarketCheckSection(PSection.PREFLIGHT_SMOKE.value, v.value,
                                 (_item("preflight", v, "PREFLIGHT", f"status={status}"),),
                                 note="EXE preflight smoke (read-only).")


def _sec_docs(inp: PremarketInputs | None = None) -> PremarketCheckSection:
    # 설치본/CI 런타임은 docs 미번들이 정상 → 누락 시 FAIL 대신 SKIP(참고용).
    source_dev = (inp is None) or (inp.app_runtime == "SOURCE_DEV")
    missing_verdict = PVerdict.FAIL if source_dev else PVerdict.SKIP
    items: list[PremarketCheckItem] = []
    for rel in REQUIRED_DOCS:
        p = _REPO_ROOT / rel
        items.append(_item(rel, PVerdict.PASS if p.exists() else missing_verdict,
                           "DOC_EXISTS",
                           "존재" if p.exists() else ("누락" if source_dev
                                                    else "설치본 미번들(참고용)")))
    # runbook 의 문제 보고 양식 / Claude Code 전달 금지 문구.
    runbook = _REPO_ROOT / "docs" / "runbook.md"
    rb = runbook.read_text(encoding="utf-8") if runbook.exists() else ""
    items.append(_item("problem_report_form",
                       PVerdict.PASS if ("문제 보고" in rb or "보고 양식" in rb) else PVerdict.WARN,
                       "PROBLEM_REPORT_FORM", "runbook 문제 보고 양식"))
    items.append(_item("claude_code_redaction_notice",
                       PVerdict.PASS if "Claude Code" in rb else PVerdict.WARN,
                       "CLAUDE_CODE_NOTICE", "전달 금지 정보 안내"))
    # 금지 문구 점검 (단언형만 — 부정/인용은 허용).
    bad: list[str] = []
    for rel in REQUIRED_DOCS:
        p = _REPO_ROOT / rel
        if not p.exists():
            continue
        for ph in _doc_violations(p.read_text(encoding="utf-8")):
            bad.append(f"{rel}:{ph}")
    items.append(_item("no_forbidden_phrases",
                       PVerdict.PASS if not bad else PVerdict.FAIL,
                       "DOC_PHRASES", "금지 문구 없음" if not bad else f"발견: {bad[:3]}"))
    return PremarketCheckSection(PSection.DOCS_RUNBOOK.value, _section_verdict(tuple(items)),
                                 tuple(items), note="문서/Runbook 존재 + 안전 문구.")


def _sec_report_scripts(inp: PremarketInputs | None = None) -> PremarketCheckSection:
    # 설치본/CI 런타임은 report 스크립트 미번들이 정상 → 누락 시 FAIL 대신 SKIP(참고용).
    source_dev = (inp is None) or (inp.app_runtime == "SOURCE_DEV")
    missing_verdict = PVerdict.FAIL if source_dev else PVerdict.SKIP
    items: list[PremarketCheckItem] = []
    for rel in REQUIRED_SCRIPTS:
        p = _REPO_ROOT / rel
        items.append(_item(rel, PVerdict.PASS if p.exists() else missing_verdict,
                           "SCRIPT_EXISTS",
                           "존재" if p.exists() else ("누락" if source_dev
                                                    else "설치본 미번들(참고용)")))
    return PremarketCheckSection(PSection.REPORT_SCRIPTS.value, _section_verdict(tuple(items)),
                                 tuple(items),
                                 note="백테스트/Walk-forward/스트레스/정합성 스크립트 가용 (full mode 실행).")


# ─────────────────────────────────────────────────────────────────────────────
# full mode 명령 plan
# ─────────────────────────────────────────────────────────────────────────────


def full_mode_command_plan() -> tuple[FullModeCommand, ...]:
    """full mode (CLI 전용) 외부 명령 plan — 본 모듈은 *생성만*, 실행은 CLI."""
    return (
        FullModeCommand("ruff check", PSection.BACKEND_QUALITY.value,
                        ("python", "-m", "ruff", "check", "app", "tests"), "backend"),
        FullModeCommand("pytest (not slow)", PSection.BACKEND_QUALITY.value,
                        ("python", "-m", "pytest", "-q", "-m", "not slow",
                         "--timeout=120", "--timeout-method=thread"), "backend"),
        FullModeCommand("security_scan", PSection.BACKEND_QUALITY.value,
                        ("python", "scripts/security_scan.py"), "."),
        FullModeCommand("npm run lint", PSection.FRONTEND_QUALITY.value,
                        ("npm", "run", "lint"), "frontend"),
        FullModeCommand("npm test", PSection.FRONTEND_QUALITY.value,
                        ("npm", "test", "--", "--run", "--exclude", "**/*.stress.test.jsx"),
                        "frontend"),
        FullModeCommand("npm run build", PSection.FRONTEND_QUALITY.value,
                        ("npm", "run", "build"), "frontend"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────────────────────


def run_premarket_readiness_gate(
    inp: PremarketInputs | None = None, *, mode: str = "fast",
) -> PremarketReadinessReport:
    """fast mode in-process 사전 검증. full mode 는 추가로 command plan 을 carry."""
    inp = inp or PremarketInputs()
    mode = "full" if str(mode).lower() == "full" else "fast"

    sections = [
        _sec_env(inp), _sec_kis_credentials(inp), _sec_paper_live_separation(inp),
        _sec_universe(), _sec_portfolio(), _sec_agent_cards(),
        _sec_program_integrity(inp), _sec_preflight(inp), _sec_docs(inp),
        _sec_report_scripts(inp),
    ]
    # full mode 에서는 backend/frontend quality 섹션을 PENDING(SKIP)으로 추가 —
    # 실제 verdict 는 CLI 가 명령 실행 후 merge_full_mode_results 로 채운다.
    plan: tuple[FullModeCommand, ...] = ()
    if mode == "full":
        plan = full_mode_command_plan()
        sections.append(PremarketCheckSection(
            PSection.BACKEND_QUALITY.value, PVerdict.SKIP.value,
            (_item("backend_quality", PVerdict.SKIP, "FULL_MODE_PENDING",
                   "CLI full mode 에서 ruff/pytest/security_scan 실행 후 채움"),),
            note="full mode 외부 명령 (CLI 실행)."))
        sections.append(PremarketCheckSection(
            PSection.FRONTEND_QUALITY.value, PVerdict.SKIP.value,
            (_item("frontend_quality", PVerdict.SKIP, "FULL_MODE_PENDING",
                   "CLI full mode 에서 npm lint/test/build 실행 후 채움"),),
            note="full mode 외부 명령 (CLI 실행)."))

    return _finalize(inp, mode, tuple(sections), plan)


def _finalize(inp, mode, sections, plan) -> PremarketReadinessReport:
    counts = {v.value: 0 for v in PVerdict}
    missing: list[str] = []
    warn: list[str] = []
    fail: list[str] = []
    for s in sections:
        counts[s.verdict] = counts.get(s.verdict, 0) + 1
        for it in s.items:
            if it.verdict == PVerdict.FAIL.value:
                fail.append(f"{s.section}/{it.name}")
                if "MISSING" in it.reason_code or "누락" in it.detail:
                    missing.append(f"{s.section}/{it.name}")
            elif it.verdict == PVerdict.WARN.value:
                warn.append(f"{s.section}/{it.name}")

    has_fail = counts.get(PVerdict.FAIL.value, 0) > 0
    has_warn = counts.get(PVerdict.WARN.value, 0) > 0
    overall = (PVerdict.FAIL.value if has_fail
               else PVerdict.WARN.value if has_warn else PVerdict.PASS.value)

    premarket_ready = not has_fail
    # program integrity build_ready 여부.
    pi = next((s for s in sections if s.section == PSection.PROGRAM_INTEGRITY.value), None)
    pi_ready = pi is not None and pi.verdict == PVerdict.PASS.value
    build_ready_for_offline = premarket_ready and pi_ready
    # 장중 리허설 준비: offline 준비 + KIS 자격 present (없으면 리허설 불가).
    rehearsal = build_ready_for_offline and (inp.kis_credentials_present is True)

    return PremarketReadinessReport(
        generated_at=datetime.now(timezone.utc).isoformat(), mode=mode,
        sections=sections, counts=counts, overall_status=overall,
        premarket_ready=premarket_ready, build_ready_for_offline=build_ready_for_offline,
        ready_for_market_open_rehearsal=rehearsal,
        missing_items=tuple(missing), warn_items=tuple(warn), fail_items=tuple(fail),
        full_mode_command_plan=tuple(c.to_dict() for c in plan),
    )


def merge_full_mode_results(
    report: PremarketReadinessReport, command_results: dict[str, str],
) -> PremarketReadinessReport:
    """CLI 가 full mode 명령을 실행한 뒤 backend/frontend quality 섹션 verdict 갱신.

    command_results: {section_name: 'PASS'|'FAIL'} (CLI 가 exit code 로 산정).
    """
    new_sections: list[PremarketCheckSection] = []
    for s in report.sections:
        if s.section in {x.value for x in _FULL_MODE_SECTIONS} and s.section in command_results:
            res = command_results[s.section]
            v = PVerdict.PASS.value if res == "PASS" else PVerdict.FAIL.value
            items = tuple(PremarketCheckItem(i.name, v, i.reason_code,
                                             f"full mode 실행 결과: {res}") for i in s.items)
            new_sections.append(PremarketCheckSection(s.section, v, items, s.note))
        else:
            new_sections.append(s)
    plan = tuple(FullModeCommand(**c) for c in report.full_mode_command_plan)
    inp = PremarketInputs()  # 재판정에는 사용 안 함 (rehearsal 은 원 report 유지).
    merged = _finalize(inp, report.mode, tuple(new_sections), plan)
    # rehearsal 은 원래 KIS 자격 기반 판정을 유지 (merge 가 자격을 모름).
    object.__setattr__(merged, "ready_for_market_open_rehearsal",
                       report.ready_for_market_open_rehearsal and merged.premarket_ready)
    return merged


def summarize(report: PremarketReadinessReport) -> dict[str, Any]:
    return report.to_dict()


def render_markdown_report(report: PremarketReadinessReport) -> str:
    lines: list[str] = []
    lines.append("# 장 열리기 전 사전 검증 리포트 (BUILD-02A)")
    lines.append("")
    lines.append(f"> {report.disclaimer}")
    lines.append("")
    lines.append("## 전체 요약")
    lines.append(f"- 생성: {report.generated_at} · mode: {report.mode}")
    lines.append(f"- 전체 상태: **{report.overall_status}**")
    lines.append(f"- premarket_ready: **{'예' if report.premarket_ready else '아니오'}**")
    lines.append(f"- build_ready_for_offline: **{'예' if report.build_ready_for_offline else '아니오'}**")
    lines.append(f"- ready_for_market_open_rehearsal: "
                 f"**{'예' if report.ready_for_market_open_rehearsal else '아니오'}**")
    lines.append(f"- PASS {report.counts.get('PASS',0)} · WARN {report.counts.get('WARN',0)} · "
                 f"FAIL {report.counts.get('FAIL',0)} · SKIP {report.counts.get('SKIP',0)}")
    lines.append("")
    lines.append("## 섹션별 점검")
    lines.append("")
    lines.append("| 섹션 | 판정 | 비고 |")
    lines.append("|---|---|---|")
    for s in report.sections:
        lines.append(f"| {s.section} | {s.verdict} | {s.note} |")
    lines.append("")
    if report.fail_items:
        lines.append("## FAIL 조치 필요")
        for f in report.fail_items:
            lines.append(f"- {f}")
        lines.append("")
    if report.warn_items:
        lines.append("## WARN")
        for w in report.warn_items:
            lines.append(f"- {w}")
        lines.append("")
    lines.append("## 다음 단계 (BUILD-02B, 장중)")
    lines.append("- KIS 모의 자격 입력 → 장중 실제 KIS 모의 현재가/주문/체결 polling 리허설.")
    lines.append("- 본 사전 검증은 **실제 KIS API 호출 0건 · 실전 승인 아님 · 수익 보장 아님.**")
    lines.append("")
    return "\n".join(lines) + "\n"


__all__ = [
    "PVerdict", "PSection", "PremarketCheckItem", "PremarketCheckSection",
    "FullModeCommand", "PremarketInputs", "PremarketReadinessReport",
    "REQUIRED_DOCS", "REQUIRED_SCRIPTS",
    "run_premarket_readiness_gate", "full_mode_command_plan",
    "merge_full_mode_results", "summarize", "render_markdown_report",
]
