# Agent Trader EXE 운영자 매뉴얼 — 초보자 실행가이드

> 이 문서 하나만 순서대로 따라 하면, 코딩을 몰라도 EXE 실행 · KIS 모의 설정 ·
> 안전 점검 · 오류 확인 · 장중 테스트 준비를 할 수 있습니다.

---

## 1. 이 문서의 목적

- 코딩을 모르는 사용자가 **문서만 보고** 프로그램을 실행/점검할 수 있게 합니다.
- EXE 로 프로그램을 켜고, KIS **모의투자** 자동매매가 안전하게 동작하는지
  확인하는 절차를 안내합니다.
- 실전 전환 *전*에 Paper / KIS 모의 상태를 점검하는 방법을 설명합니다.

## 2. 현재 프로그램의 상태

- 이 프로그램은 현재 **KIS 모의투자 / Paper 검증용**입니다.
- **실전 자동매매를 승인하는 문서가 아닙니다.**
- **수익을 보장하지 않습니다.** (시뮬레이션/모의 결과는 실제 성과와 다릅니다.)
- 실전 전환은 별도 Paper Gate · Live Capital Review · 운영자 수동 승인 이후에만
  *검토*합니다. 추가 검증이 필요합니다.

## 3. 꼭 알아야 할 안전 원칙

아래는 **반드시** 지켜야 하는 원칙입니다.

- `ENABLE_LIVE_TRADING` 은 반드시 **false** 여야 합니다.
- `ENABLE_AI_EXECUTION` 은 반드시 **false** 여야 합니다.
- `ENABLE_FUTURES_LIVE_TRADING` 은 반드시 **false** 여야 합니다.
- `KIS_IS_PAPER` 는 반드시 **true** 여야 합니다 (모의투자 전용).
- `KIS_APP_SECRET`, `KIS_ACCOUNT_NO`, `KIS_APP_KEY` 같은 자격정보는
  **화면 · 로그 · GitHub · 캡처에 올리면 안 됩니다.**
- `.env` 파일은 **GitHub 에 올리면 안 됩니다** (`.gitignore` 에 이미 등록).
- 모르는 오류가 나오면 **주문을 계속 실행하지 말고**, 로그를 복사해서 먼저
  원인을 확인합니다.
- 이 프로그램은 **돈을 안전하게 벌어주는 도구가 아닙니다.** 검증 도구입니다.

## 4. 설치 전 준비물

- Windows PC
- 한국투자증권(KIS) **모의투자** 신청 + 모의투자용 App Key / App Secret /
  모의 계좌번호 (KIS 개발자센터에서 발급)
- 인터넷 연결
- (선택) 문제 발생 시 도움을 받을 수 있는 Claude Code

> 실전 계좌 자격이 아니라 **모의투자(Paper)** 자격을 준비하세요.

## 5. EXE 설치 및 실행

1. 배포받은 설치 파일(예: `AgentTrader-v1-Setup.exe`)을 더블클릭합니다.
2. 안내에 따라 설치하고, 바탕화면 아이콘으로 실행합니다.
3. 프로그램이 켜지면 내부적으로 **backend sidecar**(백그라운드 서버)가 함께
   실행됩니다. 처음 실행 시 수십 초 정도 걸릴 수 있습니다.
4. 화면이 뜨면 상단/하단 탭에서 **설정(Settings)** 탭을 엽니다.

> 명령어로 직접 실행하는 개발용 방법은 [`docs/exe_env_setup.md`](exe_env_setup.md)
> 를 참고하세요. 초보자는 설치형 EXE 사용을 권장합니다.

## 6. backend/.env 설정 방법

자격정보와 안전 플래그는 `backend/.env` 파일에만 입력합니다.
(`backend/.env.example` 를 복사해 `backend/.env` 를 만든 뒤 값만 채웁니다.)

> **주의**: 아래는 *형식 예시*입니다. `<...>` 부분에 본인 KIS **모의투자** 값을
> 넣되, 그 값을 문서/화면/GitHub 에 올리지 마세요. 실제 키/계좌번호 예시는
> 일부러 적지 않았습니다.

