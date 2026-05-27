"""연구용(research-only) 네임스페이스.

본 패키지의 어떤 것도 런타임 주문 경로 / 실시간 전략 registry / broker / OrderExecutor /
route_order 와 연결되지 않는다. 백테스트·가설 검증 전용. 여기 정의된 전략 후보는
*절대* 실시간 전략으로 등록되거나 자동 적용되지 않는다 (`is_research_only=True`).
"""
