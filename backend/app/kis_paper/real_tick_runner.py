"""KIS 모의 자동매매 *real* multi-symbol tick runner (V1 wiring).

기존 `live_runner.build_kis_paper_tick_runner` 는 단일 종목(005930) 만 도는
검증용이고, V2 다종목 KIS 실시간 시세 스캔(`driver_bridge.kis_paper_realtime_scan_tick`)
은 background Paper Auto Loop 에만 연결돼 있었다. 본 모듈은 *one-click*
KIS Paper Engine 의 quick/slow 모드 tick_runner 를 V2 스캔으로 흘려보내는
얇은 wrapper 다.

USE_REAL_TICK_RUNNER 플래그:
- default `False` (안전) → 기존 `live_runner.build_kis_paper_tick_runner` 사용.
- True (운영자가 .env 에 명시 설정) → V2 다종목 스캔 위임.
- mock 모드는 본 flag 와 무관 — 항상 engine default counter loop.

본 모듈은 broker / OrderExecutor / route_order / KisBrokerAdapter / MockBrokerAdapter
를 *직접 import 하지 않는다* (정적 grep 가드). V2 스캔이 이미 sanctioned
경로(`execute_kis_paper_auto_order` → `route_order` → RiskManager →
PermissionGate → OrderExecutor) 를 통과한다.

안전 invariant (CLAUDE.md + #89 14원칙):
- 실계좌 주문 0건 — KisBrokerAdapter live 주문 경로 NotImplementedError
- KIS_IS_PAPER / ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION mutate 0건
- mock 으로 silent swap 0건 (KIS 시세 실패 시 종목별 skip + 명시 reason_code)
- broker 주문 메서드 직접 호출 0건 (V2 스캔에 위임)
- per-tick 결과 즉시 disk persist (오류 발생 시 운영자가 중단 시점까지 데이터 보존)
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable


_log = logging.getLogger(__name__)

TickResult = dict[str, Any]
TickRunner = Callable[[Any, Any, int], Awaitable[TickResult]]

# 디스크 persist 디렉토리 — `reports/*` 는 .gitignore 등재.
_DEFAULT_PERSIST_DIR = pathlib.Path("reports") / "kis_paper_test"

# auto_executor reason_code 상수 (live_runner 와 동일).
_KIS_PAPER_SUBMITTED       = "KIS_PAPER_SUBMITTED"
_KIS_PAPER_REJECTED        = "KIS_PAPER_REJECTED"
_KIS_PAPER_NEEDS_APPROVAL  = "KIS_PAPER_NEEDS_APPROVAL"
_BLOCKED_BY_PERMISSION_GATE = "BLOCKED_BY_PERMISSION_GATE"
_MISSING_EXIT_PLAN         = "MISSING_EXIT_PLAN"
_KIS_PAPER_ERROR           = "KIS_PAPER_ERROR"


def should_use_real_tick_runner(settings: Any) -> bool:
    """USE_REAL_TICK_RUNNER 플래그 평가 — default False.

    우선순위:
      1) `settings.use_real_tick_runner` (Settings 필드, env via pydantic)
      2) `os.environ["USE_REAL_TICK_RUNNER"]` (truthy 문자열)
      3) False
    """
    val = getattr(settings, "use_real_tick_runner", None)
    if val is not None:
        return bool(val)
    raw = os.environ.get("USE_REAL_TICK_RUNNER", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _zero_result() -> TickResult:
    return {
        "ai_decisions":          0,
        "ai_buy_signals":        0,
        "ai_sell_signals":       0,
        "ai_hold_signals":       0,
        "orders_attempted":      0,
        "orders_executed":       0,
        "orders_rejected":       0,
        "orders_needs_approval": 0,
        "risk_blocks":           0,
        "fills_observed":        0,
        "unfilled_count":        0,
        "errors":                0,
        "rate_limit_hit":        False,
        "failures":              [],
    }


def _is_rate_limit_message(msg: str) -> bool:
    up = (msg or "").upper()
    return "EGW00201" in up or "RATE LIMIT" in up or "RATE_LIMIT" in up


def _map_scan_to_tick_result(scan: dict[str, Any]) -> TickResult:
    """V2 스캔 응답 → engine TickResult dict 매핑.

    V2 스캔의 orders/skipped 리스트를 reason_code 별로 집계해 카운터 갱신.
    """
    out = _zero_result()
    out["ai_decisions"] = int(scan.get("symbols_scanned", 0) or 0)
    out["orders_attempted"] = int(scan.get("orders_attempted", 0) or 0)

    orders = list(scan.get("orders", []) or [])
    skipped = list(scan.get("skipped", []) or [])
    failures: list[str] = []

    for o in orders:
        side = str(o.get("side", "")).upper()
        rc = str(o.get("reason_code", "")).upper()
        if side == "BUY":
            out["ai_buy_signals"] += 1
        elif side == "SELL":
            out["ai_sell_signals"] += 1
        if rc == _KIS_PAPER_SUBMITTED:
            out["orders_executed"] += 1
            if str(o.get("fill_status", "")).upper() == "FILLED":
                out["fills_observed"] += 1
            elif bool(o.get("submitted")):
                out["unfilled_count"] += 1
        elif rc in (_KIS_PAPER_NEEDS_APPROVAL, _BLOCKED_BY_PERMISSION_GATE):
            out["orders_needs_approval"] += 1
        elif rc == _KIS_PAPER_REJECTED:
            out["orders_rejected"] += 1
            out["risk_blocks"] += 1
            msg = str(o.get("reason_message") or "")[:120]
            failures.append(f"{o.get('symbol', '?')} 주문 거부: {msg}")
        elif rc == _MISSING_EXIT_PLAN:
            out["risk_blocks"] += 1
            failures.append(
                f"{o.get('symbol', '?')} BUY 차단: exit_plan 없음 (MISSING_EXIT_PLAN)"
            )
        elif rc == _KIS_PAPER_ERROR:
            out["errors"] += 1
            msg = str(o.get("reason_message") or "")[:120]
            failures.append(f"{o.get('symbol', '?')} 자동주문 오류: {msg}")
            if _is_rate_limit_message(msg):
                out["rate_limit_hit"] = True

    # skipped 종목 — HOLD_NO_SIGNAL 은 ai_hold_signals 카운트, 나머지는 정보용.
    for s in skipped:
        rc = str(s.get("reason_code", "")).upper()
        if rc == "HOLD_NO_SIGNAL":
            out["ai_hold_signals"] += 1
        # 데이터 부재 / stale 은 풀-스캔이 종목별로 skip — 전체 loop 는 유지.
        # KIS_MARKET_DATA_UNAVAILABLE / KIS_PRICE_STALE / KIS_PRICE_INVALID 등.

    # scan-level reason — 전체 실패 시 1건 carry.
    scan_rc = str(scan.get("reason_code", "")).upper()
    scan_msg = str(scan.get("reason_message") or "")[:160]
    if scan_rc == _KIS_PAPER_ERROR:
        out["errors"] += 1
        failures.append(f"스캔 오류: {scan_msg}")
        if _is_rate_limit_message(scan_msg):
            out["rate_limit_hit"] = True
    elif scan_rc == "KIS_MARKET_DATA_UNAVAILABLE":
        # KIS read-only client 없음 — silent mock fallback 금지 → 명시 실패.
        out["errors"] += 1
        failures.append(scan_msg or "KIS 시세 client 없음 — 실시간 스캔 불가")

    out["failures"] = failures
    return out


def _emit_runtime_event(
    *,
    run_id: str,
    tick_idx: int,
    scan: dict[str, Any],
    tick_result: TickResult,
) -> None:
    """tick 결과를 RuntimeEvent 로 기록 — Settings > Log Viewer 노출.

    best-effort — 실패해도 루프 안 죽음. secret 원문 0건 (count / reason_code 만).
    무인 자동 실행 가시성을 위해 매 tick 한 줄씩 기록.
    """
    try:
        from app.system.event_log import log_event
        errors = int(tick_result.get("errors", 0) or 0)
        rate_limit = bool(tick_result.get("rate_limit_hit"))
        level = "ERROR" if rate_limit else ("WARN" if errors > 0 else "INFO")
        scan_rc = str(scan.get("reason_code") or "KIS_REALTIME_SCAN_DONE")
        msg = (
            f"real_tick_runner tick {tick_idx} ({run_id}): "
            f"scanned={tick_result.get('ai_decisions', 0)} "
            f"buy={tick_result.get('ai_buy_signals', 0)} "
            f"sell={tick_result.get('ai_sell_signals', 0)} "
            f"hold={tick_result.get('ai_hold_signals', 0)} "
            f"orders_exec={tick_result.get('orders_executed', 0)} "
            f"rejected={tick_result.get('orders_rejected', 0)} "
            f"errors={errors}"
        )
        log_event(
            level=level,
            category="PAPER",
            code=f"REAL_TICK_RUNNER_{scan_rc}",
            message=msg,
            details={
                "run_id":             run_id,
                "tick_idx":           tick_idx,
                "price_source":       "kis",
                "broker_order_type":  "KIS_PAPER",
                "broker_order_sent":  bool(scan.get("broker_order_sent")),
                "is_live_authorization": False,
                "symbols_scanned":    int(tick_result.get("ai_decisions", 0)),
                "orders_executed":    int(tick_result.get("orders_executed", 0)),
                "orders_rejected":    int(tick_result.get("orders_rejected", 0)),
                "orders_needs_approval":
                    int(tick_result.get("orders_needs_approval", 0)),
                "risk_blocks":        int(tick_result.get("risk_blocks", 0)),
                "errors":             errors,
                "rate_limit_hit":     rate_limit,
            },
        )
    except Exception:  # noqa: BLE001 — event 기록 실패는 루프 안 죽임.
        pass


def _persist_tick(
    *,
    persist_dir: pathlib.Path,
    run_id: str,
    tick_idx: int,
    scan: dict[str, Any],
    tick_result: TickResult,
) -> None:
    """tick 결과를 디스크에 즉시 기록 — best-effort, 실패해도 루프 안 죽음.

    secret 원문 0건 — V2 스캔 응답은 이미 boolean / count / reason_code 만 carry.
    파일 위치는 `reports/kis_paper_test/{run_id}_tick_{idx:04d}.json`.
    """
    try:
        persist_dir.mkdir(parents=True, exist_ok=True)
        path = persist_dir / f"{run_id}_tick_{tick_idx:04d}.json"
        payload = {
            "run_id":     run_id,
            "tick_idx":   tick_idx,
            "saved_at":   datetime.now(timezone.utc).isoformat(),
            "tick_result": tick_result,
            "scan_summary": {
                "reason_code":      scan.get("reason_code"),
                "reason_message":   scan.get("reason_message"),
                "mode":             scan.get("mode"),
                "price_source":     scan.get("price_source"),
                "dry_run":          scan.get("dry_run"),
                "smoke_mode":       scan.get("smoke_mode"),
                "symbols_scanned":  scan.get("symbols_scanned"),
                "candidates_found": scan.get("candidates_found"),
                "orders_attempted": scan.get("orders_attempted"),
                "orders_submitted": scan.get("orders_submitted"),
                "broker_order_type": scan.get("broker_order_type", "KIS_PAPER"),
                "is_live_authorization": False,
                "orders":           scan.get("orders", []),
                "skipped":          scan.get("skipped", []),
            },
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — persist 실패가 루프를 깨지 않음.
        _log.warning("[real-tick-runner] tick persist failed: %s: %s",
                     type(exc).__name__, exc)


def build_real_kis_paper_tick_runner(
    *,
    db: Any,
    broker: Any,
    risk: Any,
    settings: Any,
    credentials_present: bool,  # noqa: ARG001 — V2 스캔이 자체적으로 readiness 재평가
    force_dry_run: bool = False,
    persist_dir: pathlib.Path | None = None,
    scan_fn: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    run_id: str | None = None,
) -> tuple[TickRunner, Callable[[], None]]:
    """*real* 다종목 KIS Paper tick runner + cleanup 콜백.

    매 tick 마다 V2 스캔(`kis_paper_realtime_scan_tick`)을 1회 호출 — universe
    순회 / KIS read-only 실시세 / Agent Council / route_order 위임은 모두 V2
    가 담당. 본 wrapper 는 결과를 engine 의 TickResult dict 로 매핑 + 디스크
    persist + force_dry_run 적용만 한다.

    force_dry_run=True 면 `settings.kis_paper_auto_order_dry_run` 를 True 로
    덮어쓴 wrapper 를 V2 에 전달 → `auto_permission` 가 dry_run 으로 진입해
    broker 주문 호출 0건. .env 파일 미변경.

    scan_fn 은 테스트에서 V2 스캔을 fake 로 주입할 때 사용. None 이면 실제
    `kis_paper_realtime_scan_tick` 을 lazy import.
    """
    pdir = persist_dir or _DEFAULT_PERSIST_DIR
    rid = run_id or f"realrun-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    # force_dry_run 시 settings 래퍼 — kis_paper_auto_order_dry_run 만 True 로 덮어씀.
    # 다른 모든 속성은 원본으로 위임. (live_runner._ForceDryRunSettings 와 동일 정책.)
    if force_dry_run:
        class _ForceDryRunSettings:
            __slots__ = ("_base",)

            def __init__(self, base: Any):
                object.__setattr__(self, "_base", base)

            def __getattr__(self, name: str) -> Any:
                if name == "kis_paper_auto_order_dry_run":
                    return True
                return getattr(object.__getattribute__(self, "_base"), name)

        exec_settings: Any = _ForceDryRunSettings(settings)
    else:
        exec_settings = settings

    # session_factory: 본 runner 는 *주입된* db 세션을 매 tick 재사용한다.
    # V2 스캔은 자체 session_factory 를 받으므로, 매 tick 새 세션을 열지 않고
    # 동일 세션을 yield 하는 factory 를 만들어 격리.
    def _session_factory() -> Any:
        return db

    # KIS-PAPER-FULL-LIFECYCLE-V1 (B): 매 tick 종료 시 OrderAuditLog 에서 FillPoller
    # 가 *FILLED* 로 update 한 audit row 중 *이전 tick 이후 새로 본 것* 만 카운트해
    # tick_result["fills_observed"] 로 forward — engine 의 in-memory counter 와 DB
    # write 가 decouple 된 문제를 해결. 본 runner 가 *broker / route_order 를 호출하지
    # 않으며*, DB read-only SELECT 만 수행한다.
    _seen_filled_audit_ids: set[int] = set()

    def _count_new_fills() -> int:
        try:
            from sqlalchemy import select
            from app.db.models import OrderAuditLog
            rows = db.execute(
                select(OrderAuditLog.id).where(
                    OrderAuditLog.broker_status == "FILLED",
                    OrderAuditLog.executed.is_(True),
                )
            ).scalars().all()
            ids = {int(i) for i in rows if i is not None}
            new_ids = ids - _seen_filled_audit_ids
            _seen_filled_audit_ids.update(new_ids)
            return len(new_ids)
        except Exception:  # noqa: BLE001 — fill counter 가 죽어도 tick 자체는 진행.
            return 0

    async def runner(engine: Any, mode: Any, tick_idx: int) -> TickResult:
        # V2 스캔은 자체 try/except 로 종목별 격리 + scan-level except 처리.
        # 본 wrapper 는 매핑 + persist 만 담당.
        try:
            if scan_fn is None:
                # lazy import — engine 정적 가드(broker/route_order 미import) 회피.
                from app.kis_paper.driver_bridge import kis_paper_realtime_scan_tick
                fn = kis_paper_realtime_scan_tick
            else:
                fn = scan_fn

            scan = await fn(
                now=datetime.now(timezone.utc),
                session_factory=_session_factory,
                broker=broker,
                risk=risk,
                settings=exec_settings,
            )
        except Exception as exc:  # noqa: BLE001 — wrapper 가 죽지 않게.
            out = _zero_result()
            out["errors"] = 1
            msg = f"{type(exc).__name__}: {str(exc)[:120]}"
            out["failures"] = [f"V2 스캔 wrapper 오류: {msg}"]
            if _is_rate_limit_message(msg):
                out["rate_limit_hit"] = True
            try:
                _persist_tick(
                    persist_dir=pdir, run_id=rid, tick_idx=tick_idx,
                    scan={
                        "reason_code": _KIS_PAPER_ERROR,
                        "reason_message": msg,
                        "broker_order_type": "KIS_PAPER",
                        "is_live_authorization": False,
                    },
                    tick_result=out,
                )
            except Exception:  # noqa: BLE001
                pass
            return out

        tick_result = _map_scan_to_tick_result(scan)
        # KIS-PAPER-FULL-LIFECYCLE-V1 (B): FillPoller 가 audit row 를 FILLED 로 update
        # 한 *새로운* 건수를 counter 로 forward (engine.fills_observed 와 동기화).
        tick_result["fills_observed"] = int(tick_result.get("fills_observed", 0) or 0) \
            + _count_new_fills()
        _persist_tick(persist_dir=pdir, run_id=rid, tick_idx=tick_idx,
                      scan=scan, tick_result=tick_result)
        _emit_runtime_event(run_id=rid, tick_idx=tick_idx,
                            scan=scan, tick_result=tick_result)
        return tick_result

    def cleanup() -> None:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass

    return runner, cleanup


__all__ = [
    "build_real_kis_paper_tick_runner",
    "should_use_real_tick_runner",
]
