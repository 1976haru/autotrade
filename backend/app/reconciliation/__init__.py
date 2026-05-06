"""Position reconciliation (212, MUST).

운영 안전성 직결: broker가 인식한 포지션과 백엔드 audit log에서 산출한
포지션이 일치하는지 비교한다. LIVE 활성화 시 broker가 별도 시스템(KIS)이
되므로 두 view 사이의 drift가 사고로 이어질 수 있다 — backlog #2 (운영
안전성 직결, LIVE 활성화 직전 필요).

CLAUDE.md 준수:
- 새 broker 호출 가드 우회 0건. broker.get_positions()만 read.
- 새 RiskManager / PermissionGate 분기 0건.
- 새 주문 경로 0건. 본 모듈은 read-only 비교만 한다.

운영 동선: 운영자가 `/api/reconciliation/status`를 호출하거나, frontend
Dashboard 카드가 주기적으로 조회. drift_detected이면 운영자가 수동으로
원인 조사 (broker UI 비교 / audit log 점검).
"""
