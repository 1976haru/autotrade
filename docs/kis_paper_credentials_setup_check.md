# KIS 모의 자격 입력/검증 (4-01)

KIS 모의투자 API 주문을 위해 `backend/.env` 에 자격정보를 정확히 입력했는지
*확인*하는 체크리스트. **secret / 계좌번호 원문은 API / UI / log 어디에도 노출되지
않으며**, 존재 여부(boolean) + 누락 키 이름만 표시한다. 실거래 0건 — 본 작업은
자격 *검증* 이며 실전 API 호출이 아니다.

> 본 문서에 실제 secret 예시를 넣지 않는다. 가짜 예시는 `FAKE_...` 또는
> `<YOUR_KIS_APP_KEY>` 형태만 사용한다.

## 1. backend/.env 준비

1. `backend/.env.example` 을 `backend/.env` 로 복사한다.
   (`.env` 는 git 추적 안 됨 — `.gitignore` 에 `backend/.env` 등록.)
2. `backend/.env` 에만 자격을 입력한다 (`.env.example` 은 비워 둔다):
   ```
   KIS_APP_KEY=<YOUR_KIS_APP_KEY>
   KIS_APP_SECRET=<YOUR_KIS_APP_SECRET>
   KIS_ACCOUNT_NO=<YOUR_KIS_ACCOUNT_NO>
   KIS_PRODUCT_CODE=01
   ```
   - `KIS_PRODUCT_CODE` 는 계좌상품코드 (국내주식 현금 기본 `01`). secret 아님.

## 2. 안전 flag 확인 (backend/.env)

| 변수 | 필요 값 |
|---|---|
| `DEFAULT_MODE` | `PAPER` |
| `KIS_IS_PAPER` | `true` |
| `PAPER_BROKER_KIND` | `KIS_PAPER` |
| `ENABLE_KIS_PAPER_AUTO_TRADING` | `true` |
| `KIS_PAPER_AUTO_ORDER_DRY_RUN` | `false` |
| `KIS_PAPER_FILL_POLLING` | `true` |
| `ENABLE_LIVE_TRADING` | `false` (유지) |
| `ENABLE_AI_EXECUTION` | `false` (유지) |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` (유지) |

## 3. EXE 재시작 후 확인

1. EXE 를 재시작해 새 `.env` 를 로드한다.
2. **Settings 탭 → KIS 모의 자동매매 설정 상태 카드** 에서:
   - KIS 자격 구성: **구성됨**
   - APP KEY / APP SECRET / ACCOUNT NO / PRODUCT CODE: 각각 **구성됨**
   - 미구성이 있으면 "미구성 자격: KIS_APP_SECRET, …" 안내가 표시된다.
3. **진단 API** `GET /api/kis-paper/auto/status` 응답에서:
   - `credentials_present: true`
   - `kis_app_key_present / kis_app_secret_present / kis_account_no_present /
     kis_product_code_present: true`
   - `missing_credentials: []`
   - `default_mode: "PAPER"`, `paper_broker_kind: "KIS_PAPER"`,
     `dry_run: false`, `fill_polling: true`
   - `is_live_authorization: false`, `contains_secret: false`
4. `GET /api/kis-paper/readiness` 에서 `credentials_present` / `can_run_kis_paper`
   확인.

## 4. readiness 차단/허용 조건

- 자격(KEY/SECRET/ACCOUNT/PRODUCT_CODE) 중 하나라도 미설정 →
  `credentials_present=false` + `can_run_kis_paper=false` (KIS 모의 주문 진입
  불가, 즉 BLOCKED 상태). `missing_credentials` 에 누락 키 *이름만* 표시.
- 4종 모두 설정 + 안전 flag 정상 → `credentials_present=true` +
  `can_run_kis_paper=true` (주문 가능 *전 단계* 로 이동). 실제 주문은 기존
  sanctioned 경로 (route_order → RiskManager → PermissionGate → OrderExecutor →
  KisBrokerAdapter.place_order(is_paper=True)) 를 통과해야 한다.

## 5. secret / account 비노출 보장 (테스트로 lock)

- readiness / auto-status 응답은 `*_present` boolean + `missing_credentials`
  (키 이름) 만 carry — APP KEY / SECRET / ACCOUNT NO *값* 0건, length 0건,
  access_token 0건.
- Settings 카드: 입력창 / secret 보기 버튼 / 복사 버튼 / account_no 표시 /
  실전 ON 버튼 0개.
- 화면/로그에 secret/account 원문이 없는지 직접 확인:
  - 카드에 구성됨/미구성 라벨만 보이고 실제 값은 보이지 않아야 함.

## 6. 코드 단 보장

- `backend/tests/test_kis_paper_credentials_readiness.py` — 4종 자격 present /
  credentials_present / missing_credentials / 안전 flag carry / secret·account·
  app_key 값 미노출 / invariant / 정적 broker-import 가드 / API 필드.
- `backend/tests/test_exe_env_defaults.py` — `.env.example` 기본값 +
  `/auto/status` 자격 필드 (secret 값 패턴 0건).
- `frontend/src/components/common/KisPaperEnvStatusCard.test.jsx` — 4 row
  구성됨/미구성 + missing 표시 + footer 안내 + 입력창/버튼 0개 + 값 미노출.

본 작업은 `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
`ENABLE_FUTURES_LIVE_TRADING` / `KIS_IS_PAPER` default 를 변경하지 않으며,
KIS 실전 endpoint 를 사용하지 않는다.
