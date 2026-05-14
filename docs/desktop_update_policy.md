# Agent Trader v1 — 데스크톱 자동 업데이트 정책

> 베타테스터에게 매번 새 `.exe` 를 카톡으로 보내는 비효율을 없앤다.
> 앱 안의 "업데이트 확인" 버튼이 새 버전을 발견하면 *서명된* 패치를 받아
> 재시작 후 적용한다.

## 1. 배포 방식

### 1.1 채널

| 채널 | 대상 | tag prefix |
|---|---|---|
| **beta** | 내부 베타테스터 (현 단계) | `v*-beta.*` (예: `v1.0.0-beta.3`) |
| **stable** | 정식 릴리스 (후속) | `v*` (예: `v1.0.0`, `v1.1.0`) |

앱의 `Settings → 업데이트 채널` 토글로 사용자가 채널 변경 가능. 본 PR 시점
default 는 **beta**.

### 1.2 배포 경로

- 모든 데스크톱 artifact 는 **GitHub Releases** 에 업로드.
- artifact 종류:
  - `AgentTrader-v1-Setup-1.0.0.msi` — Windows 정식 설치파일 (msi)
  - `AgentTrader-v1-Setup-1.0.0.exe` — Windows NSIS 설치파일 (대안)
  - `latest.json` — Tauri updater 메타 (버전 / 다운로드 URL / 서명)
  - `*.sig` — 각 패치 파일의 ed25519 서명
- *직접 URL 공유 / 카톡 첨부 / private 드라이브 호스팅 금지* — GitHub
  Releases 가 단일 진실.

## 2. 업데이트 흐름

```
사용자가 앱 안에서 "업데이트 확인" 클릭
   ↓
Tauri updater → latest.json fetch
   ↓
현재 버전 vs 최신 버전 비교 (semver)
   ↓
새 버전 발견 → 다운로드 (.zip / .exe)
   ↓
ed25519 공개키로 서명 검증 → 실패 시 즉시 폐기
   ↓
diff 적용 / installer 실행
   ↓
앱 재시작
```

## 3. 보안 — 서명 키 관리

### 3.1 키 생성 (1회)

```powershell
cargo install tauri-cli@^2
cargo tauri signer generate -w ./agent-trader-private.key
# 결과:
#   ./agent-trader-private.key       ← private key (절대 commit 금지)
#   ./agent-trader-private.key.pub   ← public key (tauri.conf.json 에 commit)
```

### 3.2 키 저장 정책

| 위치 | 내용 | 보안 |
|---|---|---|
| `GitHub Secrets::TAURI_PRIVATE_KEY` | private key 전체 | repository admin 만 읽기 가능 |
| `GitHub Secrets::TAURI_KEY_PASSWORD` | private key 의 password | 동일 |
| `src-tauri/tauri.conf.json::plugins.updater.pubkey` | public key 만 | 모든 사용자가 보는 파일 |
| 개인 워크스테이션 | private key 가 *임시* 존재해도 됨 | 즉시 password manager 로 옮기고 로컬 파일 삭제 |
| 로컬 `.env` / 어떤 commit 도 | **절대 금지** | `.gitignore` 의 `*.key` 패턴이 1차 방어 |

### 3.3 서명 없는 업데이트 금지

- Tauri updater 는 `pubkey` 가 설정돼 있으면 *서명 없는 / 잘못된 서명* artifact 를
  자동 거부.
- 본 PR 시점 `pubkey` 빈 문자열 → updater 자체가 비활성. **실 활성화는 키 생성
  + commit 한 후속 PR 에서만**.
- 베타테스터는 "업데이트 실패" 가 떠도 *수동으로 임의 zip / exe 파일을 받지
  않는다*. fallback 은 항상 *GitHub Releases 의 공식 installer 재다운로드*.

## 4. 버전 규칙 (SEMVER)

| 변경 | 버전 증가 | 예시 |
|---|---|---|
| 버그 수정 / 안전 강화 | PATCH (1.0.x) | 1.0.0 → 1.0.1 |
| 기능 추가 (호환 유지) | MINOR (1.x.0) | 1.0.1 → 1.1.0 |
| 구조 변경 / 안전 정책 변경 / migration 필요 | MAJOR (x.0.0) | 1.1.0 → 2.0.0 |
| 베타 후보 | pre-release | 1.1.0 → 1.2.0-beta.1 → 1.2.0-beta.2 → 1.2.0 |

