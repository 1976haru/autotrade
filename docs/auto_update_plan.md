# 자동 업데이트 계획 (Auto Update Plan)

본 문서는 **에이전트 트레이더 v1**의 단계별 업데이트 구현 계획을 정리한다. 매번 .exe를 직접 보내는 방식 대신 *알림 → 자동* 흐름을 단계적으로 도입한다.

## 0. 버전 관리 (SEMVER)

```
1.0.0   v1 첫 공개
1.0.x   버그 수정
1.x.0   기능 추가
2.0.0   구조가 크게 바뀌는 대규모 변경
```

### 단일 진실의 원천

| 위치 | 역할 |
|---|---|
| `frontend/src/config/appInfo.js::APP_INFO.version` | 화면 표시 / Release Notes Modal |
| `frontend/package.json::version` | npm / Vite 빌드 |
| `frontend/src/config/releaseNotes.js` | 버전별 변경사항 / 안전 고지 |
| GitHub release tag (`v1.0.0`) | 배포 channel |
| (미래) Tauri `tauri.conf.json::version` | desktop bundle |

위 4개(+ tauri)는 *반드시 일치*. CI lint로 검증 가능 (별도 PR).

## 1. Phase 1 — 수동 다운로드 (현재)

- 운영자가 GitHub Releases에 새 버전 업로드.
- 베타테스터는 *운영자가 알려준 시점*에 직접 다운로드.
- 앱 자체는 업데이트 *인지 X* — 사용자가 알아서 갱신.

**장점**: 인프라 / Code signing 불필요, 가장 간단.
**단점**: 베타테스터가 새 버전이 있다는 사실을 *모를 수 있음*.

## 2. Phase 2 — 업데이트 알림 (1차 구현 우선순위)

앱 실행 시 *latest version*을 GitHub Releases API로 조회하고, 현재 버전과 다르면 사용자에게 알림.

### 2.1 흐름

```
앱 실행
  ↓
GET https://api.github.com/repos/1976haru/autotrade/releases/latest
  ↓
{tag_name: "v1.1.0", body: "...", assets: [...]}
  ↓
appInfo.version (1.0.0) vs tag_name (1.1.0) 비교
  ↓
다르면 → UpdateBanner 표시 (다운로드 링크 + release notes 보기)
  ↓
사용자가 직접 다운로드 → 설치 → 재실행
```

### 2.2 구현 방식

- frontend hook `useLatestRelease()`:
  - mount 시 1회 GitHub API 호출.
  - 응답 캐시: localStorage 6시간 (rate limit 60 req/hour 회피).
  - 실패 시 *조용히 skip* — 업데이트 알림은 best-effort.

- `<UpdateBanner>` 컴포넌트:
  - 새 버전이 있으면 화면 상단에 노란 banner.
  - "새 버전 v1.1.0이 있습니다 · 다운로드" 링크.
  - 클릭 시 GitHub Release 페이지로 이동 (또는 직접 asset 다운로드).
  - "다음에 알림" 버튼 → localStorage `dismissedUntilVersion=1.1.0`.

