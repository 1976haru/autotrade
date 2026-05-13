"""체크리스트 #79: Loss Tagging — 손실 원인 *추정* 분석.

CLAUDE.md 절대 원칙:
- 본 패키지는 broker / OrderExecutor / route_order 호출 0건.
- 본 패키지의 결과는 *추정값*이며 확정 원인이 아니다. 운영자 검토 권장.
- 태그는 주문 차단 / 실행 트리거로 사용 금지 (advisory only).
"""