```
# 운용 모드 — 모의투자 전용
DEFAULT_MODE=PAPER
KIS_IS_PAPER=true
PAPER_BROKER_KIND=KIS_PAPER

# KIS 모의 자동주문 동작
ENABLE_AI_PAPER_BACKGROUND_TICK=true
ENABLE_KIS_PAPER_AUTO_TRADING=true
KIS_PAPER_AUTO_ORDER_DRY_RUN=false
KIS_PAPER_FILL_POLLING=true

# 실전 관련 플래그 — 반드시 false 유지
ENABLE_LIVE_TRADING=false
ENABLE_AI_EXECUTION=false
ENABLE_FUTURES_LIVE_TRADING=false

# KIS 모의 자격 4종 (값은 본인 것으로, 문서엔 적지 않음)
KIS_APP_KEY=<YOUR_KIS_APP_KEY>
KIS_APP_SECRET=<YOUR_KIS_APP_SECRET>
KIS_ACCOUNT_NO=<YOUR_KIS_ACCOUNT_NO>
KIS_PRODUCT_CODE=01
```

설명:
- `KIS_PRODUCT_CODE=01` 은 보통 종합계좌/상품코드 용도입니다(기본값 01).
- 자격 4종이 비어 있으면 자동주문은 **READY 가 아니라 BLOCKED** 가 정상입니다.
- 화면에는 자격정보 **원문이 표시되지 않아야** 정상입니다(구성됨/미구성만 표시).

자세한 입력 점검은
[`docs/kis_paper_credentials_setup_check.md`](kis_paper_credentials_setup_check.md),
조합 점검은
[`docs/kis_paper_auto_env_combination_check.md`](kis_paper_auto_env_combination_check.md)
를 참고하세요.

## 7. KIS 모의 자격 4종 입력 방법

KIS 개발자센터에서 **모의투자용** 으로 발급받은 값을 `backend/.env` 에 입력합니다.

| 항목 | .env 키 | 설명 |
|---|---|---|
| App Key | `KIS_APP_KEY` | 모의투자 앱 키 |
| App Secret | `KIS_APP_SECRET` | 모의투자 앱 시크릿 |
| 계좌번호 | `KIS_ACCOUNT_NO` | 모의 계좌번호 |
| 상품코드 | `KIS_PRODUCT_CODE` | 보통 `01` |

- 입력 후 **EXE 를 재시작**해야 적용됩니다.
- 4종이 모두 채워져야 KIS 모의 자동주문이 READY 가 됩니다(없으면 BLOCKED).

## 8. 절대 켜면 안 되는 실전 플래그

다음 3개는 **항상 false** 여야 하며, 1개는 **항상 true** 여야 합니다.

- `ENABLE_LIVE_TRADING` → **false**
- `ENABLE_AI_EXECUTION` → **false**
- `ENABLE_FUTURES_LIVE_TRADING` → **false**
- `KIS_IS_PAPER` → **true**

> 위 값을 실전 방향으로 바꾸면 안 됩니다. 이 매뉴얼은 모의투자 전용이며,
> 실전 전환은 별도 승인 절차가 필요합니다(섹션 27 참고).

## 9. EXE 재시작 후 Settings 탭 확인

`.env` 를 수정했다면 EXE 를 껐다 켜고, **설정(Settings)** 탭을 엽니다.
설정 탭에는 다음 운영 카드가 있습니다.

- 🖥️ **EXE 연결 상태** (Backend / Sidecar / 진단)
- 🏷️ **앱 버전 / 빌드 정보**
- 🩺 **EXE Preflight Smoke Test**
- 📜 **최근 이벤트 로그**
- 🔧 **KIS 모의 자동매매 설정 상태**

## 10. Backend/Sidecar 연결 상태 확인

"🖥️ EXE 연결 상태" 카드에서 확인합니다.

- **Backend API**: "연결됨" 이어야 정상. "연결 실패" 면 EXE 를 재시작하고 잠시
  기다립니다.
- **Sidecar**: 데스크톱에서 "실행 중" 이면 정상.
- **Diagnostics / DB**: "정상" 이면 정상.
- **KIS Paper**: READY / BLOCKED.
- "연결됨" 과 "연결 실패" 가 **동시에** 표시되지는 않습니다(설계상 차단).

자세히: [`docs/exe_backend_sidecar_status_check.md`](exe_backend_sidecar_status_check.md)

## 11. 앱 버전 / commit 확인

"🏷️ 앱 버전 / 빌드 정보" 카드에서 확인합니다.

