# Agent Trader v1 — Release Notes

> 베타테스터 / 운영자가 *버전별 변경 내용* 을 한 곳에서 확인할 수 있는 단일
> 진실. GitHub Release 생성 시 해당 버전 섹션 내용을 release body 에 복사
> 권장 (자동 sync 는 후속 PR).
>
> 본 문서는 *고지 / 변경 요약* 이며 **투자 조언이 아닙니다**. 기본 모드는
> SIMULATION / PAPER 이고 실거래 flag 는 항상 false default 입니다.

## 안전 배너 (모든 버전 공통)

- `KIS_IS_PAPER=true` 기본값 유지 — 실거래 자동 활성화 0건
- `ENABLE_LIVE_TRADING=false` / `ENABLE_AI_EXECUTION=false` /
  `ENABLE_FUTURES_LIVE_TRADING=false` 기본값 유지
- installer (setup.exe / msi) 에 사용자 `.env` / API key / 계좌번호 *포함 0건*
- 업데이트는 *앱 코드* (frontend / backend sidecar) 만 갱신 — 사용자 `.env`
  보존
- 베타 시점 SmartScreen 경고 가능 — "추가 정보 → 실행" 으로 진행

---

## v1.0.0 — 2026-05 초기 베타

**범위:** Agent Trader v1 베타 첫 정식 릴리스. KIS 모의투자 one-click test +
EXE 자동 빌드 + Desktop Auto Updater (A 단계).

### 새 기능
- EXE 더블클릭 → backend sidecar 자동 실행 → AI Paper Auto Loop 시작/정지/
  긴급정지 컨트롤 (단일 화면).
- GitHub Actions Windows runner 자동 빌드 (`desktop-release.yml`) — NSIS
  setup.exe 산출물 + 안전 guard.
- Strategy Optimization & Paper Readiness 파이프라인 — 6개 주식 전략 그리드
  서치 + 다중 지표 + 스트레스 테스트 + Paper 후보 추천.
- Feature Flags 다중 잠금 계층 (`app.core.feature_flags`) — 단일 env flag
  만으로 실거래 / AI / 코인선물 활성화 불가.

### 안전 invariant
- broker / OrderExecutor / route_order 호출 0건 (정적 grep + dataclass 가드).
- 자동 주문 트리거 라벨 0개 (button text 전수 검사 테스트로 lock).
- secret 패턴 (`sk-...` / `ghp_...` / `Bearer ...`) release notes 에 발견 시
  `[REDACTED]` 마스킹.

### 알려진 제약
- Tauri auto updater 서명 미활성 — A 단계는 *update 확인 + 수동 다운로드*
  안내만. 자동 설치는 B 단계 (TAURI_PRIVATE_KEY 등록 후) 별도 PR.
- MSI 산출 보류 — WiX 외부 다운로드 503 이슈로 NSIS-only. 향후 PR 에서 WiX
  캐시 step 추가 후 복원.
- KIS LIVE place_order 미구현 (`NotImplementedError`) — 실거래 경로 0건 유지.

