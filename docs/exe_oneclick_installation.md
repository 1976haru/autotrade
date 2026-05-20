# EXE 원클릭 설치 가이드 — 베타테스터 / 지인 배포 (#90)

> **이 가이드는 한투 모의투자 전용입니다. 실제 돈이 나가지 않습니다.**
> 실거래(LIVE) 활성화는 본 EXE 의 범위가 *아니며*, 별도 운영자 옵트인 PR
> 이후에만 가능합니다.

## 1. 한 줄 요약

1. `AgentTrader-v1-Setup.exe` 더블클릭 → 설치
2. 바탕화면 / 시작메뉴의 **Agent Trader v1** 실행
3. 처음 한 번만 `%APPDATA%\Autotrade\.env` 에 한투 *모의투자* API 키 입력
4. 앱 안에서 **"준비상태 확인"** → **"한투 모의 빠른 점검 시작"**

> 터미널 / PowerShell / `uvicorn` 명령어를 직접 입력할 필요 없습니다.

## 2. 시스템 요구

| 항목 | 최소 | 권장 |
|---|---|---|
| OS | Windows 10 64-bit | Windows 10 / 11 64-bit |
| 디스크 | 200 MB | 500 MB (로그 포함) |
| 메모리 | 2 GB | 4 GB |
| 네트워크 | 한투 모의투자 API 접근 가능 | 동일 |
| 별도 의존성 | **없음** (Python / Node 설치 불필요 — EXE 안에 포함) | — |

## 3. 설치 파일 위치

본 PR 시점 기준 설치 파일이 *이미 빌드된 경우*:

```
src-tauri/target/release/bundle/msi/Agent Trader v1_1.0.0_x64_en-US.msi
src-tauri/target/release/bundle/nsis/Agent Trader v1_1.0.0_x64-setup.exe
```

베타테스터는 보통 **GitHub Release** 의 첨부 파일에서 받습니다:

```
https://github.com/1976haru/autotrade/releases/latest
```

> #5-05: 본 URL 은 앱 안 `UpdateBanner` 가 *유일하게 신뢰하는 진실 소스* 입니다.
> Release draft 가 *publish* 되면 `UpdateBanner` 가 다음 사용자 접속 시 자동으로
> 새 버전을 안내하고, `assets[].browser_download_url` 을 그대로 사용해 카드
> 안에 **"setup.exe 직접 받기"** 링크를 노출합니다 (target=\_blank,
> noopener/noreferrer). Release 가 없거나 조회 실패 시에는 **"최신 버전 확인 불가"**
> 로만 표시되며, 오래된 release note 가 *최신 업데이트* 처럼 둔갑하지 않습니다.

### 3-1. (운영자) GitHub Actions 자동 빌드로 installer 만들기

로컬 PC 에 Rust / WiX 가 없어도 **GitHub Actions Windows runner** 에서 자동
빌드 가능 (`.github/workflows/desktop-release.yml`):

1. 저장소 GitHub 페이지 → **Actions** 탭
2. 좌측 workflow 목록에서 **desktop-release** 선택
3. 우측 상단 **Run workflow** 클릭
4. 입력:
   - `release_tag`: 예) `v1.0.1-beta.1`
   - `draft`: true (운영자가 수동 publish)
   - `create_release`: true (GitHub Release draft 생성) / false (Actions
     artifact 만)
5. **Run workflow** 클릭 → 30~45분 후 완료
6. Actions 실행 페이지 하단 *Artifacts* 에서 `agent-trader-windows-installer-{tag}.zip`
   다운로드 → 압축 해제 → `.msi` / `-setup.exe` 사용

자세한 정책: [`docs/desktop_exe_status.md`](desktop_exe_status.md) §8-C.

