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
| 시세 stale 최대 age | `stale_price_max_age_seconds` | 60초 | `STALE_PRICE_MAX_AGE_SECONDS` |
| AI 제안 최소 confidence | `min_ai_confidence` | 0 (비활성) | `MIN_AI_CONFIDENCE` |
| AI reasoning 강제 | `enforce_ai_reasoning` | true | `ENFORCE_AI_REASONING` |
| AI rate limit 윈도우 | `ai_rate_limit_window_seconds` | 60초 | `AI_RATE_LIMIT_WINDOW_SECONDS` |
| AI rate limit 임계 | `ai_rate_limit_max_count` | 0 (비활성) | `AI_RATE_LIMIT_MAX_COUNT` |
| equity 대비 단일 주문 한도 (%) | `max_position_size_pct` | 0 (비활성) | `MAX_POSITION_SIZE_PCT` |
| 거래 허용 symbol whitelist | `symbol_whitelist` | (빈 set, 비활성) | `SYMBOL_WHITELIST` (콤마 구분: `"005930,000660"`) |
| 한국 시장 시간 강제 | `enforce_market_hours` | false (비활성) | `ENFORCE_MARKET_HOURS` (KST 평일 09:00–15:30 외 거부) |

값은 `app/risk/risk_manager.py::RiskPolicy` dataclass의 default. 런타임 `get_risk_manager()`는 `RiskPolicy.from_settings(get_settings())`로 env 값을 적용한다 — env를 비워두면 위 기본값 그대로. 향후 `/api/risk/policy` write 라우트(미구현)로 런타임 조정도 추가 예정.

## 평가 순서

`RiskManager.evaluate_order(order, mode, balance, positions, latest_price, requested_by_ai=False)`가 다음 순서대로 검사한다. **하나라도 reason을 추가하면 최종 decision = REJECTED** (단, NEEDS_APPROVAL early-return은 예외).

1. **Emergency Stop** — `risk.emergency_stop=True`면 즉시 `REJECTED`로 short-circuit. 이후 단계 평가하지 않으며, NEEDS_APPROVAL early-return(step 8)도 우회하지 못한다 — 알람 상태에서는 큐 자체가 닫힌다.
1.5. **(143)** **시세 Stale 검사** — `latest_price_timestamp`가 제공되고 `policy.stale_price_max_age_seconds > 0`이면, `now - timestamp > threshold`인 경우 즉시 `REJECTED`로 short-circuit. broker 응답이 너무 오래됐을 때 사이즈/포지션 평가의 근거 자체가 없으므로 emergency_stop과 같은 hard-reject 위치. `latest_price_timestamp` 미제공 또는 `stale_price_max_age_seconds=0`이면 검사 우회.
2. **주문 명목 한도** — `latest_price * order.quantity > max_order_notional`이면 거부
3. **일일 손실 한도** — `daily_realized_pnl ≤ -max_daily_loss`이면 거부. **(145)** `route_order`가 매 평가 직전에 [`compute_today_realized_pnl(db)`](../backend/app/risk/daily_pnl.py)로 audit log를 walk하여 카운터를 채운다 — 145 이전엔 0에 머물러 검사가 무효였음. 일자 경계는 UTC date (KST와 9시간 차이는 MVP의 의도된 단순화 — 운영 단계에서 KST 정밀화 가능)
4. **현금/포지션 가용성** — BUY인데 cash < notional이면 거부 (예비 체크 — 실 잔고는 broker 응답에서 확정)
5. **최대 보유 종목** — 신규 매수가 새로운 symbol을 추가하고 보유 종목 수 ≥ max_positions이면 거부
6. **종목별 노출 한도** — BUY인데 (현재 노출 + 추가 노출) > max_symbol_exposure이면 거부
7. **LIVE_SHADOW** — 모드가 LIVE_SHADOW이면 reason 추가 → 결과적으로 REJECTED
8. **NEEDS_APPROVAL early-return** — 모드가 LIVE_MANUAL_APPROVAL 또는 LIVE_AI_ASSIST일 때:
   - 먼저 `enable_live_trading` 검사. False면 `REJECTED`로 short-circuit (큐 자체가 닫힘 — 운영자가 명시적으로 flag를 켜야만 큐에 들어감).
   - True면 `decision=NEEDS_APPROVAL`로 즉시 반환 (이후 단계 평가 안 함).
9. **AI 실행 권한** — `requested_by_ai=True`이고 모드/플래그가 AI 실행 허용 안 하면 거부
10. **LIVE 가드** — 모드가 LIVE_*인데 `enable_live_trading=False`이면 거부

## 모드별 결정 매트릭스

`enable_live_trading`/`enable_ai_execution`이 모두 default(False)일 때 :

| 모드 | 일반 주문 | AI 주문(`requested_by_ai=True`) |
|---|---|---|
| `SIMULATION` | 한도 통과 시 APPROVED | 한도 통과 시 APPROVED (AI 가드 무관) |
| `PAPER` | 한도 통과 시 APPROVED | APPROVED (PAPER는 LIVE_*에 해당 안 함) |
| `LIVE_SHADOW` | **REJECTED** ("LIVE_SHADOW records signals only") | **REJECTED** |
| `LIVE_MANUAL_APPROVAL` | **REJECTED** (LIVE 가드, queue gate) | **REJECTED** |
| `LIVE_AI_ASSIST` | **REJECTED** (LIVE 가드, queue gate) | **REJECTED** |
| `LIVE_AI_EXECUTION` | LIVE 가드에서 REJECTED | AI 가드에서 REJECTED |