### 베타테스터 안내
1. https://github.com/1976haru/autotrade/releases 에서 `*-setup.exe` 다운로드
2. 더블클릭 → SmartScreen 경고 시 "추가 정보 → 실행"
3. 첫 실행 시 `%APPDATA%\Autotrade\` 자동 생성 — 그 안의 `.env` 에 **한투
   모의투자 API 키** 직접 입력 (실거래 키 입력 *절대 금지*)
4. 앱 재시작 → Dashboard → AI Paper Auto Loop 카드 → "시작" 클릭

---

## v1.0.1-beta — (예정)

본 섹션은 v1.0.1 릴리스 시점에 채워짐. 운영자가 PR 머지 후 본 파일을 업데이트.

### 예정 항목
- (TBD) Tauri updater B 단계 활성화 — TAURI_PRIVATE_KEY 등록 후 원클릭 자동
  설치.
- (TBD) MSI 산출 복원 — WiX 캐시 step 추가.
- (TBD) AI Paper Auto Loop tick 실제 strategy 통합 (현 placeholder).

### 포함 (이미 머지)
- **#5-04 Stale popup 방지 가드 강화** — fetch 실패 시 stale welcome / release
  안내가 "최신 업데이트" 처럼 둔갑하지 않도록 `UpdateBanner.jsx` /
  `desktop/updaterClient.js` 가 `../config/releaseNotes` 를 *import 하지 않음*
  을 정적 grep 으로 lock. FAILED state 의 raw `error` 문자열에도 `sanitizeText`
  를 적용해 secret 패턴 노출 0건. 자세한 정책: [`docs/auto_update_policy.md`](auto_update_policy.md) §8.
- **#5-05 GitHub Release ↔ UpdateBanner 연동 contract** — `desktop-release.yml`
  가 (1) `release_tag` SemVer sanitize step (2) `actions/upload-artifact@v4` +
  `if-no-files-found: error` (3) `softprops/action-gh-release@v2` 가 같은
  `bundle/nsis/*-setup.exe` 만 첨부 — `test_repository_hygiene.py` 6개 정적
  가드로 lock. UpdateBanner UPDATE_AVAILABLE 상태에 GitHub Release asset
  의 `browser_download_url` 을 그대로 사용하는 **"setup.exe 직접 받기"** 링크
  추가 (`link-setup-exe-direct`, `target=_blank` + `rel="noopener noreferrer"`).
  asset 부재 / FAILED / UP_TO_DATE 에서는 본 링크 0건. 자세한 정책:
  [`docs/auto_update_policy.md`](auto_update_policy.md) §9.
- **#5-06 Tauri updater Phase 3 준비 (문서 / 테스트만, 활성화 0건)** —
  Phase 3 (`downloadAndInstall()` + `relaunch()`) 전환 8단계 절차 +
  활성화 직전 체크리스트를 [`docs/auto_update_policy.md`](auto_update_policy.md)
  §10 에 신설. `tauri.conf.json::plugins.updater.active=false` /
  `bundle.createUpdaterArtifacts=false` / `pubkey=""` *유지* (변경 0건).
  `desktop-release.yml` Tauri build step 의 `TAURI_SIGNING_PRIVATE_KEY` /
  `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` env 는 *주석 처리* 유지 — 본 PR 시점
  활성 env 0건. 신규 정적 가드 4개로 향후 PR 의 부주의한 활성화 방지
  (`test_tauri_updater_inactive_in_phase2` /
  `test_tauri_create_updater_artifacts_false` /
  `test_no_tauri_private_key_committed` /
  `test_desktop_release_workflow_phase3_signing_inactive`).
- **#5-07 사용자 `.env` 보존 정책 (문서 / 테스트만, 코드 변경 0건)** —
  [`docs/auto_update_policy.md`](auto_update_policy.md) §11 신설 (`.env`
  표준 경로 / 보존 매트릭스 / installer 가드 4-layer / 트러블슈팅 /
  Secret 비노출 invariant / backend-port.json 재생성 안내 / 운영자 백업
  권장). [`docs/exe_oneclick_installation.md`](exe_oneclick_installation.md)
  §13-3 신설 — KIS 키 사라짐 트러블슈팅 표 (`.env.txt` 함정 / AppData 경로 /
  읽기 전용 / 백업 복구). [`docs/desktop_exe_status.md`](desktop_exe_status.md)
  §8-I 신설 — 보존 메커니즘 4 layer 요약. 신규 정적 가드 4개
  (`test_env_preservation_policy.py`): launcher 가 `.env` 에 write/delete
  호출 0건, `updaterClient.js` 가 `.env` / 파일시스템 접근 0건,
  `UpdateBanner.jsx` 가 "사용자 .env 보존" 안전 배지 영구 노출, docs 가
  `%APPDATA%\Autotrade\.env` 표준 경로 명시.

---

## 참고

- [`docs/auto_update_policy.md`](auto_update_policy.md) — 자동 업데이트 정책
- [`docs/desktop_update_policy.md`](desktop_update_policy.md) — 서명 키 관리
- [`docs/exe_oneclick_installation.md`](exe_oneclick_installation.md) — 설치 가이드
- [`docs/desktop_exe_status.md`](desktop_exe_status.md) — EXE 빌드 상태 / 실패 이력