> 본 시점에 위 빌드가 아직 *없을 수* 있습니다. 그 경우 `docs/desktop_exe_status.md`
> §1 "한 줄 결론" 을 먼저 확인하세요. 빌드가 안 되어 있어도 §6 의 대체 흐름
> (script + 브라우저) 으로 동일한 모의 테스트가 가능합니다.

## 4. 설치 방법

### 4-1. `.msi` 또는 `.exe` 더블클릭

- **MSI 권장** (`Agent Trader v1_1.0.0_x64_en-US.msi`)
  - Windows Installer 가 시작메뉴 + 바탕화면 아이콘을 자동 생성
- **NSIS 대체** (`Agent Trader v1_1.0.0_x64-setup.exe`)
  - 설치 폴더를 직접 선택할 수 있는 안내 wizard

### 4-2. Microsoft Defender / SmartScreen 경고

본 베타 빌드는 *코드 서명 인증서가 아직 없을 수* 있습니다. 다음 안내가 뜨면:

```
Microsoft Defender SmartScreen이 인식할 수 없는 앱의 시작을 차단했습니다.
```

→ **추가 정보 (More info)** → **실행 (Run anyway)** 클릭.

> 본 단계가 불편하지만 *정상 동작입니다*. 본 베타가 일정 사용자 수를 넘으면
> SmartScreen 평판이 자동 누적되어 경고가 사라집니다. 코드 서명은 후속 PR.

## 5. 첫 실행 흐름

설치 후 **Agent Trader v1** 을 실행하면 다음 순서로 자동 진행됩니다:

```
1. 앱 메인 윈도우가 열림
2. 백엔드 sidecar (autotrade-backend.exe) 가 자동으로 spawn → 127.0.0.1:8000
3. 대시보드의 "한투 모의투자 AI 자동매매 테스트" 카드가 backend 와 연결
4. 카드 상단에 "백엔드 연결 완료" 표시
5. 사용자가 [1. 준비상태 확인] → [2. 한투 모의 빠른 점검 시작] 클릭
```

> 백엔드를 따로 켜는 BAT / PowerShell 작업이 **필요 없습니다**.

## 6. 한투 모의투자 API 설정 (처음 한 번만)

KIS 모의투자 키가 없어도 **내부 Mock 고속 스트레스** 는 그대로 가능합니다.
하지만 *실제 한투 모의투자* 흐름을 검증하려면 다음 한 번의 설정이 필요합니다.

### 6-1. 설정 파일 위치

```
%APPDATA%\Autotrade\.env
```

(Windows 탐색기 주소창에 위 경로를 그대로 붙여넣으세요. `%APPDATA%` 는
보통 `C:\Users\<사용자>\AppData\Roaming` 입니다.)

폴더가 없으면 직접 만드세요. 폴더 안에 `.env` 라는 *텍스트 파일* 을
만들고 아래 내용을 채웁니다 (Notepad / VS Code 등).

### 6-2. `.env` 예시 (한투 *모의투자* 전용)

```dotenv
# 안전 flag — 베타 시점 변경 금지
KIS_IS_PAPER=true
ENABLE_LIVE_TRADING=false
ENABLE_AI_EXECUTION=false
ENABLE_FUTURES_LIVE_TRADING=false
DEFAULT_MODE=PAPER

# 한투 모의투자 키 — https://apiportal.koreainvestment.com 모의투자 발급
KIS_APP_KEY=여기에_모의투자_APP_KEY
KIS_APP_SECRET=여기에_모의투자_APP_SECRET
KIS_ACCOUNT_NO=여기에_모의투자_계좌번호
```

### 6-3. 절대 하지 말 것

- **실거래 계좌의 키를 입력하지 마세요.** 베타 본 PR 시점에는
  `ENABLE_LIVE_TRADING=true` 가 코드 단에서 차단되지만, 모의/실거래 키를
  섞어서 보관하면 추후 사고 가능성이 높습니다.
- **`.env` 를 다른 사람에게 보내지 마세요.** 모의투자 키라도 본인 계정에
  연결돼 있습니다.
