# EGW00201 근본수정 1단계 — 프론트 일괄조회 구현 + dry-run 검증

> `results/ratelimit_fix/design_v2.md` 후속. 구현 + 실측. 4계층
> (`RiskManager`/`OrderGuard`/`PermissionGate`/`OrderExecutor`) 미접촉,
> 조회 경로만 변경.

## 구현

| 파일 | 변경 |
|---|---|
| `backend/app/api/routes_broker.py` | `GET /broker/prices?symbols=A,B,C` 신설. 기존 `/price/{symbol}`의 stale-fallback 로직을 `_fetch_quote_with_fallback` 공용 헬퍼로 추출해 재사용(중복 없음). **순차** 조회(중요 — 아래 dry-run 참고) |
| `frontend/src/services/backend/client.js` | `brokerPrices(symbols)` 추가 |
| `frontend/src/store/usePortfolio.js` | 종목별 개별 `brokerPrice` N콜 → `brokerPrices` 1콜로 교체. balance는 기존처럼 독립(`Promise.allSettled`) |
| `frontend/src/config/constants.js` | `PRICE_TICK_MS` 15000 → 20000 (quote 캐시 TTL 20s와 정렬) |
| `backend/tests/test_broker_price_stale.py` | 배치 엔드포인트 테스트 5개 추가(순차호출 검증, 부분실패, stale fallback, 빈 symbols) |
| `frontend/src/store/usePortfolio.test.js` | 배치 mock으로 교체 + 호출횟수/인자 검증 테스트 추가 |

