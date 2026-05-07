"""Agent Operating System (223+).

지능형 Agent OS — 사용자가 스마트폰에서 시작/일시정지/긴급중단/요약확인만 해도
운용 루프가 돌도록. 모든 Agent 출력은 deterministic stub으로 동작 — AI Key 없이
mock output 보장. 실 LLM 통합은 별도 옵트인.

CLAUDE.md 절대 원칙:
- AI Agent는 broker 주문 API를 직접 호출하지 않는다.
- 모든 결정은 RiskManager + PermissionGate + Audit Log를 통과한다.
- VIRTUAL_AI_EXECUTION 외 LIVE 모드에서는 본 루프가 주문을 만들지 않는다.
"""
