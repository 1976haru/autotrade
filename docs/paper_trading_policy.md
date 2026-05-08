# Paper Trading Policy (#42)

> 코드: [`backend/app/execution/paper_trader.py`](../backend/app/execution/paper_trader.py)
> API: `GET /api/paper/status` ([`backend/app/api/routes_paper.py`](../backend/app/api/routes_paper.py))
> 테스트: [`backend/tests/test_paper_trader.py`](../backend/tests/test_paper_trader.py)
> Frontend: [`frontend/src/components/common/PaperModeStatusCard.jsx`](../frontend/src/components/common/PaperModeStatusCard.jsx)
> 관련 문서: [`docs/paper_mode.md`](paper_mode.md), [`docs/broker_selection.md`](broker_selection.md), [`docs/api_limits.md`](api_limits.md)

## 1. 목적

> **실제 시세 + 모의투자 주문으로 전략 오류를 실시간 환경에서 확인한다.**

LIVE 활성화 전 단계 — 실 시세 데이터 + 모의 broker 체결로 전략의 실시간 동작
/ 주문 흐름 / 체결 처리 로직을 검증한다. paper 단계가 충분히 안정화되어야
LIVE_MANUAL_APPROVAL → LIVE_AI_ASSIST → LIVE_AI_EXECUTION으로 점진 승격 가능
([`docs/promotion_policy.md`](promotion_policy.md)).

## 2. 지원 broker (`PaperBrokerKind`)

| Kind | 설명 |
|---|---|
| `MOCK` | `MockBrokerAdapter` — 즉시 가상 체결, 메모리 in-process |
| `KIS_PAPER` | `KisBrokerAdapter` (`is_paper=True`) — KIS 실제 API의 모의투자 환경 |

운영자 선택: `Settings.paper_broker_kind` (env `PAPER_BROKER_KIND`).
미설정 시 default 추론:
- `DEFAULT_MODE=PAPER` + `KIS_IS_PAPER=true` → `KIS_PAPER`
- 그 외 → `MOCK`

## 3. 표준 흐름

```
Strategy / AI / Manual
        │
        ▼
   route_order
        │
        ├─► OrderGuard.check (#38)
        ├─► RiskManager.check_order (#34)
        ├─► PermissionGate.submit (NEEDS_APPROVAL이면 큐)
        │
        ▼
   OrderExecutor.execute (#40)
        │
        ▼
   PaperTrader.assert_paper_broker(broker)  ◄── 인스턴스 단계 가드
        │
        ▼
   BrokerAdapter (Mock or KIS Paper)
```

PaperTrader는 *route_order를 대체하지 않는다*. 역할:
- broker 선택 (`make_paper_broker(kind)`)
- live 차단 (`assert_paper_broker(broker)`)
- 표준 흐름 wrapper (`PaperTrader.execute(order, audit)` → OrderExecutor 위임)
- read-only status (`build_paper_status()`)

## 4. 안전 원칙

본 PR에서 강제되는 invariant:

