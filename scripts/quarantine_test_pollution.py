"""운영 DB 의 *과거 테스트 오염 행* 식별 · 백업 · 격리 목록 작성 (기본 read-only).

배경 (KIS-PAPER-EOD V2/V3, 2026-10-23): 테스트 격리(conftest hermetic) 이전에는
tests/test_post_trade_review_agent.py, tests/test_sell_reason_logging.py 의 db_session
fixture 가 운영 SessionLocal(backend/data/auto_trader.db)에 episode 를 commit 했다.
본 스크립트는 그 행들을 *테스트 고정 페이로드 서명* 으로만 식별한다(추정 매칭 없음).

기본 동작 (운영 DB 변경 0):
  1) SQLite online backup → backend/data/quarantine/auto_trader.db.bak-<UTC타임스탬프>
  2) 서명 일치 행 목록 → backend/data/quarantine/test_pollution_manifest-<ts>.csv
  3) 요약 출력 (서명별 건수, id 범위, 기간)

삭제는 운영자 명시 승인 시에만:
  python scripts/quarantine_test_pollution.py --delete --i-approve-delete <manifest.csv>
  → manifest 의 id 중 *지금도 서명이 일치하는* 행만 삭제 (백업 선행 필수).

서명 (테스트 코드의 고정 인자):
  S1 test_attach_review_and_surface : BUY 005930, council={"selected_strategies": ["MOMENTUM", "VWAP"]}
  S2 test_review_episode_api        : BUY 005930, council=null,
                                       outcome={"status": "COMPLETE", "label": "PROFITABLE", "return_close": 1.2}
  S3 test_episode_records_and_...   : SELL 005930, kis_order_result={"submitted": true, "sell_reason_code": "VWAP_BREAKDOWN"}
공통: market_snapshot/votes 미기록(null), audit_id 없음 — 실제 봇 episode 와 구분되는 조건.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "backend" / "data" / "auto_trader.db"
OUT = ROOT / "backend" / "data" / "quarantine"

_COMMON = ("symbol = '005930' AND (market_snapshot IS NULL OR market_snapshot = 'null') "
           "AND (votes IS NULL OR votes = 'null') AND audit_id IS NULL")
SIGNATURES = {
    "S1_post_trade_review_attach": (
        "final_action = 'BUY' AND council = '{\"selected_strategies\": [\"MOMENTUM\", \"VWAP\"]}'"),
    "S2_post_trade_review_api": (
        "final_action = 'BUY' AND (council IS NULL OR council = 'null') AND "
        "outcome = '{\"status\": \"COMPLETE\", \"label\": \"PROFITABLE\", \"return_close\": 1.2}'"),
    "S3_sell_reason_logging": (
        "final_action = 'SELL' AND "
        "kis_order_result = '{\"submitted\": true, \"sell_reason_code\": \"VWAP_BREAKDOWN\"}'"),
}


def _ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)


def find(con: sqlite3.Connection) -> list[tuple[str, int, str, str]]:
    out = []
    for name, cond in SIGNATURES.items():
        q = (f"SELECT id, created_at, episode_id FROM agent_decision_episode "
             f"WHERE {_COMMON} AND {cond} ORDER BY id")
        for rid, created, eid in con.execute(q):
            out.append((name, int(rid), str(created), str(eid)))
    return out


def backup(db: Path, dest: Path) -> None:
    src = _ro(db)
    dst = sqlite3.connect(str(dest))
    with dst:
        src.backup(dst)   # online backup — 실행 중 backend 와도 일관된 스냅샷
    dst.close()
    src.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--delete", type=Path, metavar="MANIFEST_CSV",
                    help="(승인 시에만) manifest 의 행 삭제")
    ap.add_argument("--i-approve-delete", action="store_true")
    a = ap.parse_args(argv)
    if not a.db.exists():
        print(f"DB not found: {a.db}")
        return 2
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    a.out.mkdir(parents=True, exist_ok=True)

    if a.delete is not None:
        if not a.i_approve_delete:
            print("delete requires --i-approve-delete (explicit operator approval)")
            return 3
        bak = a.out / f"auto_trader.db.bak-{ts}-predelete"
        backup(a.db, bak)
        ids = [int(r["id"]) for r in csv.DictReader(a.delete.open(encoding="utf-8"))]
        con = _ro(a.db)
        still = {rid for _, rid, _, _ in find(con)}
        con.close()
        target = sorted(set(ids) & still)
        con = sqlite3.connect(str(a.db))
        with con:
            con.executemany("DELETE FROM agent_decision_episode WHERE id = ?",
                            [(i,) for i in target])
        con.close()
        print(f"deleted {len(target)} rows (backup: {bak})")
        return 0

    bak = a.out / f"auto_trader.db.bak-{ts}"
    backup(a.db, bak)
    con = _ro(a.db)
    rows = find(con)
    con.close()
    manifest = a.out / f"test_pollution_manifest-{ts}.csv"
    with manifest.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["signature", "id", "created_at", "episode_id", "status"])
        for name, rid, created, eid in rows:
            w.writerow([name, rid, created, eid, "QUARANTINED_TEST_POLLUTION_NOT_DELETED"])
    by = {}
    for name, rid, created, _ in rows:
        by.setdefault(name, []).append((rid, created))
    print(f"backup   : {bak}")
    print(f"manifest : {manifest}")
    print(f"total    : {len(rows)} rows (live DB unchanged - nothing deleted)")
    for name, lst in by.items():
        print(f"  {name}: {len(lst)} rows, id {lst[0][0]}..{lst[-1][0]}, "
              f"{lst[0][1][:10]} ~ {lst[-1][1][:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