- 기존 `<ReleaseNotesModal>` (#2의 PHASE 2)와 *연계*:
  - 새 버전 알림 banner의 "release notes 보기" 버튼이 동일 modal을 연다.
  - `RELEASE_NOTES`가 GitHub Release `body`와 sync되도록 운영자 절차 정의.

### 2.3 Phase 2 구현 시 주의

- **GitHub API rate limit**: 미인증 60 req/hour/IP. localStorage 캐시 + 6시간 간격 호출로 회피.
- **API 오류 무시**: Pages demo / offline / GitHub 장애 시 알림이 안 떠도 정상.
- **자동 다운로드 X**: 사용자가 *명시적*으로 클릭해야 다운로드. 무단 다운로드 / 자동 설치 *금지*.

## 3. Phase 3 — 자동 업데이트 (후속)

### 3.1 Tauri Updater (1순위)

```toml
# tauri.conf.json (예시)
[updater]
active = true
endpoints = [
  "https://github.com/1976haru/autotrade/releases/latest/download/latest.json"
]
pubkey = "..."  # signed update verification
```

- `tauri-plugin-updater`가 `endpoints`에서 manifest를 가져와 비교.
- pubkey로 *서명 검증* — 임의 manifest 주입 방어.
- "업데이트 → 다운로드 → 검증 → 설치 → 재실행"을 한 번에.

### 3.2 Electron `autoUpdater` (대안)

- `electron-updater` (electron-builder 통합).
- GitHub Releases provider 사용.
- code signing 필수.

### 3.3 Phase 3 진입 조건

- [ ] Phase 2 (업데이트 알림) 안정 동작 4주+
- [ ] code signing 인증서 발급 (Windows EV / macOS Developer ID)
- [ ] update manifest 서명 절차 정립 (CI / 빌드 파이프라인)
- [ ] 운영자 옵트인 PR + 베타테스터 동의

## 4. Update Manifest 형식 (Phase 2 / 3 공통)

```json
{
  "version": "1.1.0",
  "release_date": "2026-06-01",
  "title": "에이전트 트레이더 v1.1.0",
  "highlights": [
    "사용자 가이드 강화",
    "긴급중단 banner 개선"
  ],
  "safety_notes": [
    "이 버전은 실거래 자동매매 허가 버전이 아닙니다.",
    "AI 판단은 참고자료이며 최종 책임은 사용자에게 있습니다."
  ],
  "assets": [
    { "platform": "windows-x64", "url": "...", "signature": "..." },
    { "platform": "macos-arm64",  "url": "...", "signature": "..." }
  ]
}
```

본 문서 시점은 *Phase 1*만 운영. Phase 2 manifest는 운영자가 GitHub Release에서 manual로 채울 수 있으나, Phase 3 진입 시 빌드 파이프라인이 자동 생성.

## 5. 보안 — Update 흐름의 절대 원칙

| 원칙 | 가드 |
|---|---|
| **임의 update manifest 주입 방어** | Phase 3에서 pubkey 서명 검증. Phase 2는 GitHub API HTTPS만 신뢰 |
| **외부 호스트 update 금지** | GitHub Releases / 운영자가 신뢰하는 서명된 endpoint만. ngrok / 임의 CDN 사용 X |
| **자동 다운로드 / 설치는 사용자 명시 동의 후** | Phase 2는 사용자가 link 클릭해야 다운로드. Phase 3는 명시 동의 prompt 후 진행 |
| **버전 downgrade 차단** | 더 낮은 버전 manifest는 무시 (rollback 공격 방어) |
| **Code signing 검증** | Phase 3 필수. Tauri / Electron updater가 자동 처리 |

## 6. 운영자 release 절차

```bash
# 1. 코드 변경 + 테스트
npm test
npm run build

# 2. 버전 업데이트 (3곳 모두 일치)
#    - frontend/package.json
#    - frontend/src/config/appInfo.js::APP_INFO.version
#    - frontend/src/config/releaseNotes.js (새 entry 추가)

# 3. commit + tag
git add .
git commit -m "chore(release): v1.1.0"
git tag v1.1.0
git push origin main --tags

# 4. GitHub Releases에 새 release 생성 (수동)
#    또는 GitHub Actions가 tag push 시 자동 생성 (별도 PR)
```

## 7. 후속 backlog

- **CI lint**: package.json + appInfo.js + releaseNotes.js 버전 일치 검증
- **GitHub Actions release workflow**: tag push 시 build + asset 업로드
- **Tauri updater 통합** (Phase 3)
- **Electron 대안 PoC** (Tauri 적용 어려운 경우)
- **Code signing 인증서** + 빌드 파이프라인 통합
- **베타테스터 dismiss 정책** — "이 버전은 알림 끄기" → `dismissedUntilVersion` localStorage

## 관련 문서

- [`deployment_strategy.md`](deployment_strategy.md) — 전체 배포 정책
- [`beta_distribution_plan.md`](beta_distribution_plan.md) — 베타 단계 계획
- [`local_security_policy.md`](local_security_policy.md) — 보안 정책
- `frontend/src/config/releaseNotes.js` — 버전별 변경사항 (단일 출처)
- `frontend/src/components/common/VersionBadge.jsx` — release notes modal
- `CLAUDE.md` — 절대 원칙 4-5