- **앱 버전**, **빌드 commit**, **빌드 시각**, **채널** 이 표시됩니다.
- 현재 EXE 가 최신인지 확인하려면 빌드 commit 을 GitHub `main` 최신 commit 과
  비교합니다. 다르면 구버전일 수 있습니다.
- "로컬 변경 포함(dirty)" 이 "예" 면 비공식 빌드일 수 있습니다.

자세히: [`docs/exe_build_version_check.md`](exe_build_version_check.md)

## 12. KIS 모의 자동주문 READY 확인

"🔧 KIS 모의 자동매매 설정 상태" 카드에서 확인합니다.

- **READY**: 자격 4종 구성 + 안전 flag 정상 + 자동주문 ON + KIS 모의 → 정상.
- **BLOCKED**: 자격 미구성이거나 안전 flag 가 어긋난 상태. 자격을 채우거나
  `.env` 를 점검합니다. (자격이 없으면 BLOCKED 가 **정상**입니다.)
- 화면에는 자격 **원문이 표시되지 않고** "구성됨 / 미구성" 만 보입니다.

## 13. Preflight Smoke Test 실행

"🩺 EXE Preflight Smoke Test" 카드에서 **PASS / WARN / FAIL** 을 확인합니다.
이 검사는 **주문을 발생시키지 않습니다**.

- **PASS / WARN** 이면 기본 작동 정상(장 닫힌 날 WARN 은 정상).
- **FAIL** 이면 해당 항목(예: `safety_flags`, `db_status`)을 점검합니다.

명령으로도 실행할 수 있습니다(개발용):

```
python scripts/exe_smoke_test.py --base-url http://127.0.0.1:8000
```

자세히: [`docs/exe_preflight_smoke_test.md`](exe_preflight_smoke_test.md)

## 14. 최근 이벤트 로그 확인

"📜 최근 이벤트 로그" 카드에서 최근 100건을 봅니다.

- **source 필터**: RuntimeEvent / AI 판단 / KIS 주문.
- **severity 필터**: ERROR / CRITICAL 만 보면 오류 원인을 빠르게 찾습니다.
- **검색**: 사유 코드(`NO_MARKET_DATA`, `REJECTED` 등)로 필터.
- **복사** 버튼으로 문제 상황을 복사할 수 있습니다(민감정보 자동 제외).

자세히: [`docs/exe_runtime_event_log_viewer.md`](exe_runtime_event_log_viewer.md)

## 15. 장이 닫힌 날 정상 상태

장이 닫힌 날(주말/공휴일/장외 시간)에도 아래는 확인할 수 있고, 이런 표시는
**정상**입니다.

- EXE 실행 / backend sidecar 연결 / Settings 탭 표시
- KIS 자격 구성 여부 / KIS Paper Auto READY 또는 BLOCKED
- Preflight Smoke Test 실행
- `MARKET_CLOSED` / `NO_MARKET_DATA` 메시지 표시
- 자격정보 미노출
- KIS 모의 **주문번호(broker_order_no) 없음** (장이 닫혀 주문이 안 나감)

> 장 닫힌 날 `MARKET_CLOSED` / `NO_MARKET_DATA` 는 오류가 아니라 **정상**입니다.

## 16. 장이 열린 날 확인 순서

장이 열린 날(평일 09:00~15:30 KST 부근)에는 아래 순서로 확인합니다.

1. EXE 실행 → Settings 탭 → "연결됨" / READY 확인.
2. KIS 실시간/현재가 **시세 수신** 확인 (`NO_MARKET_DATA` 해소).
3. 자동매매가 1종목 정도부터 동작하는지 확인(canary).
4. KIS 모의 **주문번호 수신** 확인.
5. **체결 / 미체결** 상태 갱신 확인.
6. KIS 모의 **잔고 / 포지션** 동기화 확인.
7. KIS(MTS/HTS) 모의 화면의 주문내역과 대조.

## 17. KIS 모의 시세 확인

- 장이 열린 시간에 시세가 들어오면 `NO_MARKET_DATA` 가 사라집니다.
- 시세가 오래되면 `PRICE_STALE` 이 표시될 수 있습니다 — 네트워크/장 시간을
  확인합니다.

## 18. KIS 모의 주문번호 확인

- 자동매매가 KIS 모의로 주문을 내면 **주문번호(broker_order_no)** 가 최근
  이벤트 로그(source=KIS 주문)와 Decision Episode 에 표시됩니다.
