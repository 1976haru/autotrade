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

| 결과 | 사유 |
|---|---|
| **SKIP** | `KIS_ACCOUNT_NO` 길이가 8자. KIS 표준은 10자(8자리 CANO + 2자리 PRDT_CD)이며 `KisBrokerAdapter._split_account()`가 `account_no[:-2]`/`account_no[-2:]`로 분리한다. 길이 8로 분리 시 `cano=6자, prdt=2자`가 되어 명백히 잘못된 호출이 생성되므로 read-only 조회조차 시도하지 않음 |

본 단계는 [`backend/app/brokers/kis.py`](../backend/app/brokers/kis.py)의 `_split_account()` 계약을 준수한다. 잔고 조회는 운영자가 `KIS_ACCOUNT_NO`를 10자로 갱신한 뒤 재실행하면 통과 가능.

## 실제 주문(`place_order`) 호출 여부

**호출하지 않음.** CLAUDE.md 절대 원칙 1·2에 따라 본 테스트는 read-only 경로(`_ensure_token`, `inquire_balance`)만 사용했다. 또한 모든 안전 플래그(`ENABLE_LIVE_TRADING=false`, `KIS_IS_PAPER=true`)가 활성화되어 있어, 가령 시도했더라도 `KisBrokerAdapter.place_order(is_paper=False)`는 `NotImplementedError`로 차단된다.

## 결론

| 영역 | 상태 |
|---|---|
| KIS PAPER 게이트웨이 연결성 | **OK** |
| App Key / App Secret 유효성 | **OK** |
| Token issuance | **OK** |
| 잔고/포지션 조회 | **PENDING** — 계좌번호 길이 보정 후 재시도 |
| 안전 플래그 정합성 | **OK** — `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `KIS_IS_PAPER=true` |
| 실주문 호출 | **None (의도된 부재)** |

체크리스트 #14는 토큰 발급까지를 PASS로 판정 — 잔고 단계는 운영자 후속 조치 후 재실행 대기.

## 오류 코드 정리

| 코드 | 발생 단계 | 의미 | 재현성 |
|---|---|---|---|
| `EGW00133` | Token 2회차 | 접근토큰 발급 1분당 1회 제한 | 1분 경과 후 해소 |

## 권장 후속 조치 (운영자)

1. **`backend/notepad .env` → `backend/.env`로 정리**
   - 현재 backend `Settings`는 `.env`만 로드하므로 실제 서버 부팅 시 KIS 자격증명이 미주입 상태.
   - 동일 디렉터리에서 파일명만 변경(또는 내용 복사)하고 `notepad .env`는 삭제 권장.
   - 두 파일 모두 `.gitignore` 적용 중이므로 커밋 위험은 없음.
2. **`KIS_ACCOUNT_NO`를 10자로 보정**
   - 현재 8자 → KIS 표준 `CANO(8) + PRDT_CD(2)` 형식으로 갱신.
   - 위탁계좌(주식): PRDT_CD = `01`이 일반적. 실제 값은 운영자 KIS 계좌 정보로 확인 필요.
3. **(2) 완료 후 본 테스트 재실행**
   - `cd backend && python data/kis_connection_test.py` (스크립트 재생성 시)
   - 또는 동등한 ad-hoc 검증 절차를 통해 `inquire_balance` rt_cd=`0` 확인.
4. **`LIVE_MANUAL_APPROVAL` 라우팅 PR 진입 전 본 로그를 다시 갱신**
   - 잔고 조회까지 PASS 상태가 되어야 다음 단계(`docs/promotion_policy.md`) 진입 안전.

## 본 테스트가 검증하지 않은 것 (의도된 범위 밖)

- 실제 주문 제출(`place_order`) — 절대 원칙에 의해 영구 금지(LIVE)이거나 별도 옵트인 필요(PAPER `is_paper=True` 경로).
- 시세 조회(`get_price`) — 본 검증은 token + balance에 한정.
- LIVE 호스트(`https://openapi.koreainvestment.com:9443`) — `KIS_IS_PAPER=true`로 강제 차단됨.
- `ENABLE_AI_EXECUTION=true` 경로 — 절대 원칙 1·3에 의해 본 단계 미진입.
