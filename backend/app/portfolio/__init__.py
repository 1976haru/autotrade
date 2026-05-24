"""Portfolio display-consistency layer (#55 / 7-03).

현금 / 총자산 / 포지션 값을 *어디서 왔는지(source)* 와 *조회 상태(status)* 를 함께
표시해, API 실패를 0원으로 오해하지 않도록 통일한다. 본 패키지는 read-only —
broker / OrderExecutor / route_order 호출 0건, 주문 생성 0건.
"""
