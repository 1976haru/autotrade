# KIS API 연결 테스트 로그 (체크리스트 #14)

> 본 문서는 한국투자증권(KIS) Open API 연결성 검증 결과를 정리한다.
> **시크릿(App Key, App Secret, 계좌번호, 액세스 토큰, 잔고 값)은 본 문서에 일절 기록하지 않는다** — 길이/존재 여부만 기록한다.
> 실제 주문(`place_order`) 호출은 수행하지 않았다.

## 실행 환경

| 항목 | 값 |
|---|---|
| 실행 일시 | 2026-05-08 (KST) |
| Host | `https://openapivts.koreainvestment.com:29443` (PAPER) |
| 운용모드 (`DEFAULT_MODE`) | `LIVE_SHADOW` |
| `ENABLE_LIVE_TRADING` | `false` |
| `ENABLE_AI_EXECUTION` | `false` |
| `KIS_IS_PAPER` | `true` |
| Python | 3.14 (system) |
| HTTP client | `httpx 0.28.1` |
| 테스트 코드 | `backend/data/kis_connection_test.py` (gitignored, 실행 후 제거) |

## 환경변수 점검

`backend/.env`는 **존재하며** (운영자가 `backend/notepad .env`에서 정리 완료, 2026-05-08), backend `Settings`(`pydantic-settings`)가 정상 적재 가능한 상태.

| 키 | 존재 여부 | 비고 |
|---|---|---|
| `DEFAULT_MODE` | PRESENT | `LIVE_SHADOW` |
| `ENABLE_LIVE_TRADING` | PRESENT | `false` (안전) |
| `ENABLE_AI_EXECUTION` | PRESENT | `false` (안전) |
| `KIS_IS_PAPER` | PRESENT | `true` (PAPER host 강제) |
| `KIS_APP_KEY` | PRESENT (length=36) | KIS 표준 길이 |
| `KIS_APP_SECRET` | PRESENT (length=180) | KIS 표준 길이 |
| `KIS_ACCOUNT_NO` | PRESENT (length=10) | 정상 (CANO 8 + PRDT_CD 2 분리 가능) |

## 테스트 1 — 토큰 발급 (`POST /oauth2/tokenP`)

| 회차 | 결과 | HTTP | 비고 |
|---|---|---|---|
| 1회차 | **PASS** | 200 | `access_token` length=346 발급, `expires_at(UTC)=2026-05-08T22:21:56` (≈24h 유효), 토큰 값은 로그·문서에서 redact |
| 2회차 (즉시 재시도) | **EXPECTED RATE-LIMIT** | 403 | `error_code=EGW00133` — KIS는 토큰 발급을 1분당 1회로 제한. 정상 응답이며 연결성 자체의 추가 증거 |

→ KIS PAPER 게이트웨이와 양방향 통신 확립. App Key + App Secret 자체는 유효함이 확인됨.

## 테스트 2 — 잔고/포지션 조회 (`GET /uapi/domestic-stock/v1/trading/inquire-balance`, tr_id=`VTTC8434R`)

### 1차 실행 (2026-05-08 초기 검증)

| 결과 | 사유 |
|---|---|
| **SKIP** | `KIS_ACCOUNT_NO` 길이가 8자. `KisBrokerAdapter._split_account()`가 `account_no[:-2]`/`account_no[-2:]`로 분리하므로 8자 입력은 `cano=6, prdt=2`라는 잘못된 분리를 생성. 안전상 호출 자체를 막음 |

### 2차 재실행 (`PRDT_CD=01` 폴백, 같은 날)

`KIS_ACCOUNT_NO`가 여전히 8자(CANO만)인 상태에서 위탁계좌 paper 표준 PRDT_CD인 `01`을 명시적으로 가정하고 read-only 호출을 1회 시도.

| 항목 | 값 |
|---|---|
| 결과 | **PASS** |
| `rt_cd` | `"0"` (정상) |
| `msg_cd` | `"20310000"` (정상 조회 완료) |
| `output1` (positions) | 0행 (PAPER 계좌가 비어 있는 정상 상태) |
| `output2` (summary) | 1행 (현금/평가 요약, 값은 redact) |
| 가정 | `PRDT_CD="01"` (운영자 후속 조치 1번 참고) |

→ 잔고 endpoint와의 통신, tr_id(`VTTC8434R`) 정합성, 권한 모두 정상.

### 3차 재실행 (어댑터 경로, `KIS_ACCOUNT_NO=10`자 보정 후)

운영자가 `KIS_ACCOUNT_NO`를 10자(CANO 8 + PRDT_CD 2)로 갱신한 뒤 backend 정상 경로(`KisBrokerAdapter._split_account()`)를 통해 read-only 검증.

| 항목 | 값 |
|---|---|
| 결과 | **PASS** |
| `rt_cd` | `"0"` (정상) |
| `msg_cd` | `"20310000"` (정상 조회 완료) |
| `output1` (positions) | 0행 |
| `output2` (summary) | 1행 (값 redact) |
| 가정 | 없음 — env의 10자 그대로 split |

→ backend 부팅 경로에서도 잔고 조회 정상. 이전의 `PRDT_CD=01` 외부 가정이 더 이상 필요 없음.

## 테스트 3 — 시세 조회 (`get_price`, `GET /uapi/domestic-stock/v1/quotations/inquire-price`, tr_id=`FHKST01010100`)

