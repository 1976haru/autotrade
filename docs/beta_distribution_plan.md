# 베타 배포 계획 (Beta Distribution Plan)

본 문서는 **에이전트 트레이더 v1**의 베타테스터 배포 정책 + 단계별 업데이트 계획을 정리한다. 핵심:

1. **베타테스터는 각자 자기 PC에 설치형 앱으로 실행** — 운영자가 매번 .exe를 보내지 않는다.
2. **각자 자기 API Key 입력** — 운영자의 .env / 자격증명을 *절대 포함하지 않는다*.
3. **기본 SIMULATION** — LIVE 기능 비활성. 베타 테스트는 검증 단계.
4. **단계적 업데이트** — 1단계 수동 → 2단계 알림 → 3단계 자동.

## 1. 절대 금지 — 운영자 자격증명 포함 금지

| 금지 행위 | 사유 |
|---|---|
| **내 .env를 베타테스터에게 전달** | API Key / Secret / 계좌번호가 그대로 노출됨 |
| **빌드된 앱에 자격증명 inline** | 배포물 reverse-engineering으로 탈취 가능 |
| **공통 API Key를 다수 베타테스터가 공유** | 호출 제한 / 책임 추적 불가 |
| **배포 채널 (URL / Discord / 카카오톡 단톡)** | 모르는 사람이 배포물 탈취 가능 — 베타테스터에게 *직접* 전달 |

베타테스터는 **각자 KIS 모의투자 계정 / API Key를 발급받아** 자기 PC `.env`에 입력. 운영자는 *템플릿 .env.example*만 제공.

## 2. 단계별 업데이트 계획

### Phase 1 — 수동 다운로드 (현재 / 가까운 시점)

- 운영자가 GitHub Releases에 .zip / .exe 업로드.
- 베타테스터는 직접 다운로드 → 압축 해제 → 실행.
- 업데이트는 베타테스터가 *수동*으로 새 버전 다운로드.
- 단점: 베타테스터가 "최신 버전이 있는지" 인지 못 함.
- **장점**: 가장 간단 / Code signing 불필요 / 인프라 없음.

### Phase 2 — 업데이트 알림 (Phase 1 + α)

- 앱 실행 시 GitHub Releases API (`GET /repos/1976haru/autotrade/releases/latest`) 호출.
- 현재 앱 version과 비교 → 새 버전이 있으면 운영자에게 *알림 banner*.
- 업데이트 자체는 여전히 *수동* (다운로드 링크 표시).
- 본 phase는 [`auto_update_plan.md`](auto_update_plan.md)와 연계 — 1차 구현 우선순위.

### Phase 3 — 자동 업데이트 (후속)

