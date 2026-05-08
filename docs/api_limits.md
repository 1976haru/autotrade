# API Limits

외부 서비스 호출 제한 + 본 프로젝트의 대응 정책. 정확한 수치는 각 사업자의 공식 문서를 일차 출처로 보며, 미확인 항목은 **TBD**로 두고 향후 작업으로 분리한다.

## 1. Rate Limit 정책 요약

| 원칙 | 적용 |
|---|---|
| 모든 외부 호출은 단일 retry/backoff 정책을 따른다 | KIS = `SlidingWindowRateLimiter`, Anthropic = SDK 내장 retry |
| 주문 API는 조회 API보다 보수적 | 별도 tr_id + LIVE는 PermissionGate 큐 통과 후에만 도달 |
| 429 / RATE 오류 발생 시 신규 주문 중지 | 운영자 emergency_stop + nginx 단 차단 권장 |
| 무한 재시도 금지 | 단일 시도 후 상위로 전파, audit 기록 |
| 폴링 주기는 보수적 | 운영 시 `ENABLE_FILL_POLLING=false` 권장 |
| 시크릿은 backend `.env`에만 | rate limit 우회 목적의 frontend 직접 호출은 절대 원칙 위반 |

코드 단 강제 — `app/core/rate_limiter.py::SlidingWindowRateLimiter`는 `KisClient` 생성 시 주입(`app/api/deps.py`). PAPER 기준 `KIS_RATE_LIMIT_CALLS=5`, `KIS_RATE_LIMIT_WINDOW_SECONDS=1.0` 기본값.

## 2. KIS API 제한

| 항목 | 공식 한도 | 본 프로젝트 코드 위치 | 대응 정책 |
|---|---|---|---|
| OAuth token (`POST /oauth2/tokenP`) | 1분당 1회 (실측 — `EGW00133` 발생 사례) | `KisClient._ensure_token` | 24시간 캐시 + 만료 60초 전 자동 갱신 (`_TOKEN_REFRESH_MARGIN`) |
| Quote (`/quotations/inquire-price`) | TBD (PAPER 실측 ~1 TPS, `EGW00201`) | `KisClient.get_price` → `KisBrokerAdapter.get_price` | `SlidingWindowRateLimiter` 5 calls/1s 기본 |
| Balance (`/trading/inquire-balance`) | TBD | `KisClient.inquire_balance` → adapter `get_balance` / `get_positions` | 동일 limiter |
| Positions | (잔고와 동일 endpoint, 분리 호출 시 동일 한도 차감) | adapter `get_positions` | 같은 호출 1회로 balance + positions 분리 매핑 |
| Order (`/trading/order-cash`) | TBD — 조회보다 엄격 | `KisClient.place_order` → adapter `place_order` | LIVE는 `LIVE_MANUAL_APPROVAL` 라우팅 PR까지 차단 (현재 `NotImplementedError`) |
| Order status (단건 lookup 부재) | inquire-daily-ccld 한도 차감 | `KisClient.inquire_daily_ccld` → adapter `get_order_status` | client-side ODNO filter — 추가 호출 0 |
| Daily fills (`/trading/inquire-daily-ccld`) | TBD | 위와 동일 | 동일 limiter |
| WebSocket / 실시간 시세 | TBD | **미통합** | Phase TBD — 통합 시 별도 connection limit 정책 작성 |
| 모의/실전 host 분리 | `openapivts.koreainvestment.com:29443` vs `openapi.koreainvestment.com:9443` | `KisClient.base_url` | `is_paper`로 host + tr_id 분기, factory가 PAPER 모드 + `KIS_IS_PAPER=false` 구성 시 시작 거부 |

> 정확한 RPM/TPM은 **공식 문서 확인 후 본 표 갱신** — 향후 작업 9절 참고.

## 3. Kiwoom REST API 제한 (2차 브로커, 미도입)

본 표는 Phase 2 어댑터 도입 시 채운다. 자세한 도입 계획: [`kiwoom_rest_research.md`](kiwoom_rest_research.md).

