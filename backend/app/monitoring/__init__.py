"""체크리스트 #70: Monitoring — 서버 / API / 데이터 / 주문 안정성 read-only 집계.

CLAUDE.md 절대 원칙:
- 본 패키지는 broker / OrderExecutor / route_order / live order API를
  *호출하지 않는다*. 정적 grep 가드로 강제.
- DB는 read-only SELECT만 — INSERT/UPDATE/DELETE 0건.
- 외부 알림은 `NotificationService` 가 있을 때만 호출하고, 실패해도 시스템
  중단 금지 — 본 패키지는 `notify_alerts(service, alerts)` helper로 위임만.
- 주문 / 승인 / 리스크 상태를 직접 변경하지 않는다. *수집*만.

수익률 모니터링보다 시스템 안정성을 우선한다 — `MetricStatus`는
OK / WARN / CRITICAL / UNKNOWN 네 단계.
"""
