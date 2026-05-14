# 다음 단계 우선순위 — 2026-05

> 본 문서는 *현재 → 실거래* 까지의 후속 작업 우선순위를 정리한다. 본 우선순위
> 는 [`docs/status/known_risks.md`](known_risks.md) 의 위험과 1:1 대응.

## P0 (단기 — Paper 운영 시작 *전* 필수)

| # | 작업 | 근거 |
|---|---|---|
| P0-1 | 사전 환경 실패 7건 격리 (`DEFAULT_MODE=SIMULATION` fixture 강제) | `known_risks.md` §4.1 |
| P0-2 | `backend/requirements.lock.txt` 도입 (`pip-compile` 또는 `uv pip compile`) | `dependency_policy.md`, `known_risks.md` §2.1 |
| P0-3 | KIS 모의투자 API 실 연결 통합 테스트 1건 (운영자 hand-test 가능한 fixture) | `known_risks.md` §3.3 |
| P0-4 | Paper 운영 시작 — 28일 데이터 축적 (`#72` Paper Gate evaluator 입력) | `paper_gate_policy.md` |
| P0-5 | staging up → health-check → shutdown smoke 자동화 (`scripts/` 또는 CI job) | `known_risks.md` §4.3 |

## P1 (중기 — Paper Gate PASS 직후)

| # | 작업 | 근거 |
|---|---|---|
| P1-1 | Tauri desktop installer 빌드 검증 (Windows runner + Rust 툴체인 + tauri-cli) | `desktop_packaging.md` §6 |
| P1-2 | PyInstaller backend sidecar 빌드 → `src-tauri/binaries/` 등록 | `desktop_packaging.md` §3 |
| P1-3 | `tauri signer generate` → public key commit, private key GitHub Secrets | `desktop_update_policy.md` §3 |
| P1-4 | First-run wizard 실 저장 흐름 (OS keychain via `tauri-plugin-stronghold`) | `first_run_setup_wizard.md` §4 |
| P1-5 | ShadowTrade 추정 정확도 측정 (실시세 vs ShadowTrade.estimated_fill_price) | `known_risks.md` §3.2 |

## P2 (중기 — Live Manual Approval 진입 *후*)

| # | 작업 | 근거 |
|---|---|---|
| P2-1 | GitHub Pages 배포 구조 단순화 (`gh-pages` only, main root cleanup) | `known_risks.md` §1.1 |
| P2-2 | Frontend lint baseline 0 정리 (134 → 0) | `known_risks.md` §5.1 |
| P2-3 | Approvals.stress 안정화 (가상 환경에서 sub-second 보장) | `known_risks.md` §4.2 |
| P2-4 | UpdateChecker → 실 Tauri updater API 연결 | `known_risks.md` §5.2 |
| P2-5 | `requirements.txt` major version 상한 (예: `fastapi>=0.115,<1.0`) + 회귀 테스트 | `dependency_policy.md` §2 |

## P3 (장기 — Live 운영 정상화 *후*)

| # | 작업 |
|---|---|
| P3-1 | `LIVE_MANUAL_APPROVAL` 라우팅 PR — KIS LIVE `place_order` 활성화 |
| P3-2 | `LIVE_AI_ASSIST` 활성화 — AI 제안 + 사용자 승인 |
| P3-3 | (별도) AI Execution gate (#75) `READY_FOR_REVIEW` 도달 시 운영자 결정 — *영구 BLOCKED 정책 재검토 불가*, 본 시스템에서는 자동 활성화 0건 |

## 실거래 전환 *최소* 조건 (영구 baseline)

다음 모두 충족하지 않으면 어떤 LIVE flag 도 true 로 전환되지 *않는다*:

1. Paper Gate (#72) PASS (28일 / 100건 / PF≥1.2 / MDD≤15% / 손실한도 위반 0)
2. Live Manual Gate (#73) PASS + 운영자 explicit opt-in
3. AI Assist Gate (#74) PASS — `LIVE_AI_ASSIST` 모드 사용 시
4. AI Execution Gate (#75) `READY_FOR_REVIEW` — `LIVE_AI_EXECUTION` 모드 사용
   시. **futures_allowed=False 영구** (#76)
5. 별도 옵트인 PR + 사용자 명시 승인 + `.env` 의 안전 flag *수동* 변경
6. 초소액 canary (1회 ≤ 5만원 / 일일 ≤ 1만원) 1주 이상 운영
7. 즉시 kill switch (`emergency_stop`) 운영자 손에 닿는 상태

본 조건들은 본 시스템에서 *영구 baseline* — 어떤 PR 도 *완화* 시키지 못한다
(테스트 + 정책 문서로 강제).

## 본 PR (#88) 이 *직접* 처리한 항목

위 우선순위 중 #88 은 **하나도 직접 해결하지 않는다** — `app/` 운영 로직,
의존성, 빌드, broker 어떤 것도 건드리지 않는다. #88 의 산출물은:

- `.gitignore` 명확화
- `docs/status/*.md` (본 문서 포함)
- `docs/dependency_policy.md` — P0-2 의 *정책 토대*
- `docs/system_hygiene_report.md`
- README 의 #88 링크
- repository hygiene 정적 invariant 테스트

→ 이후 작업이 어디서 시작해야 하는지 *명확한 지도* 를 제공하는 것이 #88 의
유일한 가치다.

## 참고

- [`docs/status/current_state.md`](current_state.md)
- [`docs/status/known_risks.md`](known_risks.md)
- [`docs/status/completed_checklist_060_088.md`](completed_checklist_060_088.md)
- [`docs/promotion_policy.md`](../promotion_policy.md) — 모드 별 승격 흐름
- [`docs/live_activation_blockers.md`](../live_activation_blockers.md) — LIVE
  활성화 blocker 매트릭스
