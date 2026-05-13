# Integration Test 정책 (체크리스트 #66)

## 1. 목적

단위 테스트(#65)가 각 모듈의 boundary를 검증한다면, **통합 테스트는 그
모듈들이 서로 연결되어 정상 작동하는지** narrative 형태로 확인한다. 운영자
/감사가 "신호 한 건이 broker까지 어떻게 흘러가나"를 한 파일에서 trace로
읽을 수 있어야 한다.

핵심 파이프라인:

```
Signal (Strategy / AI / Manual)
    ↓
route_order(...)  (단일 진입점)
    ↓
OrderGuard.check  (duplicate / cooldown / pending)
    ↓
RiskManager.evaluate_order  (notional / position / loss / freshness 등 26+ 가드)
    ↓
분기:
  - REJECTED      → audit row 작성, broker 미호출
  - NEEDS_APPROVAL → PendingApproval 큐 등록, broker 미호출
  - APPROVED      → OrderExecutor.execute → broker.place_order → audit 갱신
    ↓
MockBroker fill → 포지션 / 잔고 업데이트
    ↓
OrderAuditLog (영구화)
```

## 2. 절대 원칙 (CLAUDE.md)

| 원칙 | 강제 |
|---|---|
| 실 broker live order 호출 0건 | 모든 통합 테스트는 `MockBrokerAdapter`만 사용. `conftest.py`의 `client` fixture가 backend `get_broker` 의존성을 MockBrokerAdapter로 override |
| 실 KIS / 키움 / Anthropic / Telegram API 호출 0건 | 외부 네트워크 0건. `test_order_flow.py`의 `test_invariant_no_outbound_network_during_pipeline`이 `socket.create_connection`을 monkeypatch로 막아 통합 흐름 실행 중 외부 호출 시도 시 즉시 실패 |
| LIVE flag default false | `enable_live_trading` / `enable_ai_execution` / `enable_futures_live_trading` 모두 default False. LIVE_MANUAL_APPROVAL 시나리오는 fixture에서 explicit하게 true 설정 (테스트 안에서만) |
| API Key / Secret / 계좌번호 변경 0건 | `Settings` default가 빈 문자열. 통합 테스트는 KIS / Anthropic / Telegram 키를 *읽지 않는 경로*만 거친다 (MockBroker / NoOpChannel / `_FakeAiClient`) |
| in-memory SQLite | `conftest.py`의 `client` fixture가 `sqlite://` (in-memory) 엔진을 매 테스트마다 새로 생성. 운영 DB에 영향 0건 |

## 3. 통합 테스트 매핑

| 파일 | 시나리오 | 테스트 수 |
|---|---|---|
| `tests/test_order_flow.py` (#66 신규) | signal → risk → order → fill → position 단일 파이프라인 narrative | **14** |
| `tests/test_e2e_approval_order_flow.py` | 결재 큐 흐름 (제출 / 승인 / 거부 / 취소 / re-eval) | 8 |
| `tests/test_virtual_flow_e2e.py` | VirtualOrder 라이프사이클 + AI 가상 실행 | 10 |
| `tests/test_all_guards_integration.py` | RiskManager 26+ 가드 조합 시나리오 | 14 |
| `tests/test_auto_trader_e2e.py` | #60 AutoTrader (전략 신호 → mix → 가상 체결) | 18 |
| **통합 합계** | — | **64** |

## 4. `test_order_flow.py` 시나리오 (#66 메인 산출물)

| # | 시나리오 | 검증 단계 |
|---|---|---|
| 1 | SIMULATION BUY → MockBroker FILLED → position + cash 갱신 | signal → risk APPROVED → broker → audit → position |
| 2 | BUY → SELL round trip → 포지션 청산 + cash 복귀 + 2 audit | 두 사이클 연속 흐름 |
| 3 | RiskManager REJECTED (max_order_notional 초과) → broker 미호출 | REJECTED 분기에서 broker 호출 0건, audit row는 REJECTED |
| 4 | LIVE_MANUAL_APPROVAL → NEEDS_APPROVAL → 운영자 승인 → 체결 | PermissionGate.submit → approve → broker 호출 |
| 5 | LIVE_MANUAL_APPROVAL → 운영자 거부 → broker 미호출 | reject 분기 |
| 6 | Emergency stop ON → 모든 신규 BUY REJECTED | hard short-circuit + broker 미호출 |
| 7 | Python 레벨 `route_order(...)` 직접 호출 BUY + SELL trace | HTTP 우회 — 함수 호출 한 번으로 파이프라인 완결 |
| 8 | Strategy → LiveStrategyEngine.submit_tick → MockBroker | 전략 신호 origin 흐름 |
| 9 | 동일 client_order_id 재시도 → `DuplicateOrderError` | idempotency invariant |
| 10 | broker = MockBrokerAdapter invariant | 통합 테스트는 실 broker 인스턴스 사용 0건 |
| 11 | enable_live_trading default false | LIVE flag default 안전 |
| 12 | test DB = in-memory SQLite | 운영 DB 영향 0건 |
| 13 | Settings key 필드는 string type only | 통합 흐름이 key 값에 의존하지 않음 |
| 14 | 외부 네트워크 차단 시에도 파이프라인 정상 작동 | invariant lock — socket.create_connection 차단 후 정상 흐름 PASS |

## 5. MockBroker 사용 방식

- `conftest.py`의 `client` fixture가 매 테스트마다 *새 인스턴스*의
  `MockBrokerAdapter`를 생성하고 backend `get_broker` 의존성으로 override
- `MockBrokerAdapter`는 in-memory dict로 cash / positions / orders를 관리 —
  외부 호출 0건
- 가격은 기본 5개 종목(`005930`, `000660`, `035420`, `035720`, `005380`)에
  preset. 테스트가 다른 가격을 원하면 `broker.set_price(symbol, price)`로
  변경 가능
- `MockBrokerAdapter.place_order`는 즉시 `OrderResult(status=FILLED)` 반환 —
  슬리피지 / 부분 체결 시뮬은 `app/virtual/fill_engine.py`에서 처리 (별도 테스트)

## 6. fixture / monkeypatch 정책

| 대상 | override 방식 | 위치 |
|---|---|---|
| `get_broker` | MockBrokerAdapter 인스턴스 | `conftest.py` |
| `get_risk_manager` | 기본 `RiskPolicy()` (한도 default) | `conftest.py` |
| `get_market_data` | `MockMarketData` | `conftest.py` |
| `get_ai_client` | `_FakeAiClient` (deterministic 응답) | `conftest.py` |
| `get_db` | in-memory SQLite session | `conftest.py` |
| `default_mode` (per-test) | `monkeypatch.setattr(get_settings(), ...)` | 각 테스트 |
| `enable_live_trading` (per-test) | `client.test_risk_manager.policy.enable_live_trading = True` | 각 테스트 |

## 7. 외부 네트워크 차단 invariant

`test_invariant_no_outbound_network_during_pipeline`이 통합 흐름 실행 중
`socket.create_connection`을 monkeypatch로 막는다. 이는 *어떤 통합 테스트도
의도치 않게 KIS / Telegram / Anthropic / HTTP API를 호출하지 않는다*는 사실을
런타임에 lock하는 방어선이다.

## 8. CI / 실행 명령

```bash
# 통합 테스트만 빠르게
cd backend
pytest tests/test_order_flow.py -q                    # 본 PR 메인 산출물 14건
pytest tests/test_*_e2e.py tests/test_*_integration.py -q   # 모든 통합 테스트

# 전체
pytest -q

# 특정 시나리오 trace
pytest tests/test_order_flow.py::test_full_pipeline_simulation_buy_signal_to_filled_position -v -s
```

## 9. 남은 backlog

| 항목 | 현 상태 | 후속 |
|---|---|---|
| 실 KIS LIVE 통합 테스트 | `NotImplementedError` stub — 통합 테스트 없음 (의도적) | LIVE 활성화 PR에서 contract test 추가 — 별도 옵트인, real key 사용 0건 |
| 실 Anthropic LIVE 통합 | `_FakeAiClient` 모킹만 | 비용 발생 — manual / opt-in marker로 분리 |
| WebSocket 시세 수신 통합 | yfinance / KIS WebSocket 통합 미구현 | 시세 collector PR(#19) 위에 후속 |
| Multi-strategy 동시 신호 | `test_strategies_live_engine.py`에 일부 | 여러 strategy의 신호 confluence 통합 시나리오 후속 |
| 선물 통합 흐름 | `MockFuturesBroker` 단위 테스트만 | 선물 시뮬 통합 시나리오 후속 (LIVE는 별도 옵트인) |
| Telegram 알림 실 발송 통합 | `dry_run=True`로만 검증 | manual / opt-in marker, 실 키 사용 |

## 10. 절대 invariant (변경 금지)

1. `tests/test_order_flow.py`는 *MockBroker만* 사용한다 — `test_invariant_test_
   broker_is_mock_not_kis_live`로 lock.
2. 통합 테스트 실행 중 외부 네트워크 호출 0건 — `socket.create_connection`
   monkeypatch lock.
3. 모든 주문 경로는 `route_order` 단일 진입점을 통과 (CLAUDE.md 절대 원칙 2).
4. 통합 테스트는 운영 DB / 운영 `.env` / 운영 broker에 영향 0건. 매 테스트
   마다 새 in-memory DB + 새 MockBroker 인스턴스.
5. 실 API key (KIS / Anthropic / Telegram)는 *어떤 통합 테스트도 사용하지
   않는다*. 키 값이 .env에 우연히 있더라도 통합 흐름이 해당 코드 경로에
   도달하지 않음.

## 11. 관련 PR / 체크리스트

- #34 RiskManager 표준 진입점 (`route_order`)
- #38 OrderGuard
- #40 OrderExecutor 단일 진입점
- #44 LIVE_AI_ASSIST 흐름
- #60 AutoTrader E2E
- #65 P0 단위 테스트 (이 위에 본 통합 테스트가 쌓임)
- #66 Integration Tests (본 PR)
