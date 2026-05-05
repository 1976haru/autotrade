# Risk Policy

CLAUDE.md "감사 로그를 우선한다" 원칙에 따라 모든 리스크 평가 결과는 `OrderAuditLog`에 기록된다. 본 문서는 코드(`app/risk/risk_manager.py`, `app/futures/risk.py`)와 동기화된 정책 명세.

## 주식 RiskPolicy 기본값

| 항목 | 코드 필드 | 기본값 | 운영자 env override |
|---|---|---:|---|
| 1회 주문 최대 명목 | `max_order_notional` | 1,000,000원 | `RISK_MAX_ORDER_NOTIONAL` |
| 일일 최대 손실 | `max_daily_loss` | 200,000원 | `RISK_MAX_DAILY_LOSS` |
| 최대 보유 종목 수 | `max_positions` | 5개 | `RISK_MAX_POSITIONS` |
| 종목별 최대 노출 | `max_symbol_exposure` | 1,500,000원 | `RISK_MAX_SYMBOL_EXPOSURE` |
| 실거래 허용 | `enable_live_trading` | `false` | `ENABLE_LIVE_TRADING` |
| AI 자동실행 허용 | `enable_ai_execution` | `false` | `ENABLE_AI_EXECUTION` |

값은 `app/risk/risk_manager.py::RiskPolicy` dataclass의 default. 런타임 `get_risk_manager()`는 `RiskPolicy.from_settings(get_settings())`로 env 값을 적용한다 — env를 비워두면 위 기본값 그대로. 향후 `/api/risk/policy` write 라우트(미구현)로 런타임 조정도 추가 예정.

## 평가 순서

`RiskManager.evaluate_order(order, mode, balance, positions, latest_price, requested_by_ai=False)`가 다음 순서대로 검사한다. **하나라도 reason을 추가하면 최종 decision = REJECTED** (단, NEEDS_APPROVAL early-return은 예외).

1. **Emergency Stop** — `risk.emergency_stop=True`면 reason 추가
2. **주문 명목 한도** — `latest_price * order.quantity > max_order_notional`이면 거부
3. **일일 손실 한도** — `daily_realized_pnl ≤ -max_daily_loss`이면 거부
4. **현금/포지션 가용성** — BUY인데 cash < notional이면 거부 (예비 체크 — 실 잔고는 broker 응답에서 확정)
5. **최대 보유 종목** — 신규 매수가 새로운 symbol을 추가하고 보유 종목 수 ≥ max_positions이면 거부
6. **종목별 노출 한도** — BUY인데 (현재 노출 + 추가 노출) > max_symbol_exposure이면 거부
7. **LIVE_SHADOW** — 모드가 LIVE_SHADOW이면 reason 추가 → 결과적으로 REJECTED
8. **NEEDS_APPROVAL early-return** — 모드가 LIVE_MANUAL_APPROVAL 또는 LIVE_AI_ASSIST이면 `decision=NEEDS_APPROVAL`로 즉시 반환 (이후 단계 평가 안 함)
9. **AI 실행 권한** — `requested_by_ai=True`이고 모드/플래그가 AI 실행 허용 안 하면 거부
10. **LIVE 가드** — 모드가 LIVE_*인데 `enable_live_trading=False`이면 거부

## 모드별 결정 매트릭스

`enable_live_trading`/`enable_ai_execution`이 모두 default(False)일 때 :

| 모드 | 일반 주문 | AI 주문(`requested_by_ai=True`) |
|---|---|---|
| `SIMULATION` | 한도 통과 시 APPROVED | 한도 통과 시 APPROVED (AI 가드 무관) |
| `PAPER` | 한도 통과 시 APPROVED | APPROVED (PAPER는 LIVE_*에 해당 안 함) |
| `LIVE_SHADOW` | **REJECTED** ("LIVE_SHADOW records signals only") | **REJECTED** |
| `LIVE_MANUAL_APPROVAL` | **NEEDS_APPROVAL** (early-return) | **NEEDS_APPROVAL** |
| `LIVE_AI_ASSIST` | **NEEDS_APPROVAL** | **NEEDS_APPROVAL** |
| `LIVE_AI_EXECUTION` | LIVE 가드에서 REJECTED | AI 가드에서 REJECTED |