| 정책 | 코드 위치 | 위반 시 |
|---|---|---|
| `KIS_IS_PAPER=true` 강제 | `make_paper_broker(KIS_PAPER)`, `get_broker()` | `RuntimeError` 시작 거부 |
| `KisBrokerAdapter.place_order(is_paper=False)` 거부 | `app/brokers/kis.py:181` | `NotImplementedError` |
| `is_live_broker(broker)` 거부 | `assert_paper_broker(broker)` | `NotPaperBrokerError` |
| RiskManager 우회 진입점 0건 | PaperTrader.execute → OrderExecutor 위임 | `UnauthorizedOrderError` (audit 검증) |
| `ENABLE_LIVE_TRADING=false` (default) | `RiskPolicy` / `route_order` | LIVE 모드에서 REJECTED |
| `ENABLE_AI_EXECUTION=false` (default) | RiskManager + AI Permission Gate (#39) | AI 자동 실행 차단 |

다층 방어:
- Settings 레벨 (`KIS_IS_PAPER`, `ENABLE_LIVE_TRADING`)
- 모드 레벨 (DEFAULT_MODE 분기)
- broker 레벨 (`is_paper` flag, `place_order` 거부)
- runtime 레벨 (`assert_paper_broker`, `OrderExecutor` audit 검증)

## 5. MockBroker vs KIS Paper 차이

| 항목 | MockBroker | KIS Paper |
|---|---|---|
| 체결 시점 | 즉시 (호출 직후 FILLED) | KIS 서버 큐 + 시장 환경 매칭 |
| 시세 데이터 | mock 또는 yfinance | KIS 실시간 시세 |
| 슬리피지 모델 | 0 (또는 옵션) | 실제 호가 갭 영향 |
| 부분 체결 | 발생 X | 발생 가능 |
| 거절 / 잔고 부족 | 코드 단 가짜 검증 | 실제 broker 검증 |
| 호출 비용 | 0 | KIS API rate limit 적용 |

**모의투자 체결 품질은 실제와 다를 수 있다**:
- 체결 시간 / 슬리피지 / 부분체결 패턴이 실 시장과 차이.
- 모의투자 체결 로그가 실제 시장 체결을 *완전히* 대변하지 않음.
- LIVE 활성화 전 reconciliation 필수 (#212).

## 6. Rate limit (KIS Paper)

KIS Paper는 실제 KIS API 호출 — rate limit 적용:
- `KIS_RATE_LIMIT_CALLS=5`, `KIS_RATE_LIMIT_WINDOW_SECONDS=1.0` (default).
- **EGW00201** 에러 (호출 빈도 초과) 발생 가능.
- 호출 간격 권장 1.2초 이상.
- 자세한 정책: [`docs/api_limits.md`](api_limits.md).

## 7. Fill polling

`ENABLE_FILL_POLLING=false` (default). True 시 백그라운드 `FillPoller`가
`broker.get_order_status(order_id)` 폴링으로 체결 갱신.

- 폴링 간격: `FILL_POLLING_INTERVAL_SECONDS` (default 5초).
- 실패 시 audit row의 `message` 필드에 에러 trace 저장.
- KIS Paper는 폴링 호출에도 rate limit이 적용되므로 운영 시 주의.

## 8. 실거래 전 조건

LIVE 활성화 (`ENABLE_LIVE_TRADING=true`) 전 충족:

- [ ] **Paper 2~4주 운용** — 실제 시장 시간(09:00–15:30 KST) 동안 매일 운영.
- [ ] **주문 실패 케이스 확인** — 잔고 부족 / 종목 거래정지 / KIS API 에러를
      audit로 모두 surface.
- [ ] **부분 체결 케이스 확인** — KIS Paper에서 발생하는 부분 체결 처리
      로직 검증.
- [ ] **거절 케이스 확인** — RiskManager 거절 + KIS broker 거절 양쪽 audit
      일관성.
- [ ] **reconciliation 확인** — broker view ↔ audit view drift 0건 (#212).
- [ ] **Manual approval 유지** — LIVE_MANUAL_APPROVAL 모드로 모든 실 주문
      을 운영자 승인 큐로 라우팅 (#41 Manual Approval Policy 참고).
- [ ] **Loss limit / 한도 운영 정책 확정** (#36, #35).
- [ ] **Kill switch 운영 절차 확정** (#37).

## 9. UI 상태 표시

`/api/paper/status` 응답 → `PaperModeStatusCard` (StrategyRisk 탭):
- 현재 mode + paper 여부 배지
- `paper_broker_kind` (MOCK / KIS_PAPER) + label
- 4 안전 flag 시각화 (`kis_is_paper`, `enable_live_trading`,
  `enable_ai_execution`, `enable_futures_live_trading`,
  `fill_polling_enabled`)
- 모의투자 체결 품질 주의 안내 (warning 색)
- **주문 / test 버튼 부재** (테스트 가드 — 상태 표시 전용)

## 10. 향후 과제 (Paper Trading backlog)

- **Paper-only orchestration API** — 운영자가 명시 paper test 주문을 큐에
  등록하는 별도 흐름 (수동 승인 필수, 별도 옵트인 PR).
- **Paper 체결 품질 비교 리포트** — KIS Paper 체결가 vs 실제 시세의 슬리피지
  분포 분석.
- **Multi-broker switching** — runtime broker 변경 (현재는 Settings 기반 시작
  시 결정).
- **paper-specific scoreboard** — paper / live 결과 분리 표시.
- **bulk paper backfill** — 과거 신호를 paper로 재실행해 체결 시뮬.

## 11. 안전 invariant 확인 (테스트로 강제)

- `app/execution/paper_trader.py`에 `broker.place_order(` / `BrokerAdapter.
  place_order(` / `.place_order(` 호출 형태 0건 — OrderExecutor 단일 진입점.
- `from app.execution.order_router` import 0건 (circular avoid + bypass 방지).
- `PaperTrader.__init__` 시그니처에 `api_key` / `secret` 매개변수 0건.
- `routes_paper.py`는 read-only — `@router.post/.put/.delete/.patch` 핸들러 0건.
- 모든 테스트는 paper-safe broker 인스턴스만 사용 (실 KIS API 호출 0건).
- LIVE flag / API Key / Secret / 계좌번호 변경 0건.
