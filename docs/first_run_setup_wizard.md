# First-run Setup Wizard — 설계 문서

> 베타테스터가 `.env` 를 PowerShell / 메모장으로 직접 수정하지 않게 한다.
> 첫 실행 시 화면 wizard 가 *모드 / KIS 키 / 안전 flag 상태* 를 안내하고 입력
> 받는다.
>
> **본 PR 시점: skeleton + 본 문서** — 실 저장 흐름은 후속 PR.

## 1. 왜 별도 PR 인가

안전한 secret 저장은 가벼운 작업이 아니다 — 잘못 만들면 *frontend
localStorage 에 KIS Secret 이 노출* 되어 CLAUDE.md 절대 원칙 4 (API Key,
App Secret, 계좌번호 frontend 저장 금지) 를 위반한다.

본 PR 의 범위:
- wizard UI **skeleton** (입력 필드만, 저장 시 placeholder)
- 본 설계 문서 (저장 흐름 / 보안 정책)
- `.env` 직접 입력 fallback 유지 (베타테스터가 끝까지 hand-edit 도 가능)

후속 PR 의 범위 (별도):
- backend `POST /api/desktop/config` endpoint — secret 검증 + `.env` 안전 patch
- OS secure store 통합 (Windows DPAPI / Credential Manager via
  `tauri-plugin-stronghold` 등)
- 연결 테스트 (`POST /api/desktop/config/test-kis`)
- LIVE_* flag 변경 차단 백엔드 가드 (read-only response 강제)

## 2. Wizard 흐름

```
앱 첫 실행
    ↓
[1] 환영 화면
    ↓
[2] 운용모드 선택
    ↓
[3] KIS 모의투자 키 입력
    ↓
[4] 안전 flag 상태 확인 (read-only)
    ↓
[5] 저장 + 연결 테스트
    ↓
[6] 메인 화면
```

## 3. 화면별 사양

### 3.1 [1] 환영 화면

- 큰 타이틀: "환영합니다 — Agent Trader v1"
- 부제: "AI 에이전트 단타 자동매매 관제 (베타)"
- 안내 텍스트:
  - "본 베타에서는 실거래가 *비활성* 입니다."
  - "처음 3분 정도면 설정이 끝납니다."
- 버튼: **"시작하기"**

### 3.2 [2] 운용모드 선택