`KisBrokerAdapter.get_price("005930")` (삼성전자) 1회 호출.

| 항목 | 값 |
|---|---|
| 결과 | **PASS** |
| symbol | `"005930"` (echo 일치) |
| price | `> 0` (값 redact, 정수 KRW) |
| source | `"kis"` |
| timestamp | 응답 시각 ISO 8601 (UTC) |

→ Quote endpoint 통신, JSON 파싱(`output.stck_prpr`), `Quote` 매핑 모두 정상.

## 테스트 4 — 어댑터 잔고 (`KisBrokerAdapter.get_balance`)

| 항목 | 값 |
|---|---|
| 결과 | **PASS** |
| `currency` | `"KRW"` |
| `cash` | `≥ 0` (값 redact) |
| `equity` | `≥ 0` (값 redact) |
| `buying_power` | 설정됨 (값 redact) |

## 테스트 5 — 어댑터 포지션 (`KisBrokerAdapter.get_positions`)

| 항목 | 값 |
|---|---|
| 결과 | **PASS** |
| `count` | `0` (PAPER 계좌가 비어 있는 정상 상태) |
| 행 별 값 | redact (이번 PAPER 계좌는 0행이라 출력 자체 없음) |

## 실제 주문(`place_order`) 호출 여부

**호출하지 않음.** CLAUDE.md 절대 원칙 1·2에 따라 본 테스트는 read-only 경로(`_ensure_token`, `get_price`, `inquire_balance`)만 사용했다. 또한 모든 안전 플래그(`ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `ENABLE_FUTURES_LIVE_TRADING=false`, `KIS_IS_PAPER=true`)가 활성화되어 있어, 가령 시도했더라도 `KisBrokerAdapter.place_order(is_paper=False)`는 `NotImplementedError`로 차단된다. `cancel_order`는 영구 stub.

## 시크릿 기밀성 검증

| 항목 | 결과 |
|---|---|
| `git status -sb` | `docs/kis_connection_test_log.md` 1건만 modified (본 문서) |
| `git ls-files`로 추적되는 env-관련 파일 | `backend/.env.example` (템플릿, 무해), `backend/alembic/env.py` (Alembic 설정, 무해) |
| `git check-ignore backend/.env` | `.gitignore:2:.env` 매치 — 정상 무시 |
| `git check-ignore backend/data/kis_connection_test.py` | `.gitignore:33:data/` 매치 — 정상 무시 |

→ App Key, App Secret, Account No, access_token 모두 워킹트리/index에 미반영. 본 문서에도 길이/존재 여부만 기록.

## 결론

| 영역 | 상태 |
|---|---|
| KIS PAPER 게이트웨이 연결성 | **OK** |
| App Key / App Secret 유효성 | **OK** |
| Token issuance | **OK** |
| 잔고/포지션 조회 (raw + adapter) | **OK** (3차 — 표준 10자 split, 외부 가정 없음) |
| 시세 조회 `get_price` | **OK** |
| 어댑터 `get_balance` / `get_positions` | **OK** |
| 안전 플래그 정합성 | **OK** — `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `ENABLE_FUTURES_LIVE_TRADING=false`, `KIS_IS_PAPER=true` |
| 실주문 호출 | **None (의도된 부재)** |
| 시크릿 git 추적 | **None** (`.env` 정상 ignore) |

체크리스트 #14 PASS — 토큰·시세·잔고·포지션 모두 read-only 검증 완료. backend 정상 경로(`KisBrokerAdapter`) PASS 확인됨.

## 오류 코드 정리

| 코드 | 발생 단계 | 의미 | 재현성 |
|---|---|---|---|
| `EGW00133` | Token 즉시 재시도 | 접근토큰 발급 1분당 1회 제한 | 1분 경과 후 해소 |
| `EGW00201` | 어댑터 호출 무간격 연속 | 초당 거래건수 초과 (PAPER ~1 TPS) | 호출 간 ≥1.2s 간격 시 해소 |

## 권장 후속 조치 (운영자)

1. ~~**`backend/notepad .env` → `backend/.env`로 정리**~~ — **완료** (2026-05-08).
2. ~~**`KIS_ACCOUNT_NO`를 10자로 보정**~~ — **완료** (2026-05-08, 3차 재실행에서 표준 split PASS 확인).
3. **`LIVE_MANUAL_APPROVAL` 라우팅 PR 진입 가능** — 본 로그 기준 backend 정상 경로 PASS, 다음 단계(`docs/promotion_policy.md`) 진입 가능. 단, LIVE place_order/cancel_order 활성화는 별도 옵트인 PR.

## 본 테스트가 검증하지 않은 것 (의도된 범위 밖)

- 실제 주문 제출(`place_order`) — 절대 원칙에 의해 영구 금지(LIVE)이거나 별도 옵트인 필요(PAPER `is_paper=True` 경로).
- 주문 취소(`cancel_order`) — 영구 stub, LIVE_MANUAL_APPROVAL PR에서 wire.
- 일자별 체결 조회(`inquire_daily_ccld`) — 본 검증은 token + price + balance/positions에 한정.
- LIVE 호스트(`https://openapi.koreainvestment.com:9443`) — `KIS_IS_PAPER=true`로 강제 차단됨.
- `ENABLE_AI_EXECUTION=true` 경로 — 절대 원칙 1·3에 의해 본 단계 미진입.
