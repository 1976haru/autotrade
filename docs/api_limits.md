# API Limits

외부 서비스 호출 제한과 본 프로젝트의 대응 정책. 정확한 수치는 각 사업자의 공식 문서를 일차 출처로 본다.

## 외부 API

### KIS (한국투자증권)

| 항목 | 값 (참고) | 본 프로젝트 대응 |
|---|---|---|
| OAuth token | 24시간 유효, 1 app key당 1 token | `KisClient`가 토큰 캐싱 + 만료 60초 전 자동 갱신 (`_TOKEN_REFRESH_MARGIN`) |
| Quote (`/quotations/inquire-price`) | 분당 호출 제한 (공식 문서 확인) | 미구현 — 운영 시 `SlidingWindowRateLimiter` 적용 예정 |
| Balance (`/trading/inquire-balance`) | 분당 호출 제한 | 미구현 |
| Daily fills (`/trading/inquire-daily-ccld`) | 분당 호출 제한 | 미구현 |
| Order (`/trading/order-cash`) | 일반 조회보다 엄격 | 미구현 |
| 모의/실전 host 분리 | `openapivts.koreainvestment.com:29443` vs `openapi.koreainvestment.com:9443` | `is_paper`로 host + tr_id 분기 |

### Anthropic Claude API

| 항목 | 값 | 대응 |
|---|---|---|
| RPM/TPM 한도 | tier에 따라 다름 (Anthropic Console 확인) | 호출 빈도가 낮은 분석 라우트에서만 사용 |
| 요청당 max tokens | 모델별 컨텍스트 한도 | `AiClient.analyze` 기본 1024 (조정 가능) |
| 429 backoff | 권장 exponential backoff | SDK 내장 retry 사용 — `ANTHROPIC_MAX_RETRIES` (기본 2) 회까지 자동 backoff. 풀리지 않으면 라우트가 HTTP 429로 매핑하여 502와 구분 |
| 요청 timeout | 운영자 결정 | `ANTHROPIC_TIMEOUT_SECONDS` (기본 30초) — `AsyncAnthropic`에 직접 전달 |

### Yahoo Finance (yfinance)

| 항목 | 값 (참고) | 대응 |
|---|---|---|
| Public scraping | 명시적 한도 없음, 2-3 req/sec 권장 | yfinance 라이브러리 자체 throttling, 본 프로젝트는 `BarCache`로 중복 호출 회피 |
| 401/403/429 | 차단 시 일시 거절 | 호출 실패 시 `KisApiError` 패턴 따라 상위로 전파 |

## 내부 API

본 프로젝트의 FastAPI 라우트에는 자체 rate limit이 없다. 운영 시 reverse proxy(nginx 등) 단에서 적용한다.

| 항목 | 정책 |
|---|---|
| 모든 외부 노출 라우트 | nginx에서 IP/세션 단위 제한 (운영 단계 결정) |
| `/api/ai/analyze` | Anthropic 호출 비용이 있으므로 운영자만 사용 (인증 게이트는 별도 PR) |
| `/api/strategies/replay` | 서버측 메모리 + market 호출 발생 — DoS 방지 위해 인증 필요 |
| `/api/backtest/run` | 클라이언트 제공 bars 모드는 큰 페이로드 가능 — body 크기 제한은 nginx 단 |

## 백그라운드 폴러

| 폴러 | 변수 | 기본값 |
|---|---|---|
| `FillPoller` | `FILL_POLLING_INTERVAL_SECONDS` | 5초 |
| `useApprovals` (frontend) | hardcoded | 5초 |
| `usePortfolio` 가격 폴링 (frontend) | `PRICE_TICK_MS` 상수 | 2000ms |

폴링 주기를 너무 짧게 잡으면 KIS rate limit에 걸릴 수 있다. 운영 시 `ENABLE_FILL_POLLING=false`로 두고 수동 갱신만 하는 옵션 추천.

## 공통 정책

1. **실패 시 무한 재시도 금지** — 단일 시도 후 상위로 전파.
2. **주문 API는 일반 조회 API보다 강한 제한 적용** — KIS는 별도 tr_id 사용 (`VTTC0802U`/`VTTC0801U` paper, `TTTC*` live).
3. **429/오류코드 발생 시 신규 주문 중지** — 운영자 emergency_stop 또는 nginx 단 차단.
4. **Exponential backoff** — Anthropic은 SDK 내장 retry로 처리. KIS는 미구현(운영 단계 추가 예정).
5. **Audit 로그 우선** — 호출 실패도 `OrderAuditLog`/`AiAnalysisLog`에 기록.

## 코드 hooks

- `app/core/rate_limiter.py::SlidingWindowRateLimiter` — in-memory, max_calls/window_seconds 인자. `KisClient`에 wired (5 calls/sec 기본, `KIS_RATE_LIMIT_*` 환경변수로 조정).
- `app/ai/client.py::AiClient` — `AsyncAnthropic(max_retries, timeout)`로 SDK 내장 backoff 위임. `ANTHROPIC_MAX_RETRIES` / `ANTHROPIC_TIMEOUT_SECONDS` 환경변수로 조정.

## 향후 작업

- KIS 공식 문서에서 endpoint별 정확한 RPM/TPM 매트릭스 채우기
- `/api/ai/analyze`에 사용자별 호출 제한 (인증 도입 후)
- 운영 nginx config에 IP/세션 단위 제한 추가

## 관련 문서

- [`broker_selection.md`](broker_selection.md) — 브로커 선정 비교
- [`shadow_mode.md`](shadow_mode.md) / [`paper_mode.md`](paper_mode.md) — 운영자 가이드
- [`risk_policy.md`](risk_policy.md) — emergency_stop 흐름