`frontend/src/config/appInfo.js::APP_INFO.version`,
`frontend/package.json::version`, `src-tauri/tauri.conf.json::version`,
`src-tauri/Cargo.toml::version` 은 **모두 일치**한다. CI 에서 mismatch 면 fail
(향후 lint script).

## 5. 자동 업데이트 트리거 정책

| 조건 | 동작 |
|---|---|
| 앱 시작 시 | 1회 백그라운드 확인 (사용자 동의 없으면 다운로드 X) |
| 사용자가 "업데이트 확인" 버튼 클릭 | 즉시 확인 + 새 버전 있으면 다운로드 / 적용 UI 노출 |
| 12시간 마다 idle 시 | 백그라운드 1회 확인만 (다운로드 사용자 확인 필요) |
| 업데이트 실패 (서명 검증 X / 네트워크) | 친절한 오류 + GitHub Releases URL 안내 |

자동 *적용* 은 절대 하지 않는다 — 사용자가 **"재시작하여 적용"** 버튼을
명시적으로 눌러야 installer 가 실행된다. 운영 중 자동 재시작은 진행 중인
주문 / 결재 큐를 잃을 수 있다.

## 6. 베타테스터 안내 (요약)

- **새 exe 매번 카톡으로 받지 않습니다.** 앱의 `Settings → 업데이트` 카드의
  *"업데이트 확인"* 버튼을 누르세요.
- 새 버전 있으면 자동 다운로드 → "재시작하여 적용" 클릭.
- 업데이트 실패 시:
  1. 네트워크 확인
  2. 그래도 안 되면 GitHub Releases 페이지의 최신 installer 를 직접 받아
     설치 (기존 데이터는 유지됨)
  3. **임의의 zip / exe 를 카톡 / 이메일로 받지 말 것** — 서명되지 않은 파일은
     트로이목마 위험.

자세한 사용자 가이드: [`beta_tester_install_guide.md`](beta_tester_install_guide.md).

## 7. 본 PR 의 범위 / 후속 PR

| 항목 | 본 PR | 후속 PR |
|---|---|---|
| tauri.conf.json + Cargo.toml skeleton | ✅ | — |
| `plugins.updater.active = false` | ✅ | true 로 전환 (key 생성 후) |
| public key commit | ❌ (빈 값) | ✅ (signer generate 결과) |
| private key 저장 | ❌ | GitHub Secrets 만 |
| `latest.json` 생성 | ❌ | `cargo tauri build --target updater` |
| GitHub Actions desktop-release.yml | ✅ (draft, manual trigger only) | tag-trigger 활성화 |
| Frontend "업데이트 확인" UI | ✅ (mock) | Tauri updater API 실 연결 |
| 베타 채널 / stable 채널 토글 | ✅ (UI) | endpoint switching |

## 8. 안전 invariant

- 서명 없는 / 잘못 서명된 update artifact 는 *어떤 경우에도 실행되지 않는다*.
- 자동 다운로드 ≠ 자동 적용. 적용은 항상 사용자 명시 클릭.
- updater 가 *backend 의 `.env` 파일* 을 절대 덮어쓰지 않는다 — secret /
  ENABLE_* flag 는 사용자 설정 그대로 보존.
- 업데이트 중 *결제 / 주문 / 결재 큐* 에 어떤 side effect 도 없다 — installer 가
  실행되는 동안 backend 는 정상 종료, 재시작 후 audit log 그대로 복귀.

## 9. 참고

- [`auto_update_plan.md`](auto_update_plan.md) — 기존 자동 업데이트 큰 그림
- [`beta_distribution_plan.md`](beta_distribution_plan.md) — 기존 베타 배포 계획
- [`desktop_packaging.md`](desktop_packaging.md) — 데스크톱 패키징 결정
- [Tauri v2 Updater 공식 문서](https://tauri.app/plugin/updater/) — 외부 참조