- **앱 안에서 키를 입력하지 *마세요*.** 프론트엔드에는 키 입력 폼이 *없습니다*.
  키가 필요하면 `.env` 파일 한 곳만 사용합니다.

### 6-4. 키 변경 후 반영

`.env` 를 저장한 뒤 앱을 **종료 후 다시 실행** 하면 새 키가 적용됩니다.

## 7. "준비상태 확인" 방법

### 7-1. KIS Paper test 카드의 준비상태 확인

1. 앱 메인 윈도우 → 상단 좌측 **🧪 한투 모의투자 AI 자동매매 테스트** 카드
2. **[1. 준비상태 확인]** 버튼 클릭
3. 카드에 다음이 표시됩니다:
   - 현재 모드 (SIMULATION / PAPER / ...)
   - `KIS_IS_PAPER` (true 여야 안전)
   - 실거래 차단 (✓ 비활성 = 안전)
   - AI 자동 실행 차단 (✓ 비활성 = 안전)
   - KIS Key 입력됨 (✓ 입력됨 / ❌ 미입력)
   - KIS Paper 모드 가능 (✓ / ❌)
   - Mock 모드 가능 (✓ / ❌)
4. 안내 메시지에 노란색 텍스트가 있으면 그 안내를 따라 `.env` 를 수정.

### 7-2. **#91 — Pre-market Checklist 카드 확인 (권장)**

