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

`backend/.env`는 **존재하지 않으며**, 시크릿은 `backend/notepad .env`(공백 포함 파일명, gitignored)에 저장돼 있다. backend `Settings`(`pydantic-settings`)는 기본적으로 `.env`만 로드하므로 실제 backend 부팅 시에는 이 시크릿이 적재되지 않는다 — **운영자 조치 필요** (아래 "권장 후속 조치" 참조).

| 키 | 존재 여부 | 비고 |
|---|---|---|
| `DEFAULT_MODE` | PRESENT | `LIVE_SHADOW` |
| `ENABLE_LIVE_TRADING` | PRESENT | `false` (안전) |
| `ENABLE_AI_EXECUTION` | PRESENT | `false` (안전) |
| `KIS_IS_PAPER` | PRESENT | `true` (PAPER host 강제) |
| `KIS_APP_KEY` | PRESENT (length=36) | KIS 표준 길이 |
| `KIS_APP_SECRET` | PRESENT (length=180) | KIS 표준 길이 |
| `KIS_ACCOUNT_NO` | PRESENT (length=8) | **이상 (10자 필요)** |

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

## 실제 주문(`place_order`) 호출 여부

**호출하지 않음.** CLAUDE.md 절대 원칙 1·2에 따라 본 테스트는 read-only 경로(`_ensure_token`, `inquire_balance`)만 사용했다. 또한 모든 안전 플래그(`ENABLE_LIVE_TRADING=false`, `KIS_IS_PAPER=true`)가 활성화되어 있어, 가령 시도했더라도 `KisBrokerAdapter.place_order(is_paper=False)`는 `NotImplementedError`로 차단된다.

## 결론

| 영역 | 상태 |
|---|---|
| KIS PAPER 게이트웨이 연결성 | **OK** |
| App Key / App Secret 유효성 | **OK** |
| Token issuance | **OK** |
| 잔고/포지션 조회 | **OK** (2차 재실행, `PRDT_CD=01` 가정) |
| 안전 플래그 정합성 | **OK** — `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `KIS_IS_PAPER=true` |
| 실주문 호출 | **None (의도된 부재)** |

체크리스트 #14 PASS — 토큰·잔고 모두 read-only 검증 완료.

## 오류 코드 정리

| 코드 | 발생 단계 | 의미 | 재현성 |
|---|---|---|---|
| `EGW00133` | Token 2회차 | 접근토큰 발급 1분당 1회 제한 | 1분 경과 후 해소 |

## 권장 후속 조치 (운영자)

1. ~~**`backend/notepad .env` → `backend/.env`로 정리**~~ — **완료** (2026-05-08).
2. **`KIS_ACCOUNT_NO`를 10자로 보정 (권장, 미완료)**
   - 현재 8자(CANO만). 2차 재실행은 코드 외부에서 `PRDT_CD="01"`을 가정해 통과시켰으나, `KisBrokerAdapter._split_account()`는 10자 입력을 전제하므로 backend 부팅 경로에서는 여전히 잘못 분리됨.
   - 운영자 KIS 마이페이지에서 실제 PRDT_CD를 확인해 `KIS_ACCOUNT_NO`를 10자로 갱신 필요. 위탁계좌(주식)는 일반적으로 `01`.
3. **`LIVE_MANUAL_APPROVAL` 라우팅 PR 진입 전 본 로그를 다시 갱신**
   - (2) 완료 + `inquire_balance`가 backend 정상 경로(`KisBrokerAdapter`)로도 PASS 됨을 확인한 뒤 다음 단계(`docs/promotion_policy.md`) 진입.

## 본 테스트가 검증하지 않은 것 (의도된 범위 밖)

- 실제 주문 제출(`place_order`) — 절대 원칙에 의해 영구 금지(LIVE)이거나 별도 옵트인 필요(PAPER `is_paper=True` 경로).
- 시세 조회(`get_price`) — 본 검증은 token + balance에 한정.
- LIVE 호스트(`https://openapi.koreainvestment.com:9443`) — `KIS_IS_PAPER=true`로 강제 차단됨.
- `ENABLE_AI_EXECUTION=true` 경로 — 절대 원칙 1·3에 의해 본 단계 미진입.