- 같은 주문번호를 KIS 모의 화면(주문내역)에서도 확인할 수 있습니다.
- 주문번호는 자격정보가 아니므로 표시되어도 됩니다(계좌번호/시크릿과 다름).

## 19. 체결/미체결 확인

- `KIS_PAPER_FILL_POLLING=true` 이면 체결 상태가 주기적으로 갱신됩니다.
- 최근 이벤트 로그 / Decision Episode 에서 체결(filled) / 미체결(pending) /
  거절(`ORDER_REJECTED`) 상태를 확인합니다.
- 미체결이 오래 지속되면 KIS 모의 화면과 로그를 대조합니다.

## 20. Paper/KIS 포트폴리오 확인

- 대시보드/포트폴리오 영역에서 Paper(모의) 현금 / 보유 종목 / 평가금액을
  확인합니다.
- KIS 모의 잔고와 화면 포지션이 동기화되는지 확인합니다.
- Paper 자금(시드머니)은 설정 탭에서 변경할 수 있으며 **실제 돈과 무관**합니다.

## 21. 매수/매도 자동화 상태 확인

- 자동매매는 AI/전략 판단 → 위험 점검 → KIS **모의** 주문 순서로 진행됩니다.
- 신호가 없으면 `NO_SIGNAL` 로 표시되며 이는 정상일 수 있습니다.
- 이 화면들은 **조회 전용**입니다 — 수동 매수/매도 버튼이나 주문 재시도 버튼은
  제공하지 않습니다.

## 22. 손절/익절/트레일링/장마감 청산 확인

- **손절(stop loss)**, **익절(take profit)**, **트레일링 스탑(trailing stop)**,
  **장마감 청산(market-close exit)** 은 보유 포지션에 대해 매도(청산) 신호를
  만드는 안전/규칙 장치입니다.
- 청산 사유는 Decision Episode 의 `sell_reason` / 최근 이벤트 로그에서
  확인합니다.
- 참고 점검 문서: `docs/stop_loss_sell_e2e_check.md`,
  `docs/take_profit_sell_e2e_check.md`, `docs/trailing_stop_sell_e2e_check.md`,
  `docs/market_close_exit_sell_check.md`.

## 23. 중복 매수·과다 노출 차단 확인

- 이미 보유한 종목을 또 사려고 하면 `DUPLICATE_POSITION_BUY_BLOCKED` 로 차단될
  수 있습니다 — 이는 **정상 안전장치**입니다.
- 일일 매수 한도를 넘으면 `DAILY_BUY_LIMIT_EXCEEDED`, 종목/섹터 노출이 크면
  위험 가드가 매수를 제한할 수 있습니다.
- 참고: `docs/risk_gate_buy_exposure_check.md`.

## 24. 자주 나오는 메시지와 조치 방법

| 메시지/코드 | 의미 | 사용자가 할 일 |
|---|---|---|
| `MARKET_CLOSED` | 장 시간이 아님 | 장 열린 날 다시 확인 (정상) |
| `NO_MARKET_DATA` | 시세 데이터 없음 | 시세 provider / 네트워크 / 장 시간 확인 |
| `CREDENTIALS_MISSING` | KIS 자격 미설정 | `backend/.env` 자격 4종 확인 |
| `BLOCKED_BY_KIS_READINESS` | KIS 준비상태 미충족 | Settings 탭 KIS 상태 확인 |
| `NO_SIGNAL` | 조건에 맞는 신호 없음 | 정상일 수 있음 |
| `PRICE_STALE` | 현재가가 오래됨 | 시세 갱신 / 네트워크 확인 |
| `ORDER_REJECTED` | 주문 거절 | KIS 모의 화면과 로그 확인 |
| `DUPLICATE_POSITION_BUY_BLOCKED` | 이미 보유 중이라 추가매수 차단 | 정상 안전장치 |
| `DAILY_BUY_LIMIT_EXCEEDED` | 일일 매수한도 초과 | 한도 설정 확인 |
| `INSUFFICIENT_PAPER_CASH` | Paper 현금 부족 | Paper 자금 설정 확인 |
| `EXIT_PLAN_INVALID` | 손절/익절 계획 오류 | 해당 episode 확인 |
| `RISK_FLAGS_EXCEEDED` | 위험 플래그 초과 | 무리한 진입 차단 (정상) |

