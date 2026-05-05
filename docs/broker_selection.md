# Broker Selection

본 프로젝트가 어떤 브로커를 어떻게 연결하는지 정리. 실제 코드 위치와 활성화 상태를 함께 명시한다.

## 현재 상태

| 어댑터 | 위치 | 코드 상태 | 활성화 조건 |
|---|---|---|---|
| `MockBrokerAdapter` | `app/brokers/mock_broker.py` | ✓ 모든 메서드 구현 (메모리 시세/체결 시뮬) | `DEFAULT_MODE in {SIMULATION, PAPER 외, LIVE_MANUAL_APPROVAL, LIVE_AI_*}` (KIS 외) |
| `KisBrokerAdapter` (SHADOW + PAPER) | `app/brokers/kis.py` + `kis_client.py` | ✓ 4개 read 경로 + place_order(paper) | `DEFAULT_MODE in {LIVE_SHADOW, PAPER}` + `KIS_IS_PAPER=true` |
| `KisBrokerAdapter` (LIVE) | (same) | 🛑 `place_order(is_paper=False)` `NotImplementedError`, `cancel_order` stub | `LIVE_MANUAL_APPROVAL` 라우팅 PR 후 |
| Kiwoom REST | — | 🛑 미구현 | KIS 단계 안정화 후 |
| `MockFuturesBroker` | `app/futures/mock.py` | 🛑 모든 메서드 `NotImplementedError` | `ENABLE_FUTURES_LIVE_TRADING=true` (기본 false) + `FuturesRiskManager` 평가 구현 |

`get_broker()` 팩토리(`app/api/deps.py`)는 운용모드를 보고 위 어댑터 중 하나를 반환한다. 자세한 분기는 [`architecture.md`](architecture.md).

## 비교 매트릭스

| 항목 | MockBroker | KIS Open API | Kiwoom REST API | (선물) Futures |
|---|---|---|---|---|
| 실제 시세 | ✗ | ✓ | ✓ | (별도 평가 후) |
| 모의투자 환경 | (전체 모의) | ✓ (`openapivts.koreainvestment.com:29443`) | △ (확인 필요) | ✗ |
| 주문 종류 | 시장가 BUY/SELL | 시장가 + 지정가 (`order-cash`) | (확인 필요) | LONG/SHORT 신규/청산 |
| 토큰 발급 | 불필요 | OAuth 24시간, 자동 갱신 (`KisClient`) | OAuth 별도 | 별도 |
| 호출 제한 | 없음 | endpoint별 RPM ([`api_limits.md`](api_limits.md)) | (확인 필요) | (별도) |
| 체결 통보 | 즉시 (FILLED) | 비동기 (RECEIVED → 폴링 필요) | 비동기 | 비동기 |
| 우선순위 | MVP 즉시 | MVP-1 (현재) | Phase 2 | Phase 8+ |

## 단계별 결정

### MVP (현재)
- **MockBroker** — SIMULATION/모든 LIVE 모드의 fallback. 테스트 인프라.
- **KIS (SHADOW + PAPER)** — 실 시세/잔고 + KIS 모의 주문. 운영자 가이드: [`shadow_mode.md`](shadow_mode.md), [`paper_mode.md`](paper_mode.md).

### MVP-2 (다음 단계)
- **KIS LIVE** — `LIVE_MANUAL_APPROVAL` 모드에서 `KisBrokerAdapter.place_order(is_paper=False)` + `cancel_order` 구현. PermissionGate 큐 통과 후에만 broker 도달.

### Phase 2
- **Kiwoom REST API** — KIS와 같은 BrokerAdapter 인터페이스 구현. `get_broker()` 분기에 `KIS_OR_KIWOOM` 같은 설정 추가.
- 신규 어댑터 추가 시 본 문서 + `promotion_policy.md`/`api_limits.md` 동기 업데이트.

### Phase 8+
- **선물 어댑터** — 주식 MVP가 LIVE_MANUAL_APPROVAL로 안정화된 후. `FuturesBrokerAdapter` 별도 ABC, `FuturesRiskManager`로 가드, `ENABLE_FUTURES_LIVE_TRADING` 명시 옵트인.

## 새 어댑터 추가 체크리스트

새 브로커를 통합할 때 PR이 만족해야 하는 것:

1. `BrokerAdapter` ABC를 구현 — 6개 메서드 모두 (placeholder도 OK).
2. **place_order는 가장 마지막에 구현** — read 경로(quote/balance/positions)가 안정화된 다음.
3. `KisClient`처럼 별도 `XxxClient` 클래스로 HTTP/SDK 래핑 (테스트는 transport mock).
4. `get_broker()` 팩토리 분기 추가, 환경 플래그로 활성화.
5. 테스트:
   - 모킹된 transport로 단위 테스트
   - read/write tr_id 분리 검증
   - 실패/429 에러 경로
6. 운영자 가이드 문서 (`xxx_mode.md`) — `shadow_mode.md` 패턴.
7. `promotion_policy.md`의 환경 플래그 매트릭스 갱신.
8. `api_limits.md`의 endpoint 매트릭스 갱신.
9. 본 문서(`broker_selection.md`) 갱신.

## 안전 원칙

- 실주문 코드(특히 LIVE) 작성 전 SHADOW + PAPER 단계 안정화 필수.
- 다층 가드(adapter is_paper, factory refusal, route_order, RiskManager)는 어댑터별로 독립 적용.
- 모든 broker 호출은 `route_order` → `OrderExecutor` 단일 경로를 통과하므로 audit이 자동 보장됨.

## 참고 링크

- KIS Developers: <https://apiportal.koreainvestment.com/>
- KIS open-trading-api: <https://github.com/koreainvestment/open-trading-api>
- Kiwoom REST: <https://openapi.kiwoom.com/>

## 관련 문서

- [`architecture.md`](architecture.md) — 가드 체인 + 라우트 surface
- [`shadow_mode.md`](shadow_mode.md), [`paper_mode.md`](paper_mode.md) — KIS 운영자 가이드
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 + 환경 플래그
- [`api_limits.md`](api_limits.md) — 호출 제한 정책
