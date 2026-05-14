# 현재 알려진 위험 (Known Risks) — 2026-05

> 본 문서는 현재 main 상태의 *알려진 위험 / 미완성 영역 / 잠재적 회귀* 를
> 솔직하게 나열한다. 본 위험들은 사용자 / 베타테스터 / 후속 PR 담당자가 미리
> 인지해야 한다.

## 1. 배포 / 인프라

### 1.1 GitHub Pages 배포 구조의 복잡성

`.github/workflows/pages-deploy.yml` 이 *세 가지 경로* 를 동시에 보유:
1. `actions/deploy-pages@v4` (Source = GitHub Actions 모드)
2. `peaceiris/actions-gh-pages@v4` → `gh-pages` 브랜치 force-push
3. `sync-main-root` job — `dist` 산출물을 `main` root 에 자동 commit

위 셋이 **병행**하며, 운영자가 GitHub Pages Settings 의 Source 토글을 바꿔도
어느 한쪽이 동작하도록 설계됐다. 그러나:

- `main` root 에 `index.html` / `404.html` / `assets/` / `.nojekyll` 가 *commit
  된 상태* — 소스 코드와 빌드 산출물이 같은 트리에 섞임.
- "[skip ci]" + path filter 로 무한 루프는 막혀 있지만, *관찰 가능성* 이 낮다.
- 새 contributor 가 trade-off (소스 vs artifact) 를 즉시 이해하기 어려움.

**후속 cleanup 권장**:
- 장기적으로 *gh-pages branch only* 로 단순화 (option 2)
- `main` 에는 소스 코드만 — `index.html` / `assets/` 를 root 에서 제거
- 본 작업은 별도 PR (이번 #88 시점 변경 안 함 — 운영 중단 위험)

### 1.2 Tauri desktop installer 빌드 미검증

#86 의 `src-tauri/` 는 *skeleton 만* commit. 다음이 후속 PR 필요:
- 실제 `cargo tauri build` 검증 (Windows runner 에 Rust 툴체인 + tauri-cli)
- `tauri signer generate` 결과 public key commit
- PyInstaller backend sidecar 빌드
- icons 실 binary 채움
- `.github/workflows/desktop-release.yml` 의 `workflow_dispatch` → `tag: 'v*'`
  트리거 활성화

본 PR 시점 `plugins.updater.active=false` / `pubkey=""` 이므로 자동 업데이트
자체가 비활성 — 안전 baseline.

## 2. dependency 관리

### 2.1 lock 파일 부재

- frontend `package-lock.json` 은 존재 ✅
- **backend `requirements.lock.txt` / `uv.lock` 부재** ❌

같은 머신 / CI 에서 매번 동일한 transitive 의존성을 보장하기 어렵다.
새 보안 패치 / 호환성 회귀가 어느 PR 에서 처음 들어왔는지 추적하기 힘듦.

**완화책 (현재)**:
- `backend/requirements.txt` 가 major version 만 핀 (`fastapi>=0.115` 등)
- CI 가 매 push 마다 새 install 후 테스트 통과 확인

**후속**: Paper 운영 *전* `requirements.lock.txt` 또는 `uv` 도입 필수.
자세한 정책: [`docs/dependency_policy.md`](../dependency_policy.md).

### 2.2 major version 상한 미설정

`requirements.txt` 가 `>=` 만 사용:
```
fastapi>=0.115
sqlalchemy>=2.0
pydantic>=2.8
```

대규모 메이저 버전 변경 (예: SQLAlchemy 3.0 출시) 시 *자동으로 새 major* 가
들어와 호환성을 깨뜨릴 위험. 안전한 형태는 `fastapi>=0.115,<1.0` 같은 상한
명시. **단, 이번 #88 시점에는 의도적으로 변경하지 않음** (대규모 의존성
변경은 별도 PR + 회귀 테스트 필요).

## 3. 운영 데이터 / 검증

### 3.1 Paper 운영 데이터 축적 부족

`#72 Paper Gate` 가 PASS 되려면:
- ≥28일 운영
- ≥100건 paper 주문
- expectancy > 0, PF ≥ 1.2, MDD ≤ 15%