| 항목 | 공식 한도 | 본 프로젝트 대응 |
|---|---|---|
| 인증 / 토큰 | TBD | TBD — KIS 동형 패턴 (캐시 + 자동 갱신) 예정 |
| 현재가 | TBD | TBD |
| 차트 / 분봉 | TBD | TBD |
| 계좌 잔고 | TBD | TBD |
| 주문 | TBD — 조회보다 엄격 예상 | LIVE는 KIS와 동일하게 `LIVE_MANUAL_APPROVAL` 통과 후에만 |
| 주문 취소 / 정정 | TBD | TBD |
| 조건검색 | TBD — KIS 미지원 차별 endpoint | Phase 3 read-only 검증 후 결정 |
| 순위 정보 | TBD | TBD |
| WebSocket / 실시간 | TBD | TBD |

> 모든 항목 **공식 문서 확인 필요**. Phase 2 stub PR에서 `KiwoomClient` skeleton + 본 표 갱신.

## 4. 내부 Polling 정책

| 폴러 | 위치 | 기본 주기 | 비고 |
|---|---|---|---|
| `FillPoller` | `app/execution/fill_poller.py` | 5초 (`FILL_POLLING_INTERVAL_SECONDS`) | KIS `inquire_daily_ccld` 호출 — `ENABLE_FILL_POLLING=false`(default) 시 비활성. 운영 권장 |
| `useApprovals` (frontend) | `frontend/src/store/useApprovals.js` | adaptive: 5s active → 30s idle → 60s hidden | 빈 큐 + 백그라운드면 트래픽 급감. 가시성 + 활동도 기반 자동 조절 (#100 / #125) |
| `useOrderAudits` (frontend) | `frontend/src/store/useAuditLogs.js` | adaptive (#105) | 신규 audit 발생률 기반 동적 주기 |
| `usePortfolio` 가격 폴링 | `frontend/src/store/usePortfolio.js` | `PRICE_TICK_MS=2000ms` | backend `/api/market/price` 경유 — KIS quote 호출 1회 |
| Agent / Reconciliation 등 기타 dashboard | 각 hook | 5~30s | 합산 RPM이 KIS quote 한도를 초과하지 않도록 운영자 모니터링 |

폴링 합산 — 모든 폴러가 KIS quote / balance 한도를 공유하므로 운영 시 다음 가드 권장:

1. `ENABLE_FILL_POLLING=false` (운영자가 수동 갱신).
2. dashboard는 idle / hidden 시 자동 backoff (이미 frontend 측 구현됨).
3. KIS rate limit이 hit하면 `KisClient`의 `acquire()`가 코루틴을 자동 대기 → 폴러가 자연스럽게 throttle.

## 5. 재시도 정책

| 시나리오 | 정책 | 코드 |
|---|---|---|
| **무한 재시도 금지** | 단일 시도 후 상위로 전파 | `KisClient` raises `KisAuthError` / `KisApiError` 즉시 |
| **429 / RATE** | 신규 주문 중지 + audit REJECTED | 운영자 emergency_stop + nginx 차단 권장. KIS PAPER `EGW00201` 발생 시 `acquire()`가 자동 대기 |
| **Exponential backoff** | Anthropic SDK 내장 | `ANTHROPIC_MAX_RETRIES=2` 기본 — 풀리지 않으면 라우트가 HTTP 429 반환 (502와 구분) |
| **주문 API > 조회 API 보수성** | 별도 tr_id + LIVE는 PermissionGate 후에만 broker 도달 | `route_order` 단일 진입점 |
| **연속 실패** | 자동 emergency_stop 권장 | `auto_stop_consecutive_rejections` env (기본 0=비활성, 권장 5~10) — 가드 #182 |
| **Network timeout** | 제한적 재시도 후 audit 실패 기록 | `KisClient` httpx timeout 10초 — 재시도 0회 (운영 단계 추가 검토) |
| **Order rejected (broker side)** | audit REJECTED, RiskManager → PermissionGate 흐름은 그대로 | `OrderAuditLog` 자동 기록 — `route_order` 보장 |

자세한 emergency_stop reason 분류: [`risk_policy.md`](risk_policy.md).

## 6. 오류 코드 정책

| 코드 | 의미 | 운영 대응 |
|---|---|---|
| `401 Unauthorized` | 토큰 만료 / 미인증 | **즉시 중지** — 토큰 갱신 1회 시도, 실패 시 운영자 알림 |
| `403 Forbidden` | 권한 / IP 화이트리스트 거부 | **즉시 중지** — 운영자가 IP 등록 / 권한 확인. 자동 재시도 금지 |
| `429 / RATE` | rate limit | **신규 주문 중지** — backoff 대기 또는 emergency_stop |
| `5xx` | broker 장애 | 재시도 1~2회 후 중지, audit 기록, 운영자 알림 |
| `network timeout` | 일시 네트워크 | 제한적 재시도 (1회) 후 audit 실패 기록 |
| `KIS rt_cd != "0"` | broker 측 거절 | `OrderAuditLog`에 `msg_cd` + `msg1` 기록, 주문 REJECTED. 자동 재시도 금지 |
| `EGW00133` (KIS) | 토큰 발급 1분당 1회 초과 | 1분 경과 후 자연 해소 — 자동 재시도 금지 |
| `EGW00201` (KIS) | 초당 거래건수 초과 (PAPER ~1 TPS) | `SlidingWindowRateLimiter.acquire()`가 자동 대기 |
| `Anthropic RateLimitError` | LLM RPM/TPM | `routes_ai`가 HTTP 429로 매핑 — 502와 구분 |
| `Anthropic APIStatusError 5xx` | LLM 장애 | SDK 내장 retry → 실패 시 502 |

## 7. 장운영 시간표 (한국 거래소)

| 세션 | 시각 (KST) | 본 프로젝트 처리 |
|---|---|---|
| 정규장 | 평일 09:00 – 15:30 | `enforce_market_hours=true` 시 RiskManager가 외 주문 거부 (가드 #176) |
| 장전 시간외 단일가 | 08:30 – 09:00 | **미지원** — 정규장 외 모두 거부 (보수적) |
| 장후 시간외 단일가 | 15:40 – 16:00 | **미지원** |
| 시간외 단일가 (16:00–18:00) | 평일 16:00 – 18:00 | **미지원** |
| 휴장일 (공휴일 / 임시 휴장) | 거래소 캘린더 | **미구현** — 현재는 평일이면 09:00–15:30 모두 open으로 가정 |

코드 단 — `app/risk/risk_manager.py::_is_market_open` (`_KST=UTC+9`, `_MARKET_OPEN_KST=09:00`, `_MARKET_CLOSE_KST=15:30`, 토/일은 항상 closed). 휴장일 캘린더는 향후 작업 (KRX OpenAPI 또는 broker calendar 연동).

> 보수적 차단 — 휴장일 캘린더 부재 상태에서 평일 공휴일에 주문이 KIS 측에서 거부될 수 있으나, 본 프로젝트는 그 거절을 audit `REJECTED`로 기록하는 것까지가 책임 범위다. **`enforce_market_hours=true` 옵트인 운영자에게는 KRX 캘린더 통합을 권장**.

## 8. 운영 정책 (운용모드별)

| 모드 | KIS 호출 정책 | 주문 정책 | 폴링 |
|---|---|---|---|
| `SIMULATION` | 호출 없음 (MockBroker) | 메모리 시뮬, 즉시 FILLED | 무관 — 외부 호출 0 |
| `PAPER` | KIS PAPER 호스트, limiter 5/1s | KIS 모의 `place_order(is_paper=True)` 활성 | `ENABLE_FILL_POLLING=false` 권장 (RPM 절약) |
| `LIVE_SHADOW` | read-only (`get_price` / `inquire_balance`) | **모든 주문 RiskManager에서 REJECTED** | 가능 — read만 |
| `LIVE_MANUAL_APPROVAL` | LIVE 호스트, limiter 강화 권장 | PermissionGate 큐 통과 후에만 broker 도달, 사용자 승인 필수 | 신중 — 잘못된 폴링 빈도가 LIVE quote RPM 소진 위험 |
| `LIVE_AI_ASSIST` | LIVE 호스트 | AI 후보 → 사용자 승인 후에만 | 동일 |
| `LIVE_AI_EXECUTION` | LIVE 호스트 | **기본 비활성** (`ENABLE_AI_EXECUTION=false`) — 8개 옵트인 충족 후 별도 PR | 동일 |

LIVE 진입 시 권장 — `KIS_RATE_LIMIT_CALLS`을 PAPER(5)보다 보수적으로 (예: 3/1s), 폴링 주기 ≥ 5초, frontend dashboard idle backoff 필수.

## 9. 향후 작업

| 항목 | 우선순위 | 트리거 |
|---|---|---|
| KIS 공식 endpoint별 정확한 RPM/TPM 표 채우기 | 높음 | LIVE 활성화 PR 전 |
| Kiwoom REST 공식 호출 한도 표 채우기 | 중간 | Kiwoom Phase 2 stub PR |
| KIS WebSocket 연결 정책 + 동시 connection 한도 | 낮음 | WebSocket 통합 PR |
| Redis 기반 분산 rate limiter 전환 | 낮음 | 다중 worker / 다중 호스트 운영 시 |
| nginx IP / 세션 단위 limit | 낮음 | 운영 배포 시 |
| KRX 휴장일 캘린더 통합 | 중간 | `enforce_market_hours` 옵트인 운영 시 |
| 주문 timeout 시 재시도 1회 정책 도입 | 낮음 | LIVE 운영 데이터로 필요성 확정 후 |
| `/api/ai/analyze` 사용자별 호출 제한 | 낮음 | 인증 도입 후 |

## 10. 코드 hooks

| 컴포넌트 | 위치 | 환경변수 |
|---|---|---|
| `SlidingWindowRateLimiter` | `app/core/rate_limiter.py` | (limiter 자체는 인자만, 주입 측에서 env) |
| KIS limiter 주입 | `app/api/deps.py::get_kis_client` | `KIS_RATE_LIMIT_CALLS=5`, `KIS_RATE_LIMIT_WINDOW_SECONDS=1.0` |
| AI rate limit (per strategy/symbol) | `app/ai/rate_limit.py::check_rate_limit` | `AI_RATE_LIMIT_WINDOW_SECONDS=60`, `AI_RATE_LIMIT_MAX_COUNT=0` (비활성) |
| Global rate limit (system-wide) | `app/ai/rate_limit.py::check_global_rate_limit` | `GLOBAL_RATE_LIMIT_WINDOW_SECONDS=60`, `GLOBAL_RATE_LIMIT_MAX_COUNT=0` |
| Anthropic SDK retry/backoff | `app/ai/client.py::AiClient` | `ANTHROPIC_MAX_RETRIES=2`, `ANTHROPIC_TIMEOUT_SECONDS=30` |
| Market hours 가드 | `app/risk/risk_manager.py::_is_market_open` | `ENFORCE_MARKET_HOURS=false` (default) |
| 자동 emergency stop (연속 실패) | `app/risk/risk_manager.py` 가드 #182 | `AUTO_STOP_CONSECUTIVE_REJECTIONS=0` (default 비활성) |
| 일일 주문 수 한도 | RiskManager 가드 #183 | `MAX_ORDERS_PER_DAY=0` (default 비활성) |

## 관련 문서

- [`broker_selection.md`](broker_selection.md) — 브로커 선정 비교
- [`kiwoom_rest_research.md`](kiwoom_rest_research.md) — Kiwoom REST 도입 조사 (Phase 2 stub 시 본 문서 갱신)
- [`shadow_mode.md`](shadow_mode.md) / [`paper_mode.md`](paper_mode.md) — 운영자 가이드
- [`risk_policy.md`](risk_policy.md) — emergency_stop 흐름, 27 가드
- [`risk_guards_matrix.md`](risk_guards_matrix.md) — 가드별 코드/플래그 reference
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 + 환경 플래그
- [`kis_connection_test_log.md`](kis_connection_test_log.md) — KIS PAPER 연결 검증 (`EGW00133` / `EGW00201` 실측 출처)
