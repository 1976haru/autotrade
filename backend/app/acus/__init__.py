"""ACUS — Agent Council Universe Selection (advisory, read-only).

다중 에이전트 교차검증으로 한국 단타 5분봉 universe 에서 *모든 기준을 통과한*
ROBUST 종목 풀을 자동 추출하는 무인(unattended) 연구 파이프라인.

본 패키지는 분석 전용이다:
  - 실거래 주문 0건 (broker.place_order / route_order / OrderExecutor 호출 0건)
  - 안전 flag(ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / KIS_IS_PAPER) 변경 0건
  - 자동 Paper 진입 / 자동 매매 시작 0건
  - 결과는 운영자 검토 자료이며 실전 전환 승인이 아니다 (수익 보장 아님)

5개 에이전트가 각자 다른 기준으로 종목을 평가하고, 5중 교집합만 FINAL_ROBUST 로
추출한다(cherry-picking 금지 — 전체 universe 결과를 보고). 자세한 정책은
``docs/acus_pipeline.md`` 참조.
"""

from __future__ import annotations

__all__ = ["ACUS_VERSION"]

ACUS_VERSION = "ACUS-AGENT-COUNCIL-UNIVERSE-SELECTION-V1"