| 옵션 | 설명 | 가용성 |
|---|---|---|
| `SIMULATION` | 가짜 데이터 + MockBroker — 모든 흐름 안전 시뮬레이션 | ✅ 권장 |
| `PAPER` | 실 시세 + KIS 모의투자 (가상 자금) | ✅ |
| `LIVE_SHADOW` | 실 계좌 read-only, 주문 금지 | ⚠ 고급 (운영자 별도 안내 후 사용) |
| `LIVE_MANUAL_APPROVAL` | 1건씩 수동 승인 후 실거래 | 🚫 **베타 단계 비활성** |
| `LIVE_AI_ASSIST` | AI 후보 + 수동 승인 | 🚫 **비활성** |
| `LIVE_AI_EXECUTION` | AI 자동 실행 | 🚫 **영구 비활성 (#75)** |

베타테스터에게는 *SIMULATION 또는 PAPER* 만 선택 가능하게 노출. LIVE_*
옵션은 회색으로 비활성 + tooltip 으로 *"운영자 별도 옵트인 절차 필요"* 표시.

### 3.3 [3] KIS 모의투자 키 입력

PAPER 모드 선택 시에만 노출. SIMULATION 이면 §3.5 으로 직행.

| 필드 | 의미 | 검증 |
|---|---|---|
| `KIS_APP_KEY` | KIS 모의투자 발급 키 | 길이 ≥ 30, 영숫자만 |
| `KIS_APP_SECRET` | KIS 모의투자 secret | 길이 ≥ 100, 영숫자/특수문자 |
| `KIS_ACCOUNT_NO` | 모의투자 계좌 | `XXXXXXXX-XX` 형식 |
| `KIS_IS_PAPER` | 모의투자 여부 | ✅ true 고정, *비활성 toggle* |

UI 동작:
- `KIS_APP_SECRET` 은 기본 `password` 타입 — *눈 아이콘* 으로 일시 표시.
- 입력 중 *frontend localStorage 0건 저장* — `<input>` value 만 메모리에서
  유지.
- "다음" 버튼 누를 때만 backend `POST /api/desktop/config` 로 전송.
- 잘못된 형식이면 inline 오류 (예: "App Secret 이 너무 짧습니다 — 모의투자
  발급 키가 맞나요?").

### 3.4 [4] 안전 flag 상태 확인 (read-only)

backend `GET /api/status` 의 `safety_flags` 값을 그대로 표시. **사용자는
이 화면에서 toggle 할 수 없다**.

```
✓ ENABLE_LIVE_TRADING:         false  (현 베타에서는 false 고정)
✓ ENABLE_AI_EXECUTION:         false  (현 베타에서는 false 고정)
✓ ENABLE_FUTURES_LIVE_TRADING: false  (영구 false — 선물 LIVE 미구현)
✓ KIS_IS_PAPER:                true   (모의투자만)
✓ DEFAULT_MODE:                SIMULATION 또는 PAPER (앞서 선택값)
```

위 flag 들을 변경하려면 **운영자가 `backend/.env` 를 직접 편집한 후 앱 재시작**
해야 한다 — wizard 에서는 *읽기 전용*. 후속 PR 의 backend endpoint 도
LIVE_* flag mutation 을 거부 (서버 단 가드).

### 3.5 [5] 저장 + 연결 테스트

- **"저장"** 버튼:
  - SIMULATION 이면 즉시 완료 (KIS 키 불필요).
  - PAPER 이면 backend 가 secret 을 *backend/.env* 에 안전하게 patch
    (후속 PR — 본 PR 은 placeholder 표시).
- **"연결 테스트"** 버튼:
  - backend → KIS 모의투자 `GET account/balance` 1회 호출 (read-only).
  - 결과: ✅ 성공 / ❌ 실패 + 친절 한 오류 (예: "App Secret 이 만료됐을 수
    있습니다 — 다시 발급해 주세요").
  - 성공 시 wizard 완료 가능.

### 3.6 [6] 메인 화면 진입

wizard 결과를 `desktop_wizard_completed=true` 로 backend config 에 저장
(후속 PR). 이후 앱 시작 시 본 wizard 는 *다시 표시되지 않음*. `설정` 탭에서
*Setup wizard 다시 실행* 버튼으로 수동 재진입 가능.

## 4. 저장소 — 본 PR 시점 결정

| 항목 | 위치 | 비고 |
|---|---|---|
| **선택한 모드** | backend `.env`::`DEFAULT_MODE` | 후속 PR 의 patch endpoint |
| **KIS_APP_KEY** | backend `.env`::`KIS_APP_KEY` | 후속 PR — 본 PR 은 fallback (hand-edit) |
| **KIS_APP_SECRET** | backend `.env`::`KIS_APP_SECRET` | 같음 |
| **KIS_ACCOUNT_NO** | backend `.env`::`KIS_ACCOUNT_NO` | 같음 |
| wizard 완료 상태 | backend config DB (json) | 후속 PR |

frontend localStorage 에 *어떤 secret 도 저장되지 않는다*. wizard 의 `<input>`
값은 메모리만 유지하고 "저장" 버튼 누를 때 backend 로 전송 후 즉시 폐기.

### 4.1 후속 PR — OS secure store

Windows DPAPI / Credential Manager 를 거쳐:
- `Agent Trader v1 / KIS_APP_SECRET`
- `Agent Trader v1 / KIS_APP_KEY`
- `Agent Trader v1 / KIS_ACCOUNT_NO`

위 항목들이 *암호화* 되어 저장되며, backend 시작 시 `python` 측에서
`win32crypt` (또는 `keyring` 라이브러리)로 복호화해 메모리로만 로드한다.
`.env` 파일에는 *플레이스홀더* (예: `KIS_APP_SECRET=__KEYRING__`) 만 남고
실 값은 OS secure store 가 보관.

## 5. 보안 invariant (테스트로 lock 예정 — 후속 PR)

- `wizard_state` 객체에 `secret_key` / `app_secret` / `app_key` /
  `account_no` 필드가 *그대로 localStorage 에 직렬화되지 않는다* — 정적 grep
  guard.
- backend `POST /api/desktop/config` 응답에 `KIS_APP_SECRET` 그대로 carry
  하지 않는다 — masked echo 만 (`***last4***`).
- LIVE_* flag mutation 시도는 즉시 400 + audit row.
- wizard UI 에 "LIVE 활성화" / "실거래 켜기" / "Place Order" 같은 enabling
  버튼 0개.

## 6. 본 PR 의 산출물

- 본 설계 문서 (`first_run_setup_wizard.md`)
- `.env` 직접 입력 fallback 안내 (베타테스터 가이드 §5)
- frontend `Settings` 탭의 `VersionInfoCard` 옆에 추후 wizard 진입점 자리만 확보

후속 PR 산출물 (별도):
- `frontend/src/components/wizard/FirstRunWizard.jsx`
- `backend/app/api/routes_desktop.py`
- `backend/app/desktop/config_store.py` (OS secure store)
- 통합 테스트

## 7. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙 4 (API Key frontend 저장 금지)
- [`docs/desktop_packaging.md`](desktop_packaging.md) — 데스크톱 패키징 결정
- [`docs/desktop_update_policy.md`](desktop_update_policy.md) — 업데이트 정책
- [`docs/beta_tester_install_guide.md`](beta_tester_install_guide.md) — 사용자
  가이드