## 25. 문제가 생겼을 때 Claude Code 에 전달할 정보

문제가 생기면 주문을 계속 실행하지 말고, 아래 정보를 복사해 전달하세요.
**자격정보(시크릿/계좌번호)는 절대 붙여넣지 마세요.**

```
[문제 보고 양식]
- 실행한 EXE commit:
- 발생 시각:
- 장 상태(열림/닫힘):
- Settings KIS 상태(READY/BLOCKED):
- Preflight 결과(PASS/WARN/FAIL):
- 최근 이벤트 로그(10~30줄, 복사 버튼 사용):
- Decision Episode(해당 항목):
- KIS 모의 주문번호(있으면):
- 내가 한 행동(어떤 버튼을 눌렀는지):
- 화면에 보인 오류 메시지:
```

> 최근 이벤트 로그 카드의 **복사** 버튼은 민감정보를 자동으로 가린 채
> 복사합니다. 직접 캡처/타이핑할 때 계좌번호·시크릿이 들어가지 않게 주의하세요.

## 26. 절대 하면 안 되는 행동

- 실전 방향으로 안전 플래그를 바꾸지 않기(섹션 8 참고).
- `.env` 파일이나 자격정보를 GitHub/메신저/캡처로 공유하지 않기.
- 모르는 오류가 반복되는데 자동매매를 계속 돌리지 않기.
- 모의 결과를 실제 수익으로 오해하지 않기.

## 27. 실전 전환 전 조건

실전 전환은 아래 조건이 충족되기 **전에는 검토하지 않습니다**. 그리고 충족되어도
**자동으로 전환되지 않으며**, 운영자의 명시적 수동 승인이 필요합니다.

1. KIS 모의 시세 정상 수신
2. KIS 모의 주문번호 정상 수신
3. fill polling(체결 갱신) 정상
4. KIS 모의 잔고/포지션 동기화 정상
5. 1종목 canary 안정
6. 5일 이상 KIS 모의 안정성 확인
7. 28일 또는 100건 이상 Paper Gate 데이터 확보
8. 최대낙폭 / 연속손실 / 주문실패율 기준 통과
9. Live Capital Review 통과
10. 운영자 수동 승인
11. 실전 canary 별도 승인

> 이 프로그램은 **수익을 보장하지 않습니다.** 위 조건은 "소액 실전 검토
> 가능성" 을 *논의*하기 위한 최소 전제일 뿐이며, 실전 전환은 별도 승인이
> 필요하고 추가 검증이 필요합니다.

## 28. 마지막 점검 체크리스트

- [ ] EXE 가 실행되고 Settings 탭이 보인다.
- [ ] Backend API "연결됨", Sidecar "실행 중".
- [ ] 안전 플래그가 모두 안전값이다(LIVE/AI/FUTURES = false, KIS_IS_PAPER = true).
- [ ] KIS 자격 4종 구성됨(또는 미구성이면 BLOCKED 가 정상임을 이해).
- [ ] Preflight Smoke Test 가 PASS 또는 WARN.
- [ ] 최근 이벤트 로그에 치명적 ERROR 가 반복되지 않는다.
- [ ] 화면/로그/복사 어디에도 시크릿·계좌번호 원문이 없다.
- [ ] (장 열린 날) 시세 수신 · 모의 주문번호 · 체결 상태가 갱신된다.

---

### 관련 문서

- KIS 자격 입력 점검: [`docs/kis_paper_credentials_setup_check.md`](kis_paper_credentials_setup_check.md)
- KIS 모의 자동주문 조합 점검: [`docs/kis_paper_auto_env_combination_check.md`](kis_paper_auto_env_combination_check.md)
- Backend/Sidecar 상태: [`docs/exe_backend_sidecar_status_check.md`](exe_backend_sidecar_status_check.md)
- 앱 버전/빌드: [`docs/exe_build_version_check.md`](exe_build_version_check.md)
- Preflight Smoke Test: [`docs/exe_preflight_smoke_test.md`](exe_preflight_smoke_test.md)
- 최근 이벤트 로그 뷰어: [`docs/exe_runtime_event_log_viewer.md`](exe_runtime_event_log_viewer.md)
- 개발용 실행 환경: [`docs/exe_env_setup.md`](exe_env_setup.md)