`enable_live_trading=True`로 켜면 LIVE_AI_EXECUTION의 일반 주문은 APPROVED, AI 주문은 여전히 `enable_ai_execution=False`이므로 REJECTED.

## 운용모드와 안전 플래그

자세한 매트릭스는 [`promotion_policy.md`](promotion_policy.md) 마지막 표 참조.

| 환경변수 | 기본 | 적용 위치 |
|---|---|---|
| `DEFAULT_MODE` | `SIMULATION` | RiskManager mode-aware 분기 + `get_broker()` 라우팅 |
| `ENABLE_LIVE_TRADING` | `false` | RiskPolicy → step 10 (LIVE 가드) |
| `ENABLE_AI_EXECUTION` | `false` | RiskPolicy → step 9 (AI 가드) |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | FuturesRiskManager (선물 전용) |

## 긴급 정지

- API: `POST /api/risk/emergency-stop` body `{"enabled": true}`
- 프론트엔드: 전략·리스크 탭의 "긴급 정지" 버튼 (`useRiskPolicy` 훅)
- 효과: `RiskManager.emergency_stop = True`로 토글 → 모든 신규 주문이 step 1에서 reason 추가되어 REJECTED.
- 토글 이력: `EmergencyStopEvent` 테이블에 한 행씩 영구 저장 (id, created_at, enabled, decided_by, note). 같은 상태로 다시 토글하는 no-op은 라우트 레이어에서 걸러내어 노이즈 방지. `GET /api/risk/emergency-stop/history`로 조회.
- 런타임 플래그는 in-memory라 백엔드 재시작 시 OFF로 리셋되는 것이 의도된 설계 — 운영자가 재시작 후 의도적으로 다시 켜야 한다. 이력은 영구 저장되어 "누가 언제 어떤 사유로 토글했는지"가 재시작 후에도 유지된다.
- 해제: 같은 엔드포인트에 `{"enabled": false}` POST.

## 선물 RiskPolicy (Futures)

`app/futures/risk.py::FuturesRiskPolicy`. 주식보다 보수적 default.

| 항목 | 기본값 |
|---|---:|
| 최대 계약 수 | 1 |
| 최대 증거금 사용 | 1,000,000원 |
| 일일 최대 손실 | 200,000원 |
| 선물 실거래 허용 | `enable_futures_live_trading=false` |

현재 `FuturesRiskManager.evaluate_order`는:
- 플래그 OFF면 무조건 REJECTED ("ENABLE_FUTURES_LIVE_TRADING is disabled")
- 플래그 ON이면 `NotImplementedError` (실제 평가 로직은 별도 PR)

## 다층 안전 가드 (방어 심층)

RiskManager 외에도 코드 단에 두 층의 추가 가드가 있다 (자세한 내용은 [`architecture.md`](architecture.md)):

- **Adapter 단계 (KIS)** — `KisBrokerAdapter.place_order(is_paper=False)`는 `NotImplementedError`. `cancel_order`도 stub.
- **Factory 단계** — `get_broker()`가 `DEFAULT_MODE=PAPER` + `KIS_IS_PAPER=false`면 시작 거부.
- **Engine 단계** — `LiveStrategyEngine.submit_tick`이 거부 시 logical position state 롤백.

## 킬스위치 단계 (향후 작업)

현재 구현된 것은 emergency_stop 단일 플래그(주문 차단)뿐. 자동 단계적 킬스위치는 미구현.

| 레벨 | 의도 | 상태 |
|---|---|---|
| L1 | 신규 매수 중단 | emergency_stop으로 가능 |
| L2 | + 미체결 취소 | `cancel_order` LIVE 라우팅 후 가능 |
| L3 | + 청산 후보 표시 | 별도 PR (포지션 관리 모듈 필요) |
| L4 | + 자동 청산 | 명시적 옵트인 후에만 (CLAUDE.md "AI 직접 청산" 금지) |

## 변경 이력

이 문서는 코드와 동기화된다. RiskPolicy 필드 추가/수정, 평가 순서 변경, 모드 매트릭스 변동을 일으키는 PR은 본 문서도 같이 업데이트할 것.

## 관련 문서

- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 기준 + 환경 플래그 매트릭스
- [`architecture.md`](architecture.md) — 가드 체인 전체 구조
- [`shadow_mode.md`](shadow_mode.md), [`paper_mode.md`](paper_mode.md) — 단계별 운영자 가이드
