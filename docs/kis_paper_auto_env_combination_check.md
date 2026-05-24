# KIS 모의 자동주문 .env 조합 확인 (4-02)

EXE 실행 시 기본 `.env` 조합만으로 KIS 모의투자 자동주문이 **안전하게 준비**
되는지 확인하는 체크리스트. 실거래 0건 — 본 작업은 환경 *조합 확인* 이며 실전
API 호출 / 실제 계좌 주문이 아니다.

> secret / 계좌번호 원문은 API / UI / log 어디에도 노출되지 않는다.

## 1. backend/.env 준비

1. `backend/.env.example` 을 `backend/.env` 로 복사한다 (`.env` 는 git 미추적).
2. `backend/.env` 에만 자격 입력 (4-01 참조):
   ```
   KIS_APP_KEY=<YOUR_KIS_APP_KEY>
   KIS_APP_SECRET=<YOUR_KIS_APP_SECRET>
   KIS_ACCOUNT_NO=<YOUR_KIS_ACCOUNT_NO>
   KIS_PRODUCT_CODE=01
   ```

## 2. 기본 .env 조합 (EXE 안전 기본값)

`.env.example` 은 다음 조합이며 (테스트로 lock), 그대로 `.env` 로 쓰면 KIS 모의
자동주문이 준비된다:

| 변수 | 값 |
|---|---|
| `DEFAULT_MODE` | `PAPER` |
| `KIS_IS_PAPER` | `true` |
| `PAPER_BROKER_KIND` | `KIS_PAPER` |
| `ENABLE_AI_PAPER_BACKGROUND_TICK` | `true` |
| `ENABLE_KIS_PAPER_AUTO_TRADING` | `true` |
| `KIS_PAPER_AUTO_ORDER_DRY_RUN` | `false` |
| `KIS_PAPER_FILL_POLLING` | `true` |
| `ENABLE_LIVE_TRADING` | `false` |
| `ENABLE_AI_EXECUTION` | `false` |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` |
| `KIS_PRODUCT_CODE` | `01` (상품코드 기본값, secret 아님) |
| `KIS_APP_KEY` / `KIS_APP_SECRET` / `KIS_ACCOUNT_NO` | (비움 — `.env` 에서만 입력) |

## 3. READY / BLOCKED 판정

`GET /api/kis-paper/auto/status` 의 `kis_paper_auto_ready`:
- **READY (true)**: 자격 4종 present + `ENABLE_KIS_PAPER_AUTO_TRADING=true` +
  실거래/AI OFF + KIS_IS_PAPER=true.
- **BLOCKED (false)**: 자격 미설정 등 — 자동주문 차단. `missing_credentials`
  에 누락 키 이름만 표시 (값 0건).

응답 invariant: `is_live_authorization=false`, `broker_order_type="KIS_PAPER"`,
`contains_secret=false`.

## 4. EXE 재시작 후 확인

1. EXE 재시작 → 새 `.env` 로드.
2. **Settings 탭 → KIS 모의 자동매매 설정 상태 카드**:
   - 헤드라인 "KIS 모의투자 자동주문 READY" (자격 미구성 시 "BLOCKED")
   - KIS Paper Auto: **ON**, Background Tick: **ON**, dry-run: **OFF**,
     Fill Polling: **ON**, 실거래: **OFF**, KIS 자격 구성: **구성됨**
3. **AutoPaperLoopCard** (Bot / Dashboard):
   - 자동 tick driver 패널의 "KIS 모의 자동주문: ON" + "주문 전송"
   - 안전 문구 "broker_order_type=KIS_PAPER · is_live_authorization=false"
4. **진단 API** `GET /api/kis-paper/auto/status`:
   - `kis_paper_auto_ready: true`, `enable_ai_paper_background_tick: true`,
     `dry_run: false`, `fill_polling: true`, `enable_live_trading: false`.
5. 화면/로그에 secret/account 원문 없는지 확인 (구성됨/미구성 라벨만).

## 5. 코드 단 보장 (테스트로 lock)

- `backend/tests/test_kis_paper_auto_env_combination.py` — `.env.example` 조합 +
  readiness READY/BLOCKED + `/auto/status` 조합 필드 + secret/account 값 패턴 0건.
- `frontend/src/components/common/KisPaperEnvStatusCard.test.jsx` — Background
  Tick ON / READY·BLOCKED 헤드라인 / 입력창·버튼 0개 / 값 미노출.
- `frontend/src/components/tabs/AutoPaperLoopCard.test.jsx` — KIS 모의 자동주문
  ON 패널 표시 + 실전/매수/매도/Place Order 버튼 0개.

본 작업은 `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
`ENABLE_FUTURES_LIVE_TRADING` / `KIS_IS_PAPER` / `PAPER_BROKER_KIND` default 를
변경하지 않으며, KIS 실전 endpoint 를 사용하지 않는다.