대시보드의 **Pre-market Checklist (#80 / #91)** 카드는 KIS Paper test 카드와
*별개*로 *전체 자동매매 시스템*의 안전 상태를 점검합니다. 모드별 헤드라인:

| Verdict | 의미 | One-click test 시작 가능? |
|---|---|---|
| `READY_TO_START` (녹색 — "오늘 자동운용 가능") | 모든 required 항목 PASS | ✅ 가능 |
| `WARN_BUT_START_ALLOWED` (주황 — "주의 필요") | required PASS + WARN 존재 | ✅ 가능 (운영자 검토 후) |
| `DO_NOT_START` (빨강 — "시작 금지") | required FAIL 1건 이상 | ❌ 차단 — One-click test 카드의 시작 버튼이 모두 disabled |

`DO_NOT_START` 일 때 카드 안에 **초보자 안내 블록**이 나타나며, `backend/.env`
의 4개 안전 flag (`KIS_IS_PAPER=true` / `ENABLE_LIVE_TRADING=false` /
`ENABLE_AI_EXECUTION=false` / `ENABLE_FUTURES_LIVE_TRADING=false`) 를 확인하는
체크리스트가 표시됩니다. 변경 후 backend 를 재시작하고 "다시 점검" 버튼을 누르세요.

자세한 정책은 [`docs/pre_market_checklist.md`](pre_market_checklist.md) 참조.

## 8. "한투 모의 빠른 점검 시작" 방법

준비상태가 **✓ 가능** 으로 표시되면:

1. **[2. 한투 모의 빠른 점검 시작]** 클릭
2. 노란색 *확인 모달* 표시 — "⚠ 모의투자 테스트 시작 확인"
3. **[모의투자 주문 테스트 시작]** 클릭
4. 카드 하단에 결과판 (AI 판단 / 주문 / 체결 / 거절 / 리스크 차단) 실시간 표시
5. 자동으로 끝나면 **점수판** (0~100) + grade label 표시

> 매수 / 매도 버튼이 따로 *없습니다*. AI 가 알아서 판단하고, 사용자는 결과만 봅니다.

## 9. 결과 보는 법

| 항목 | 의미 |
|---|---|
| **AI 판단 횟수** | AI 가 시장을 본 횟수 |
| **AI 매수 / 매도 신호** | AI 가 매수 또는 매도를 *제안* 한 횟수 (실거래 X, 모의 주문 X — *집계만*) |
| **모의 주문 시도** | RiskManager 까지 도달한 주문 수 |
| **모의 주문 실행** | RiskManager 통과 + broker 에 보낸 수 |
| **거절** | RiskManager / OrderGuard 가 차단 |
| **체결** | broker 가 체결 응답을 보낸 수 |
| **리스크 차단** | 일일 손실 / 노출 / 중복 등으로 차단 |
| **오류** | API timeout / 데이터 stale 등 |
| **점수** | 0~100 — 60 이상이면 흐름 정상 작동 |

> **점수가 높다고 실거래 가능을 의미하지 않습니다.** 점수는 *시스템 건강도*
> 이지 *수익률 보장* 이 아닙니다.

## 10. 오류별 해결법

| 화면 표시 | 원인 | 해결 |
|---|---|---|
| "백엔드 연결 중" 이 30초 넘게 지속 | sidecar 가 spawn 되지 않음 | 앱 종료 후 재실행 |
| "백엔드 실행 실패" | sidecar exe 가 없거나 손상 | `scripts/build_backend_sidecar.ps1` 재실행 |
| "안전 flag 위반 — 모의 테스트 차단" | `.env` 에 `ENABLE_LIVE_TRADING=true` 또는 `ENABLE_AI_EXECUTION=true` 가 들어감 | `.env` 에서 `false` 로 변경 후 앱 재시작 |
| "한투 모의투자 API 설정 필요" | KIS 키 비어 있음 | §6 의 `.env` 채우기 (혹은 mock 모드 사용) |
| "확인 모달 통과 전 backend 호출 없음" | 정상 — 사용자가 *반드시* 확인 모달의 [모의투자 주문 테스트 시작] 을 눌러야 진행 | 클릭만 하세요 |
| 점수가 0 또는 매우 낮음 | KIS API rate limit / 데이터 stale / 오류 다수 | 1~2분 후 재시도, 오류 카운터 확인 |
| 상단 배너 "ℹ️ 최신 버전 확인 불가" | GitHub Release 조회 실패 (네트워크 차단 / GitHub rate limit / 사내 방화벽) | "다시 시도" 클릭, 또는 [GitHub Release 페이지 열기] 로 직접 확인 — *백엔드 연결 실패와는 별개 항목* (#5-04) |
| "최신 버전 확인 불가" 와 "백엔드 연결 안 됨" 이 동시에 노출 | 두 상태는 *서로 다른 원인* — 각각 별도로 표시됨 | 백엔드 → §10 의 "백엔드 연결 중" 행 참고. 업데이트 확인 → 위 행 참고 |

## 11. 실제 돈이 나가지 않는 이유 (다층 안전)

본 EXE 는 다음 *4개* 단계에서 실거래를 차단합니다 — 한 곳만 통과해도 못 나갑니다.

1. **`.env`** — 베타 기본값은 `KIS_IS_PAPER=true`, `ENABLE_LIVE_TRADING=false`
2. **Backend RiskManager** — `KIS_IS_PAPER=false` 면 모든 주문 REJECTED
3. **KIS Broker Adapter** — `is_paper=False` 호출 시 `NotImplementedError`
4. **UI 카드** — "지금 매수" / "Place Order" / "실거래 시작" 버튼이 *코드 단에서 0개*

자세한 보호 매트릭스: [`docs/promotion_policy.md`](promotion_policy.md),
[`docs/risk_policy.md`](risk_policy.md).

## 12. 지인 배포 시 주의사항

본 EXE 를 친구 / 가족에게 전달할 때:

- **`.env` 는 *복사하지 마세요*.** 받는 사람이 본인 모의투자 키를 발급받게 안내.
- 코드 서명이 없으면 SmartScreen 경고 발생 — 위 §4-2 안내 같이 전달.
- 받는 사람이 *실거래* 를 시도할 의도가 있으면 본 EXE 를 주지 마세요.
  본 PR 시점 EXE 는 모의 전용입니다.
- "AI 가 자동으로 돈 벌어준다" 는 *오해를 일으키지 마세요*. 본 시스템은 손실 방어
  + 운영 검증 + 모의 흐름을 위한 *연구 플랫폼* 입니다.

## 13. 삭제 방법

### 13-1. 앱 제거

- Windows **설정 → 앱 → Agent Trader v1 → 제거**
- 또는 시작메뉴에서 마우스 우클릭 → 제거

### 13-2. 사용자 데이터 (선택)

```
%APPDATA%\Autotrade\.env       # 운영자 키 — 필요 없으면 직접 삭제
%APPDATA%\Autotrade\logs\      # 로그 파일 — 필요 없으면 직접 삭제
```

> 위 폴더는 *앱 제거로 자동 삭제되지 않습니다* — 의도된 동작입니다.
> 운영자의 `.env` 가 우발적으로 사라지지 않도록 보존됩니다.

## 14. 로그 파일 위치

| 종류 | 경로 |
|---|---|
| backend sidecar 로그 | `%APPDATA%\Autotrade\logs\backend-YYYYMMDD.log` |
| 프론트엔드 콘솔 | 앱 우측 상단 > 메뉴 > 개발자 도구 (Dev 빌드 한정) |
| Tauri Rust 로그 | `%APPDATA%\Autotrade\logs\tauri.log` (향후 추가 예정) |

> 로그 파일에는 **Secret 원문이 포함되지 않습니다** — 키 존재 여부 (`present`
> / `missing`) 만 기록됩니다.

## 15. 자주 묻는 질문

**Q. 인터넷이 끊기면 어떻게 되나요?**
A. 한투 모의투자 API 호출이 timeout 되며, 카드의 "오류" 카운터가 올라갑니다.
   실제 돈은 *어떤 경우에도* 나가지 않습니다.

**Q. 다른 PC 로 설정을 옮기려면?**
A. `%APPDATA%\Autotrade\.env` 한 파일만 복사하면 됩니다. 앱 자체는 새로 설치.

**Q. 자동 업데이트는?**
A. **A 단계 (feature/desktop-auto-updater) 추가됨** — 앱 실행 시 Dashboard 상단
   `UpdateBanner` 가 GitHub Release `latest` 를 자동 조회. 새 버전이 있으면
   변경 내용 + "업데이트 적용" 버튼 노출 (현재 *수동 다운로드 페이지 열기*
   동작). **자동 설치** 는 B 단계 (TAURI_PRIVATE_KEY 등록 후) 별도 PR.
   업데이트는 *앱 코드* 만 갱신하며 사용자 `.env` 는 *보존* 됩니다. 자세한 정책:
   [`docs/auto_update_policy.md`](auto_update_policy.md), 변경 내용:
   [`docs/release_notes.md`](release_notes.md).

**Q. macOS / Linux 빌드는?**
A. 본 PR 시점 *Windows x64 만*. 코드는 cross-platform 이지만 sidecar PyInstaller
   빌드 + tauri target triple 추가가 후속 PR.

## 16. 참고

- [`docs/desktop_exe_status.md`](desktop_exe_status.md) — 빌드 산출물 *현재* 상태
- [`docs/desktop_packaging.md`](desktop_packaging.md) — 패키징 설계 (#86)
- [`docs/desktop_update_policy.md`](desktop_update_policy.md) — 업데이트 정책 (#86)
- [`docs/beta_tester_install_guide.md`](beta_tester_install_guide.md) — #86 일반 설치 가이드 (본 #90 가이드의 상위 흐름)
- [`docs/kis_paper_oneclick.md`](kis_paper_oneclick.md) — #89 KIS 모의 one-click 정책
- [`docs/promotion_policy.md`](promotion_policy.md) — 단계별 승격
- [`docs/risk_policy.md`](risk_policy.md) — RiskManager 가드