현재 운영자 PC 에 paper 운영 기록이 *충분히 누적되지 않음*. 운영 시작 후
실시간으로 audit 되어야 하며, 본 baseline 없이는 Live Manual Approval (#73)
검토조차 불가.

### 3.2 ShadowTrade 장중 검증 부족

`LIVE_SHADOW` 모드의 ShadowTrade 추정 기록 (#43) 이 실제 KIS 시세 + audit
필드 carry 가 되는지 *장중* 검증 부족. invariant
`actual_broker_order_sent=False` 는 코드 단에서 lock 되어 있지만, 실 시세
조건에서의 *추정 정확도* 측정 미완.

### 3.3 KIS API 실 연결 미검증

`KIS_APP_KEY` / `KIS_APP_SECRET` / `KIS_ACCOUNT_NO` 가 *빈 값 default*. 실
연결은 운영자가 개인 환경에서만 가능 — CI / staging 에서 검증 0건.
LIVE 활성화 *전* 다음이 필요:
- 실 KIS 모의투자 API 응답을 받는 통합 테스트
- rate limit (`KIS_RATE_LIMIT_CALLS=5, WINDOW=1.0s`) 검증
- 모의투자 ↔ 실거래 응답 schema 동일성 (KIS 공식 문서 + 운영자 hand-test)

## 4. CI / 테스트 안정성

### 4.1 사전 환경 실패 (7건)

#87 audit 시점에서 확인된 *환경 의존 실패* (`pytest -q` 전체 실행):
- `test_ai_routes.py::test_analyze_persists_default_mode_on_audit_row`
- `test_ai_routes.py::test_analyze_audit_row_carries_mode_even_on_provider_error`
- `test_brokers_kis_stub.py::test_constructor_reads_settings_credentials_when_unset`
- `test_brokers_kis_stub.py::test_get_price_raises_when_no_credentials`
- `test_data_quality.py::test_cli_runs_with_format_json`
- `test_mvp_completion_doc.py::test_summary_script_secret_check_exits_clean`
- `test_routes.py::test_status_exposes_safety_flags`

원인: 일부 테스트가 `DEFAULT_MODE` 환경변수가 `SIMULATION` 임을 가정하지만
실제 runner 가 `PAPER` 를 주입 → assert mismatch. 본 7건은 `main` 의 *기존*
상태이며 #88 의 새 변경과 무관 (재현 검증 완료 — #84 / #85 / #86 / #87 / #88
모두 동일).

**후속**: 본 테스트들을 `DEFAULT_MODE=SIMULATION` 강제 fixture 로 격리 (별도
PR — `app/` 코드 변경 0건 작업 가능).

### 4.2 Frontend stress test flakiness

`Approvals.stress.test.jsx` 의 *200 pending + 500 history* 통합 stress 가
jsdom + Windows 환경에서 default 5s timeout 을 자주 넘김. #83 에서 timeout
을 15s 로 완화. 운영 브라우저에서는 sub-second 라 큰 영향 없음. 후속 cleanup
대상.

### 4.3 staging up smoke 자동화 미완

`docker-compose.staging.yml` 이 존재하지만, *실제 up → health-check →
shutdown* 의 CI 자동화 미구현. 운영자가 수동으로 검증.

## 5. Frontend / UX

### 5.1 baseline 134 lint 경고 (#82 이후)

frontend lint 가 134 → 133 → 133 으로 거의 평탄. 대부분 `.jsx` 안의 inline
style 관련 / pre-existing import unused 경고. **내 PR (#82~#88) 이 추가한
신규 경고 0건** 으로 lock 되어 있으나, baseline 자체를 0 으로 만드는 cleanup
필요.

### 5.2 UpdateCheckerCard mock only (#86)

#86 의 `UpdateCheckerCard` 는 *mock* provider 만 사용. 실 Tauri updater API
연결은 후속 PR (Tauri 빌드 + 서명 키 활성화 시점).

## 6. 보안 / Secret hygiene

### 6.1 본 PR 시점 안전 baseline

- `.gitignore` 가 `.env*` / `*.key` / `*.pem` / `backups/*` 모두 차단
- `.env.example` / `.env.staging.example` 에 실 값 0건
- frontend localStorage 에 `secret*` / `api_key*` / `kis_*` 키 0건 (테스트로
  lock — #86)

### 6.2 후속 권장

- backend `.env` 가 OS 파일 권한으로만 보호됨 (Windows ACL). OS keychain
  통합은 #86 first-run wizard 후속 PR.
- `tauri signer` 의 private key 도 GitHub Secrets 만 — 운영자 워크스테이션에
  *원본* 이 남지 않게 password manager 로 즉시 이전 권장.

## 7. 본 PR (#88) 에서 *변경하지 않은* 의도적 항목

다음은 본 PR 의 범위 밖이며, 의도적으로 보존:

- `backend/requirements.txt` major version 상한 (큰 의존성 변경은 별도 PR)
- pages-deploy.yml 의 3-경로 구조 (운영 중단 위험)
- `.env.example` 의 default 값 (LIVE flag false 유지)
- `app/` 운영 로직, broker, RiskManager, Strategy, OrderExecutor 어떤 줄도
- DB schema / Alembic migrations
- frontend Settings 의 LIVE 활성화 UI (영구 없음)

## 8. 참고

- [`docs/status/current_state.md`](current_state.md) — 현재 상태 단일 진실
- [`docs/status/completed_checklist_060_088.md`](completed_checklist_060_088.md)
- [`docs/status/next_steps.md`](next_steps.md) — 다음 단계 우선순위
- [`docs/system_hygiene_report.md`](../system_hygiene_report.md) — 본 PR 점검
  결과
- [`docs/dependency_policy.md`](../dependency_policy.md) — 의존성 정책
- [`docs/system_audit_2026_05.md`](../system_audit_2026_05.md) — 전 영역 카탈로그
