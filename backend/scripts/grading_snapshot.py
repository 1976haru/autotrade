"""화요일 무인 운영 자동 채점 스냅샷 — 측정·기록 *전용* (수정 0건).

Windows Task Scheduler 가 장중 1시간 간격 + 마감 후 호출한다. 매 호출마다:
  - 오늘(KST) 로그의 EGW00201 / EGW00133 누적
  - order_audit_log 오늘치: 총 / broker_order_id 발급 / 체결 / REJECTED
를 reports/grading_<date>.md 에 한 줄 append + 간단 규칙기반 verdict.

★절대 원칙: DB read-only. limiter·설정·성향·안전플래그 *일절 변경 안 함*.
  (config/.env/주문경로/안전플래그 import 0건 — 순수 조회.)
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# backend 디렉토리에서 실행 가정(schtasks working dir). app 모듈 import 가능하게.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

KST = timezone(timedelta(hours=9))


def _today_kst() -> datetime:
    return datetime.now(KST)


def _count_egw(log_path: Path, code: str, line_prefix: str | None = None) -> int:
    """code 출현 줄 수. line_prefix(예: '2026-06-10') 주어지면 그 날짜 줄만(자정 무회전
    로그에서 오늘분만 누적). 멀티라인 traceback 의 EGW 도 같은 줄에 code 가 있으면 카운트."""
    if not log_path.exists():
        return -1  # 로그 없음 표시
    n = 0
    try:
        with log_path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if code not in line:
                    continue
                if line_prefix and not line.startswith(line_prefix):
                    continue
                n += 1
    except OSError:
        return -1
    return n


def _order_stats(now_kst: datetime) -> dict:
    """order_audit_log 오늘(KST) 통계 — read-only."""
    from app.db.session import SessionLocal
    from app.db.models import OrderAuditLog
    # 오늘 KST 00:00 → UTC naive
    kst_midnight = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    lo = kst_midnight.astimezone(timezone.utc).replace(tzinfo=None)
    s = SessionLocal()
    try:
        rows = s.query(OrderAuditLog).filter(OrderAuditLog.created_at >= lo).all()
        total = len(rows)
        bno = sum(1 for r in rows if (getattr(r, "broker_order_id", None) or "").strip())
        filled = sum(1 for r in rows if (getattr(r, "filled_quantity", 0) or 0) > 0)
        bstat_rej = sum(1 for r in rows if getattr(r, "broker_status", None) == "REJECTED")
        approved = sum(1 for r in rows if r.decision == "APPROVED")
        rejected = sum(1 for r in rows if r.decision == "REJECTED")
        return {
            "total": total, "broker_order_id": bno, "filled": filled,
            "broker_rejected": bstat_rej, "approved": approved, "rejected": rejected,
        }
    finally:
        s.close()


def _verdict(egw201: int, bno: int, filled: int) -> str:
    # 규칙기반(자동 수정 아님 — 표시용 판정만).
    if egw201 < 0:
        return "로그없음"
    if egw201 <= 50 and bno > 0:
        return "PASS (한도 충분 + 주문 접수됨)"
    if egw201 <= 50 and bno == 0:
        return "주의 (EGW 낮으나 주문 접수 0 — 신호/윈도 확인)"
    if egw201 > 50:
        return f"미흡 (EGW {egw201} — limiter 추가 하향 검토)"
    return "확인필요"


def main() -> None:
    now = _today_kst()
    date_str = now.strftime("%Y%m%d")
    appdata = os.environ.get("APPDATA") or str(Path.home())
    # D(2026-06-10): 백엔드는 기동 시 연 로그 파일에 *회전 없이* 계속 기록한다(자정
    #   넘어가도 backend-<기동일>.log). 따라서 backend-<오늘>.log 가 아니라 logs/ 의
    #   *가장 최근 수정* backend-*.log 를 읽어 오늘 누적 EGW 를 본다.
    logs_dir = Path(appdata) / "Autotrade" / "logs"
    cands = sorted(logs_dir.glob("backend-*.log"),
                   key=lambda p: p.stat().st_mtime, reverse=True) if logs_dir.exists() else []
    log_path = cands[0] if cands else (logs_dir / f"backend-{date_str}.log")
    # 오늘(KST) 타임스탬프 줄만 세어 누적(다른 날 로그 혼입 방지).
    today_prefix = now.strftime("%Y-%m-%d")
    egw201 = _count_egw(log_path, "EGW00201", line_prefix=today_prefix)
    egw133 = _count_egw(log_path, "EGW00133", line_prefix=today_prefix)
    try:
        st = _order_stats(now)
    except Exception as exc:  # noqa: BLE001 — 측정 실패해도 기록만, 절대 raise/수정 금지
        st = {"error": str(exc)[:80]}

    report_dir = _BACKEND / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"grading_{date_str}.md"
    new = not report.exists()
    bno = st.get("broker_order_id", 0)
    verdict = _verdict(egw201, bno, st.get("filled", 0))
    with report.open("a", encoding="utf-8") as f:
        if new:
            f.write(f"# 무인 운영 자동 채점 — {now.strftime('%Y-%m-%d')} (KST)\n\n")
            f.write("> 측정 전용 · 자동 수정 0건. limiter 0.91/s + cache 20s 운영 상태.\n\n")
            f.write("| 시각(KST) | EGW201 | EGW133 | 주문row | 접수(bno) | 체결 | brk거부 | APPR | REJ | 판정 |\n")
            f.write("|---|---|---|---|---|---|---|---|---|---|\n")
        if "error" in st:
            f.write(f"| {now.strftime('%H:%M')} | {egw201} | {egw133} | DB오류: {st['error']} |\n")
        else:
            f.write(f"| {now.strftime('%H:%M')} | {egw201} | {egw133} | {st['total']} | "
                    f"{bno} | {st['filled']} | {st['broker_rejected']} | {st['approved']} | "
                    f"{st['rejected']} | {verdict} |\n")
    print(f"[grading] {now.strftime('%H:%M')} EGW201={egw201} bno={bno} filled={st.get('filled')} -> {report}")


if __name__ == "__main__":
    main()
