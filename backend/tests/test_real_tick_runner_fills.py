"""KIS-PAPER-FULL-LIFECYCLE-V1 (B): real_tick_runner 의 fills_observed forward 검증.

대상: ``build_real_kis_paper_tick_runner``. FillPoller 가 OrderAuditLog 의 broker_status
를 FILLED 로 update 하면, 다음 tick 에서 engine 의 in-memory counter 로 forward 되어
fills_observed 가 0 에서 증가해야 한다.

안전 invariants:
- DB read-only SELECT 만 — write 0건.
- broker / route_order / place_order 호출 0건.
- 이전 tick 에서 본 FILLED row 는 다시 카운트하지 않음(dedup, set 기반).
- DB 예외 발생 시 fill counter 만 0 으로 떨어지고 tick 자체는 진행.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.kis_paper.real_tick_runner import build_real_kis_paper_tick_runner


def _empty_scan_factory():
    async def _scan(*, now=None, session_factory=None, broker=None, risk=None, settings=None):
        return {
            "now":               (now or datetime.now(timezone.utc)).isoformat(),
            "symbols_scanned":   3,
            "orders_attempted":  0,
            "orders":            [],
            "skipped":           [],
            "reason_code":       "OK",
            "reason_message":    "",
            "broker_order_type": "KIS_PAPER",
            "is_live_authorization": False,
        }
    return _scan


class _FakeDB:
    """rows: list of (id, broker_status, executed)."""

    def __init__(self, rows):
        self.snapshot = list(rows)
        self.write_calls = 0   # invariant: must stay 0

    def execute(self, _stmt):
        ids = [r[0] for r in self.snapshot
               if r[1] == "FILLED" and r[2] is True]

        class _Scalars:
            def __init__(self, vals): self._vals = vals
            def all(self): return list(self._vals)

        class _R:
            def __init__(self, vals): self._vals = vals
            def scalars(self): return _Scalars(self._vals)

        return _R(ids)

    def commit(self):
        self.write_calls += 1

    def close(self): pass


def _build(rows):
    db = _FakeDB(rows)
    runner, cleanup = build_real_kis_paper_tick_runner(
        db=db, broker=object(), risk=object(), settings=object(),
        credentials_present=True, force_dry_run=True,
        scan_fn=_empty_scan_factory(), run_id="test-fills",
    )
    return runner, cleanup, db


def _run(runner, idx):
    return asyncio.run(runner(engine=object(), mode=object(), tick_idx=idx))


def test_fills_observed_increments_when_audit_row_filled():
    runner, cleanup, db = _build(
        rows=[(1, "RECEIVED", True), (2, "RECEIVED", True)],
    )
    try:
        # tick 0: 아직 FILLED 0건 → counter 0
        r0 = _run(runner, 0)
        assert r0["fills_observed"] == 0

        # FillPoller 시뮬: id=1 이 FILLED 로 전환
        db.snapshot[0] = (1, "FILLED", True)
        r1 = _run(runner, 1)
        assert r1["fills_observed"] == 1     # 새로 본 fill 1건

        # tick 2: 같은 row 다시 안 셈 (dedup)
        r2 = _run(runner, 2)
        assert r2["fills_observed"] == 0

        # 추가 fill (id=2)
        db.snapshot[1] = (2, "FILLED", True)
        r3 = _run(runner, 3)
        assert r3["fills_observed"] == 1

        # 안전: fill counter 가 DB write 를 발생시키지 않음(read-only).
        assert db.write_calls == 0
    finally:
        cleanup()


def test_fills_observed_zero_when_no_fills():
    runner, cleanup, _ = _build(rows=[])
    try:
        r = _run(runner, 0)
        assert r["fills_observed"] == 0
    finally:
        cleanup()


def test_fills_counter_resilient_to_db_exception():
    """DB SELECT 가 raise → fills_observed=0, tick 은 정상 종료."""

    class _BoomDB:
        def execute(self, *_a, **_k):
            raise RuntimeError("simulated DB down")
        def close(self): pass

    runner, cleanup = build_real_kis_paper_tick_runner(
        db=_BoomDB(), broker=object(), risk=object(), settings=object(),
        credentials_present=True, force_dry_run=True,
        scan_fn=_empty_scan_factory(), run_id="test-fills-boom",
    )
    try:
        r = _run(runner, 0)
        # tick 은 진행, fills=0 (DB 예외는 흡수)
        assert r["fills_observed"] == 0
        assert r["errors"] == 0          # tick 자체 에러 아님
    finally:
        cleanup()


def test_only_filled_rows_counted_not_received_partial_rejected():
    """broker_status != FILLED 인 row 는 카운트되지 않음."""
    runner, cleanup, _db = _build(
        rows=[(1, "RECEIVED", True), (2, "PARTIALLY_FILLED", True),
              (3, "REJECTED", True), (4, "FILLED", True)],
    )
    try:
        r = _run(runner, 0)
        assert r["fills_observed"] == 1  # id=4 만
    finally:
        cleanup()
