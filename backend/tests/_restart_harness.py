"""격리된 *별도 프로세스* 에서 실제 FastAPI lifespan 을 시작/종료하는 하네스.

test_autopaper_restart_subprocess.py 가 `python tests/_restart_harness.py <step...>` 로 실행한다
(pytest 수집 대상 아님). 모든 상태는 HARNESS_DIR 의 임시 DB / 승인 파일 / APPDATA 에만 쓴다.
실제 KIS 호출 0건 — MARKET_DATA_PROVIDER=mock, background tick/fill polling OFF,
주문 사전검증 broker 는 호출 시 예외를 던지는 가짜.

steps (순서대로 실행, 마지막 줄에 JSON 결과 출력):
  approve            — 다음 serve 단계의 실행 중 백엔드 안에서 승인 생성 (파일 + DB)
  serve              — lifespan 시작 → POST /start → 정상 종료 (lifespan finally 실행)
  serve_emergency_fail — lifespan 시작 → /start → 저장소 고장 상태로 /emergency-stop → 정상 종료
  probe              — lifespan 시작 → 상태 조사 → 정상 종료
  probe_crash        — lifespan 시작 → 상태 조사 → os._exit (종료 처리 없음 = 크래시)
환경: HARNESS_DB_DOWN=1 이면 승인 DB 조회를 실패시킨다.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

HARNESS_DIR = Path(os.environ["HARNESS_DIR"])
os.environ.update({
    "DATABASE_URL": "sqlite:///" + (HARNESS_DIR / "harness.db").as_posix(),
    "AGENT_TRADER_CONFIG_DIR": str(HARNESS_DIR / "config"),
    "AUTOTRADE_PAPER_APPROVAL_FILE": str(HARNESS_DIR / "auth" / "paper-session-approval.json"),
    "DEFAULT_MODE": "SIMULATION", "MARKET_DATA_PROVIDER": "mock",
    "USE_REAL_TICK_RUNNER": "false", "PAPER_CAPITAL_PERSIST": "false",
    "ENABLE_LIVE_TRADING": "false", "ENABLE_AI_EXECUTION": "false",
    "ENABLE_FUTURES_LIVE_TRADING": "false", "KIS_IS_PAPER": "true",
    "ENABLE_AI_PAPER_BACKGROUND_TICK": "false", "ENABLE_FILL_POLLING": "false",
    "KIS_PAPER_FILL_POLLING": "false",
    "KIS_APP_KEY": "", "KIS_APP_SECRET": "", "KIS_ACCOUNT_NO": "1234567801",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import config as _cfg  # noqa: E402

_cfg.Settings.model_config["env_file"] = None
_cfg.get_settings.cache_clear()

import app.brokers.kis_client as _kc  # noqa: E402
import app.core.runtime_config as _rc  # noqa: E402

_appdata = HARNESS_DIR / "appdata" / "Autotrade"
_appdata.mkdir(parents=True, exist_ok=True)
_rc.overrides_path = lambda: _appdata / "runtime_overrides.json"
_rc._legacy_overrides_path = lambda: _appdata / "legacy_runtime_overrides.json"
_kc._token_cache_path = lambda: _appdata / "kis_token_cache.json"

from app.db.session import apply_migrations  # noqa: E402
from app.kis_paper import one_click_session as ocs  # noqa: E402

KST = ocs.KST


class _ReachedBroker(BaseException):
    """BaseException — authorize_order 의 `except Exception` 이 잡지 않아 승인 기록 무변경."""


class _NoCallBroker:
    def __getattr__(self, name):
        raise _ReachedBroker("REACHED_BROKER")


def _settings():
    from types import SimpleNamespace
    return SimpleNamespace(
        kis_is_paper=True, enable_live_trading=False, kis_account_no="1234567801",
        kis_product_code="01", kis_app_key="K", kis_app_secret="S",
        market_data_provider="kis", enable_ai_paper_background_tick=True,
        kis_paper_fill_polling=True, kis_paper_smoke_mode=False,
        krx_calendar_override_path="",
    )


def _target():
    from datetime import datetime, timezone
    from app.scheduler.intraday_session import next_trading_day
    return next_trading_day(datetime.now(timezone.utc))


def _approve(out):
    from types import SimpleNamespace
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        rec = ocs.create_approval(db=db, settings=_settings(),
                                  risk=SimpleNamespace(emergency_stop=False),
                                  target_date=_target().isoformat(), actor="harness",
                                  explicit_ack=True)
    finally:
        db.close()
    out["approved"] = rec["target_date"]


def _probe(client, out):
    from datetime import datetime, time
    from types import SimpleNamespace
    from app.db.session import SessionLocal
    out["loop_state"] = client.get("/api/auto-paper/status").json()["state"]
    out["approval"] = client.get("/api/auto-paper/paper-session-approval").json()
    db = SessionLocal()
    try:
        out["trading_authorized"] = ocs.session_trading_authorized(_settings(), db=db)
        out["exit_authorized"] = ocs.session_exit_authorized(_settings(), db=db)
        at = datetime.combine(_target(), time(10, 0), KST)
        try:
            ok, reason = asyncio.run(ocs.authorize_order(
                db=db, settings=_settings(), broker=_NoCallBroker(),
                risk=SimpleNamespace(emergency_stop=False), side="BUY", symbol="005930",
                quantity=1, notional_krw=10_000, now=at))
            out["buy"] = [ok, reason]
        except _ReachedBroker as exc:
            out["buy"] = [None, str(exc)]          # 차단 없이 broker 조회까지 진행
    finally:
        db.close()
    out["process_buy_block"] = ocs._PROCESS_EMERGENCY_BLOCK
    out["permit_exists"] = ocs.restore_permit_path().exists()


def main(steps):
    apply_migrations()
    if os.environ.get("HARNESS_DB_DOWN") == "1":
        def _down(*_a, **_k):
            raise RuntimeError("database is locked")
        ocs._db_record = _down
    from fastapi.testclient import TestClient
    from app.main import app
    out: dict = {}
    pending_approve = False
    for step in steps:
        if step == "approve":
            pending_approve = True          # 실제 앱처럼 *실행 중인* 백엔드 안에서 승인
        elif step in ("serve", "serve_emergency_fail"):
            with TestClient(app) as c:
                if pending_approve:
                    _approve(out)
                    pending_approve = False
                r = c.post("/api/auto-paper/start")
                out["start_state"] = r.json().get("state")
                if step == "serve_emergency_fail":
                    def _fail_write(_d):
                        raise OSError("disk full")

                    def _fail_db(_db, _r):
                        raise RuntimeError("db locked")
                    ocs._write_atomic = _fail_write
                    ocs._persist_approval = _fail_db
                    body = c.post("/api/auto-paper/emergency-stop").json()
                    out["emergency_persisted"] = body.get("session_block_persisted")
                    out["emergency_state"] = body.get("state")
            out["permit_after_shutdown"] = ocs.restore_permit_path().exists()
        elif step in ("probe", "probe_crash"):
            with TestClient(app) as c:
                _probe(c, out)
                if step == "probe_crash":
                    print(json.dumps(out, default=str), flush=True)
                    os._exit(0)                     # lifespan 종료 처리 없이 즉사
        else:
            raise SystemExit(f"unknown step {step}")
    print(json.dumps(out, default=str), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