`enable_live_trading=True`로 켜면 LIVE_MANUAL_APPROVAL/LIVE_AI_ASSIST는 NEEDS_APPROVAL로 큐에 들어가고, LIVE_AI_EXECUTION의 일반 주문은 APPROVED. AI 주문은 여전히 `enable_ai_execution=False`이므로 REJECTED.

## 운용모드와 안전 플래그

자세한 매트릭스는 [`promotion_policy.md`](promotion_policy.md) 마지막 표 참조.

| 환경변수 | 기본 | 적용 위치 |
|---|---|---|
| `DEFAULT_MODE` | `SIMULATION` | RiskManager mode-aware 분기 + `get_broker()` 라우팅 |
| `ENABLE_LIVE_TRADING` | `false` | RiskPolicy → step 10 (LIVE 가드) |
| `ENABLE_AI_EXECUTION` | `false` | RiskPolicy → step 9 (AI 가드) |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | FuturesRiskManager (선물 전용) |
| `STALE_PRICE_MAX_AGE_SECONDS` | `60` | RiskPolicy → step 1.5 (143 stale price hard-reject) |
| `MIN_AI_CONFIDENCE` | `0` (비활성) | RiskPolicy → AI 가드 (158): requested_by_ai=True인 주문이 signal_confidence 미달이면 REJECTED |
| `ENFORCE_AI_REASONING` | `true` | RiskPolicy → AI 가드 (159): requested_by_ai=True인 주문이 ai_decision_meta.reasons 비어있으면 REJECTED. LIVE에서는 절대 끄지 말 것. |
| `AI_RATE_LIMIT_WINDOW_SECONDS` | `60` | route_order rate limit (161): (strategy, symbol) 별 윈도우 길이 |
| `AI_RATE_LIMIT_MAX_COUNT` | `0` (비활성) | rate limit (161): 윈도우 내 AI 제안 최대 카운트. 임계 도달 시 추가 제안 REJECTED — LLM bug / 무한 루프 방어 |

## 긴급 정지

- API: `POST /api/risk/emergency-stop` body `{"enabled": true, "reason_code": "manual_operator"}` (153 — reason_code optional, enum 검증)
- 허용 reason_code (`app.risk.emergency_reasons.EmergencyStopReason`): `manual_operator`, `daily_loss_limit`, `data_stale`, `broker_error`, `repeated_order_failure`, `abnormal_slippage`, `agent_warning`, `margin_risk`, `futures_liquidation_risk`. 미등록 코드는 422.
- 코드 목록 조회: `GET /api/risk/emergency-stop/reasons`.
- 프론트엔드: 전략·리스크 탭의 "긴급 정지" 버튼 (`useRiskPolicy` 훅)
- 효과: `RiskManager.emergency_stop = True`로 토글 → 모든 신규 주문이 step 1에서 즉시 REJECTED로 short-circuit. LIVE_MANUAL_APPROVAL/LIVE_AI_ASSIST 모드에서도 큐잉되지 않는다.
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
- **(145) Daily PnL 누수 차단** — `route_order`가 매 평가 직전에 `compute_today_realized_pnl(db)`로 카운터를 채워 `max_daily_loss`가 실효성 가드로 작동. 어제 BUY → 오늘 SELL의 overnight 청산도 오늘 PnL에 귀속된다 (실현 시점 기준).
- **(166) KST 일자 경계** — 145의 일자 경계가 UTC에서 **KST**로 변경. 한국 시장(09:00–15:30 KST) 운영 가정. KST 자정(=15:00 UTC, 장 종료 후)에 카운터 리셋 → 운영자 직관 일치. 이전 UTC 기반은 자정(=09:00 KST 장 시작)에 리셋되어 의미와 어긋남. `tz=timezone.utc` 명시로 backwards-compat 가능.
- **(167) Approval queue TTL** — `RiskPolicy.approval_ttl_seconds`(기본 0=비활성). `PermissionGate.list_pending(ttl_seconds=N)` 호출 시 lazy expire — N초 초과 PENDING은 EXPIRED로 자동 전환되어 시세 stale 상태에서 늦은 승인 위험을 사전 차단. Terminal `EXPIRED`는 approve/reject/cancel 모두 거부. 운영자가 `expire_stale_approvals(ttl_seconds, now)`로 명시적 sweep도 가능.
- **(146) Approve-time 가드 일관성** — `PermissionGate.approve`의 re-eval도 submit 시점과 동일한 가드를 적용한다. submit과 approve 사이의 시간 차이로 (a) Quote.timestamp가 stale이거나(143), (b) 다른 거래로 max_daily_loss(145)를 초과한 손실이 누적되면 approve가 차단된다. 차단 시 approval은 PENDING으로 유지되어 운영자가 시세 회복 / 새 날 / 한도 조정 후 재시도 가능.
- **(160) Approve-time AI invariant 일관성** — 158/159 가드도 approve 시점에 적용된다. audit row의 `requested_by_ai` / `signal_confidence` / `ai_decision_meta`를 source of truth로 OrderRequest 재구성 → re-eval에 carry. submit 후 운영자가 `min_ai_confidence` 임계를 올리거나 `enforce_ai_reasoning`을 활성화한 경우 approve가 차단된다.

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