- Tauri의 [`tauri-plugin-updater`](https://tauri.app/v2/plugin/updater/) 또는 Electron `autoUpdater`로 자동 다운로드 + 재실행.
- code signing 필수 (Windows SmartScreen / macOS Gatekeeper 통과).
- 본 phase는 *후속* — beta 안정화 후 진행.

## 3. 데스크톱 앱 패키징 — Tauri 우선

본 PR은 *계획 문서*만 작성 — 실제 패키징은 별도 옵트인 PR.

### 3.1 Tauri (1순위)

- 장점:
  - bundle 크기 ~10MB (Electron 대비 1/15)
  - 메모리 사용량 적음
  - Rust 기반 — security가 더 강함
  - 내장 updater plugin 존재
- 단점:
  - 학습 곡선 (Rust)
  - 일부 deep web API 미지원

### 3.2 Electron (2순위 / 대안)

- 장점:
  - 광범위한 생태계 (Slack / Discord / VS Code 채택)
  - Node.js full API
- 단점:
  - bundle 크기 ~150MB (Tauri 대비 큼)
  - 메모리 사용량 큼
  - 보안 surface 더 넓음

### 3.3 PWA (옵션)

- 본 시점 GitHub Pages가 PWA-friendly하나 backend 호출이 필요한 운영 시나리오에는 부적합.
- 모바일 *관제* 용도로는 충분 — install prompt 추가만 별도 작업 (backlog).

## 4. 배포 채널 — GitHub Releases

```
https://github.com/1976haru/autotrade/releases
```

- 운영자가 새 버전 빌드 → `git tag v1.1.0` → push → GitHub Actions가 `npm run build` + `tauri build` → asset (.zip / .exe / .dmg) 자동 생성.
- Release Notes는 [`frontend/src/config/releaseNotes.js`](../frontend/src/config/releaseNotes.js) 내용을 그대로 복사 (Phase 2의 release notes modal과 동일 출처).
- Release tag와 `appInfo.js::APP_INFO.version`은 *반드시 일치*해야 한다 — CI lint로 검증 가능.

### Code signing (후속)

- Windows: EV Code Signing Certificate (~$300/year). SmartScreen "알 수 없는 게시자" 경고 회피.
- macOS: Apple Developer ID (~$99/year). Gatekeeper 통과.
- 본 시점 *backlog* — 베타 안정화 후 별도 PR.

## 5. 베타테스터 onboarding (수동)

운영자가 베타테스터에게 *직접* 전달:

1. **GitHub Release 링크** + 다운로드 가이드.
2. **`.env.example` 사본** + KIS 모의투자 계정 발급 가이드 (운영자 .env 사본은 *절대 X*).
3. **사용자 가이드** (`docs/user_guide_a4.md`) — 사전 학습.
4. **첫 실행 시 SIMULATION 모드 강제** — 본 시점 default 안전 설정.
5. **Slack / Discord / 1:1 채널** — 사용 중 발생하는 질문 / 오류 공유 (별도 채널, 자격증명 절대 미공유).

## 6. 베타테스터 안전 정책

| 정책 | 내용 |
|---|---|
| **기본 모드** | `DEFAULT_MODE=SIMULATION` 강제 — `.env.example` 기본값 |
| **LIVE flag default off** | `ENABLE_LIVE_TRADING=false` / `ENABLE_AI_EXECUTION=false` / `ENABLE_FUTURES_LIVE_TRADING=false` |
| **운영자 .env 미포함** | 빌드 / 배포 산출물에 운영자 자격증명 0건 |
| **각자 KIS 모의투자 계정** | 베타테스터는 자기 명의로 KIS 모의투자 발급 |
| **실거래 활성화** | 베타 테스트 기간에는 *원칙적으로 비활성*. 활성화 옵트인 시 운영자 명시 협의 + promotion_policy.md 8개 조건 모두 통과 |
| **버그 / 사고 보고** | feedback modal (mailto / 클립보드 복사 — secret 미포함). raw audit log 공유 시 운영자가 redaction 후 검토 |

## 7. 베타테스터 종료 / 정식 배포 시점 검토

다음 조건이 모두 충족되어야 *베타 → 정식 배포*:

- [ ] 베타 기간 ≥ 4주 (실 시장 환경 노출)
- [ ] 핵심 시나리오 (Mock / Paper / Manual Approval / AI Assist) 무사고
- [ ] 베타테스터 리포트의 critical 이슈 0건
- [ ] code signing 인증서 발급 + 검증 완료
- [ ] `auto_update_plan.md` Phase 2 (업데이트 알림) 작동 확인
- [ ] `local_security_policy.md`의 모든 정책 lint / runtime 가드 통과
- [ ] 정식 배포 시 *운영자 옵트인* PR로 LIVE 활성화 가능 (default는 여전히 off)

## 관련 문서

- [`deployment_strategy.md`](deployment_strategy.md) — 전체 배포 정책
- [`auto_update_plan.md`](auto_update_plan.md) — 단계별 업데이트 구현 계획
- [`local_security_policy.md`](local_security_policy.md) — 보안 정책
- [`mobile_access_guide.md`](mobile_access_guide.md) — 폰 접속 가이드
- [`promotion_policy.md`](promotion_policy.md) — LIVE 활성화 8개 조건
- `CLAUDE.md` — 절대 원칙 1-6