**KIS 다종목 일괄 시세 API는 미사용** — `inquire-price`(FHKST01010100)는 종목당
1콜만 지원(codebase 내 다른 tr_id 발견 안 됨). 서버가 내부에서 순차 반복 후
1개 JSON으로 묶어 반환하는 방식으로 구현(사용자 지시 "안 되면 서버측에서
묶어서 순차조회 후 1응답"대로).

## ★dry-run 검증 — 정직한 재평가

`backend/scripts/_tmp_ratelimit_batch_dryrun.py`(비커밋)로 실측. **결과가
애초 가설과 달랐다 — 정직하게 기록한다.**

### 예상과 다른 점: 배치화 자체는 KIS 호출 페이싱을 안 바꾼다

프론트→백엔드 요청이 N+1개(구, 동시발사)든 2개(신, 배치)든, 결국 **같은
프로세스 전역 rate limiter**(`get_kis_rate_limiter()`, 1콜/1.1초)를 거친다.
`SlidingWindowRateLimiter.acquire()`는 동시 호출도 순서대로 정확히
직렬화하므로 — **"동시발사냐 순차호출이냐"는 limiter 뒤에서 실제 KIS 호출
간격을 바꾸지 못한다.** 즉 "일괄조회가 limiter를 N+1번→1번 소모"는 틀린
가정이었다 — **limiter 소모(=실제 KIS 콜 수)는 캐시 히트 여부에만 좌우되고,
배치냐 개별이냐와는 무관**하다.

### 실제로 무엇이 바뀌었나 — dry-run 3가지 관점 분리 측정

| 관점 | 구(15초, N+1 동시발사) | 신(20초, 배치 1콜) |
|---|---|---|
| **프론트→백엔드 HTTP 요청 수/틱** | N+1개 (N=보유종목수) | **2개**(balance+prices) — 확정 감소 |
| **백엔드→KIS 실제 콜 수(캐시미스만)** | 캐시 상태에 좌우, 배치와 무관 | 동일 — 배치가 줄이지 않음 |
| **틱당 drain(KIS 예산 소진) 시간** | (1+N)×1.1s 근사 | (1+N)×1.1s 근사, **동일** |

**N=15(설정된 `max_concurrent_positions` 상한)로 3틱 시뮬레이션**:

| | 구(15s 간격) | 신(20s 간격) |
|---|---|---|
| tick 1 drain | 16.81s | 16.82s |
| **간격 초과(backlog) 여부** | **True — 15s 못 맞춤** | **False — 20s 안에 완료** |
| 3틱 누적 실제 KIS 콜 | 25 | 32(간격이 길어 캐시 만료 재발생 타이밍만 다름) |

**결론**: N=15에서 backlog를 실제로 없앤 건 **폴링 주기 15→20초 조정**이었다
— 배치화가 아니다. 현재 실측 보유종목(8종)에서는 애초에 구버전도 15초
안에 drain됐다(9.9s < 15s) — 즉 지금 당장의 조회량 문제는 이미 "간당간당"
수준이었고, 종목 수가 늘어날수록(최대 15까지) 구버전은 무너지고 신버전은
버틴다.

**그럼 배치화는 왜 했나 — 실제 이득 3가지**:
1. **HTTP 요청 수 확정 감소**(N+1→2, N=8이면 9→2, 67% 감소) — 브라우저·
   uvicorn 요청처리 오버헤드 감소, 로그/디버깅 단순화.
2. **N개의 독립 요청이 각자 백엔드 handler task를 열던 구조 제거** — 배치는
   1개 handler 안에서 순차 처리하므로 서버 쪽 동시성 오버헤드가 줄어든다.
3. **폴링주기(20s)=캐시TTL(20s) 정렬**로 steady state(보유종목 변화 없는
   구간)에서 재조회 대부분이 캐시 히트 — dry-run tick2가 모든 시나리오에서
   drain=0.00s(전부 캐시 히트)로 이를 확인했다.

**정정 필요 사항**: 애초 설계(1단계 지시문)의 "N+1콜→1콜로 limiter 소모
자체를 줄인다"는 프레이밍은 부정확했다 — 정확히는 "HTTP 요청 수를 줄이고,
폴링주기 조정과 결합해 백로그를 없앤다"가 맞다. 코드 변경 자체는 지시대로
구현했고 실제로 backlog 방지 효과는 있으나(위 N=15 표), 메커니즘은 배치화가
아니라 간격 조정이 주도한다는 걸 dry-run이 보여줬다.

## 손절 경로 영향 — 0 확인

- `app/market_data/kis_realtime.py::fetch_realtime_quote` — **미접촉**(0줄).
  raw `KisClient.get_price()`를 직접 호출하는 별도 경로로, `/broker/price`
  라우트나 `KisBrokerAdapter`의 quote 캐시를 전혀 거치지 않는다(재확인).
- `app/risk/`, `app/permission/`, `app/execution/order_router.py`,
  `app/execution/order_executor.py` — **diff 0줄**(`git diff --stat` 확인).
- 다만 **공유 자원(같은 rate limiter)**이라는 점은 여전하다 — 이번 변경으로
  프론트발 수요가 줄어든 만큼(HTTP 오버헤드 감소 + steady-state 캐시히트
  증가) 청산 판단용 실시간 조회가 리미터 대기열에서 밀릴 확률은 **간접적으로
  낮아졌다**(경쟁 트래픽 감소). 경로 자체의 우선순위 분리(설계 v2 §3의
  "우선순위 큐")는 이번 1단계 범위 밖 — 여전히 필요시 다음 단계 후보.

## 테스트

- 백엔드: 신규 5개(배치 엔드포인트) + 기존 stale-fallback 4개 전부 통과.
  전체 스위트 8,488 passed / 37 failed(전부 이번 변경 이전부터 있던 무관한
  기존 실패 — 목록 동일 확인).
- 프론트: `usePortfolio.test.js` 8개(신규 3개 포함) 통과. 전체 프론트
  스위트 197 files / 3,326 tests 전부 통과.

## 프론트 화면 확인

라이브 백엔드가 07-08 코드로 떠 있어(재시작 전) 이번 변경은 실제 화면에서
아직 관찰 불가 — 월요일 재시작 후 대시보드에서 가격표시/stale 배너가
정상 동작하는지 별도 확인 필요(코드 리뷰 + 단위테스트로는 확인했으나 실제
브라우저 수동확인은 미실시, 정직히 명시).
