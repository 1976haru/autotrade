"""#63 / 8-01 — EXE Preflight Smoke Test 평가 로직 (read-only).

EXE 빌드 전후 기본 작동 여부를 한 번에 확인한다: health / config 안전 flag /
KIS credentials / DB / auto loop / Agent Council / Decision Episode / build·version
/ update status 를 점검해 PASS / WARN / FAIL 로 분류.

설계:
  - `build_checks(inputs)` — *순수 함수*. gathered 입력 dict → check 리스트.
    (테스트가 다양한 시나리오를 dict 로 주입.)
  - `gather_inputs(db=None)` — 실 소스에서 read-only 수집 (각 항목 try/except,
    절대 예외를 밖으로 던지지 않음).
  - `evaluate_preflight(db=None)` — gather + build + summary.

절대 invariant:
  - broker / OrderExecutor / route_order 호출 0건, 주문 endpoint 호출 0건.
  - DB 는 read-only SELECT 만 (write 0건).
  - 응답·메시지에 Secret / API key / 계좌번호 원문 0건 (boolean / count / 이름만).
  - `is_live_authorization=False`, `contains_secret=False` 불변.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

# 장이 닫힌 날 등은 *운영상 정상* — 치명 FAIL 이 아니라 WARN/PASS-with-note.
_MARKET_WARN_REASONS = {"MARKET_CLOSED", "NO_MARKET_DATA"}
_SAFE_MODES = {"SIMULATION", "PAPER", "LIVE_SHADOW"}


def _check(name: str, status: str, message: str) -> dict:
    return {"name": name, "status": status, "message": message}


def build_checks(inputs: dict[str, Any]) -> list[dict]:
    """gathered 입력 → check 리스트 (순수 함수, secret 0건)."""
    checks: list[dict] = []
    g = inputs or {}

    # 1. backend health — 평가 시점에 backend 가 살아 있어야 본 함수가 호출된다.
    checks.append(_check("backend_health", PASS, "Backend 정상 응답"))

    # 2. DB 상태.
    db_ready = g.get("db_ready")
    if db_ready is True:
        checks.append(_check("db_status", PASS, "DB 연결 정상"))
    elif db_ready is False:
        checks.append(_check("db_status", FAIL, "DB 연결 실패 / 마이그레이션 미완료"))
    else:
        checks.append(_check("db_status", WARN, "DB 상태 확인 불가"))

    # 3. 안전 flag — LIVE/AI/FUTURES OFF + KIS_IS_PAPER ON.
    live = bool(g.get("enable_live_trading"))
    ai = bool(g.get("enable_ai_execution"))
    futures = bool(g.get("enable_futures_live_trading"))
    kis_is_paper = g.get("kis_is_paper")
    flag_violations = []
    if live:
        flag_violations.append("ENABLE_LIVE_TRADING")
    if ai:
        flag_violations.append("ENABLE_AI_EXECUTION")
    if futures:
        flag_violations.append("ENABLE_FUTURES_LIVE_TRADING")
    if kis_is_paper is False:
        flag_violations.append("KIS_IS_PAPER=false")
    if flag_violations:
        checks.append(_check(
            "safety_flags", FAIL,
            "위험 flag 활성: " + ", ".join(flag_violations),
        ))
    else:
        checks.append(_check(
            "safety_flags", PASS,
            "LIVE/AI/FUTURES OFF · KIS_IS_PAPER ON",
        ))

    # 4. default_mode — LIVE_* 금지 (LIVE_SHADOW 는 허용).
    mode = str(g.get("default_mode") or "")
    if mode in _SAFE_MODES:
        checks.append(_check("default_mode", PASS, f"운용모드 {mode}"))
    elif mode.upper().startswith("LIVE_"):
        checks.append(_check("default_mode", FAIL, f"실거래 계열 모드 {mode}"))
    else:
        checks.append(_check("default_mode", WARN, f"운용모드 확인 불가 ({mode or '미상'})"))

    # 5. is_live_authorization — 항상 false 여야.
    if g.get("is_live_authorization") is True:
        checks.append(_check("is_live_authorization", FAIL, "실거래 권한 활성 (비정상)"))
    else:
        checks.append(_check("is_live_authorization", PASS, "실거래 권한 없음"))

    # 6. KIS credentials present.
    creds_present = g.get("credentials_present")
    missing = g.get("missing_credentials") or []
    if creds_present is True:
        checks.append(_check("kis_credentials", PASS, "KIS 자격 구성됨"))
    else:
        names = ", ".join(missing) if missing else "일부 자격"
        checks.append(_check(
            "kis_credentials", WARN,
            f"KIS 자격 미구성 ({names}) — Mock 으로는 실행 가능",
        ))

    # 7. KIS Paper readiness — 안전 위반 BLOCKED 는 FAIL, 그 외 PASS/WARN.
    ready = g.get("kis_paper_ready")
    blocked = g.get("kis_paper_blocked_reasons") or []
    safety_blocks = {
        "ENABLE_LIVE_TRADING_TRUE", "ENABLE_AI_EXECUTION_TRUE",
        "ENABLE_FUTURES_LIVE_TRUE", "KIS_IS_PAPER_FALSE", "DEFAULT_MODE_LIVE",
    }
    if any(b in safety_blocks for b in blocked):
        checks.append(_check(
            "kis_paper_readiness", FAIL,
            "안전 위반으로 BLOCKED: " + ", ".join(blocked),
        ))
    elif ready is True:
        checks.append(_check("kis_paper_readiness", PASS, "READY"))
    else:
        checks.append(_check("kis_paper_readiness", WARN, "BLOCKED (자격/설정 점검 필요)"))

    # 8. KIS Paper Auto env 조합.
    auto_ready = g.get("kis_paper_auto_ready")
    if auto_ready is True:
        checks.append(_check("kis_paper_auto_env", PASS, "KIS 모의 자동주문 env 조합 READY"))
    else:
        checks.append(_check(
            "kis_paper_auto_env", WARN,
            "KIS 모의 자동주문 BLOCKED (자격/플래그 점검) — Mock 가능",
        ))

    # 9. broker_order_type — paper-safe(KIS_PAPER/MOCK) 여야. live → FAIL.
    bot = str(g.get("broker_order_type") or "")
    if bot == "KIS_PAPER":
        checks.append(_check("broker_order_type", PASS, "KIS_PAPER"))
    elif bot in ("MOCK", ""):
        checks.append(_check("broker_order_type", PASS, f"{bot or 'MOCK'} (paper-safe)"))
    else:
        checks.append(_check("broker_order_type", FAIL, f"비-paper broker: {bot}"))

    # 10. auto loop 상태.
    loop_state = str(g.get("loop_state") or "")
    loop_error = g.get("loop_last_error")
    if loop_error:
        checks.append(_check("auto_loop", WARN, f"loop 상태 {loop_state} · 최근 오류 있음"))
    elif loop_state in ("MARKET_CLOSED", "WAITING_MARKET"):
        checks.append(_check("auto_loop", WARN, f"loop {loop_state} (장 시간 외 정상)"))
    elif loop_state:
        checks.append(_check("auto_loop", PASS, f"loop 상태 {loop_state}"))
    else:
        checks.append(_check("auto_loop", WARN, "loop 상태 확인 불가"))

    # 11. Agent Council 구성 가용.
    council_roles = g.get("agent_council_roles")
    if isinstance(council_roles, int) and council_roles > 0:
        checks.append(_check("agent_council", PASS, f"Agent Council 구성됨 ({council_roles} roles)"))
    else:
        checks.append(_check("agent_council", WARN, "Agent Council 구성 확인 불가"))

    # 12. Decision Episode API (최근 episode 수).
    ep = g.get("decision_episode_count")
    if ep is None:
        checks.append(_check("decision_episode_api", WARN, "Decision Episode 조회 불가"))
    elif ep > 0:
        checks.append(_check("decision_episode_api", PASS, f"최근 7일 Decision Episode {ep}건"))
    else:
        checks.append(_check("decision_episode_api", WARN, "최근 Decision Episode 없음"))

    # 13. no-trade reason 기록 가능 여부.
    if g.get("no_trade_capable") is True:
        checks.append(_check("no_trade_reason", PASS, "no-trade 사유 기록/조회 가능"))
    else:
        checks.append(_check("no_trade_reason", WARN, "no-trade 사유 기능 확인 불가"))

    # 14. build / version / commit.
    bi = g.get("build_info") or {}
    commit = str(bi.get("commit") or "unknown")
    version = str(bi.get("version") or "unknown")
    if commit not in ("", "unknown") and version not in ("", "unknown", "0.0.0-unknown"):
        checks.append(_check("build_info", PASS, f"v{version} · {commit}"))
    else:
        checks.append(_check(
            "build_info", WARN,
            f"build 정보 일부 unknown (v{version} · {commit})",
        ))

    # 15. update status — updater 비활성/미구현 단계.
    upd = g.get("update_status")
    if upd in ("UP_TO_DATE", "UPDATE_AVAILABLE"):
        checks.append(_check("update_status", PASS, f"업데이트 상태 {upd}"))
    else:
        checks.append(_check("update_status", WARN, "업데이트 확인 불가 (updater 비활성)"))

    # 16. sidecar status.
    sc = str(g.get("sidecar_status") or "")
    if sc == "RUNNING":
        checks.append(_check("sidecar_status", PASS, "Sidecar 실행 중"))
    else:
        checks.append(_check("sidecar_status", WARN, f"Sidecar 상태 {sc or '확인 불가'}"))

    return checks


def summarize(checks: list[dict]) -> dict:
    pass_count = sum(1 for c in checks if c["status"] == PASS)
    warn_count = sum(1 for c in checks if c["status"] == WARN)
    fail_count = sum(1 for c in checks if c["status"] == FAIL)
    status = FAIL if fail_count else (WARN if warn_count else PASS)
    return {
        "status": status,
        "pass_count": pass_count,
        "warn_count": warn_count,
        "fail_count": fail_count,
        "total": len(checks),
    }


# ──────────────────────────────────────────────────────────────────────────────
# gather — 실 소스 read-only 수집 (각 항목 try/except, 예외 0건)
# ──────────────────────────────────────────────────────────────────────────────


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def gather_inputs(db=None) -> dict[str, Any]:
    """실 backend 상태를 read-only 로 수집. 어떤 항목이 실패해도 예외 없이 진행."""
    from app.core.config import get_settings

    inputs: dict[str, Any] = {}
    settings = _safe(get_settings)

    if settings is not None:
        mode = getattr(settings, "default_mode", None)
        inputs["default_mode"] = str(getattr(mode, "value", mode) or "")
        inputs["enable_live_trading"] = bool(getattr(settings, "enable_live_trading", False))
        inputs["enable_ai_execution"] = bool(getattr(settings, "enable_ai_execution", False))
        inputs["enable_futures_live_trading"] = bool(
            getattr(settings, "enable_futures_live_trading", False))
        inputs["kis_is_paper"] = bool(getattr(settings, "kis_is_paper", True))
    inputs["is_live_authorization"] = False

    # DB ready.
    def _db_ready():
        from app.db.migration_runner import db_is_ready
        return bool(db_is_ready())
    inputs["db_ready"] = _safe(_db_ready)

    # KIS readiness (broker 호출 0건).
    def _readiness():
        from app.kis_paper.readiness import evaluate_readiness
        rd = evaluate_readiness(settings)
        return rd
    rd = _safe(_readiness)
    if rd is not None:
        inputs["kis_paper_ready"] = bool(getattr(rd, "ready", False))
        inputs["kis_paper_blocked_reasons"] = [
            str(getattr(b, "value", b)) for b in (getattr(rd, "blocked_reasons", ()) or ())
        ]
        inputs["credentials_present"] = bool(getattr(rd, "credentials_present", False))
        inputs["missing_credentials"] = list(getattr(rd, "missing_credentials", ()) or ())
        sf = getattr(rd, "safety_flags", {}) or {}
        pbk = str(sf.get("paper_broker_kind") or "")
        inputs["broker_order_type"] = "KIS_PAPER" if pbk == "KIS_PAPER" else (pbk or "MOCK")
        # auto env 조합: 자격 + 안전 + auto on + kis paper.
        auto_on = bool(sf.get("enable_kis_paper_auto_trading"))
        inputs["kis_paper_auto_ready"] = bool(
            getattr(rd, "ready", False)
            and getattr(rd, "credentials_present", False)
            and auto_on
            and getattr(rd, "kis_is_paper", True)
        )

    # auto loop 상태.
    def _loop():
        from app.auto_paper.loop import get_auto_paper_loop
        return get_auto_paper_loop().status()
    snap = _safe(_loop)
    if snap is not None:
        inputs["loop_state"] = str(getattr(snap, "state", "") or "")
        inputs["loop_last_error"] = getattr(snap, "last_error", None)

    # Agent Council 구성.
    def _council():
        from app.agents.roles import build_default_registry
        return len(build_default_registry())
    inputs["agent_council_roles"] = _safe(_council)

    # Decision Episode 최근 7일 count (read-only SELECT).
    if db is not None:
        def _episodes():
            from sqlalchemy import func, select
            from app.db.models import AgentDecisionEpisode
            since = datetime.now(timezone.utc) - timedelta(days=7)
            return int(db.execute(
                select(func.count(AgentDecisionEpisode.id)).where(
                    AgentDecisionEpisode.created_at >= since,
                )
            ).scalar() or 0)
        inputs["decision_episode_count"] = _safe(_episodes)

    # no-trade reason 기능 가용 (빈 입력으로 호출 가능하면 PASS).
    def _no_trade():
        from app.auto_paper.no_trade_reasons import summarize_no_trade_reasons
        summarize_no_trade_reasons([], limit=1)
        return True
    inputs["no_trade_capable"] = _safe(_no_trade, default=False)

    # build info.
    def _build():
        from app.system.build_info import get_build_info
        return get_build_info()
    inputs["build_info"] = _safe(_build, default={})

    # sidecar status (build_info 의 source 와 별개 — exe-status 헬퍼 재사용).
    def _sidecar():
        import os
        marker = str(os.getenv("AUTOTRADE_DESKTOP_SIDECAR", "") or "").strip().lower()
        return "RUNNING" if marker in ("1", "true", "yes", "on") else "UNKNOWN"
    inputs["sidecar_status"] = _safe(_sidecar, default="UNKNOWN")

    # update status — updater 비활성 (mock). 확인 불가 → WARN.
    inputs["update_status"] = "UNKNOWN"

    return inputs


def evaluate_preflight(db=None) -> dict:
    """preflight 결과 dict. read-only, broker 호출 0건, secret 0건."""
    inputs = gather_inputs(db=db)
    checks = build_checks(inputs)
    return {
        "summary": summarize(checks),
        "checks": checks,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_live_authorization": False,
        "contains_secret": False,
    }
