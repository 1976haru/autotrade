# Agent Trader v1 — 현재 상태 (2026-05-14 갱신)

> 본 문서는 *현재 시점* main 브랜치의 운영 단계 / 가능 / 불가 상태를 단일
> 진실로 정리한다. 더 자세한 영역별 카탈로그는
> [`docs/system_audit_2026_05.md`](../system_audit_2026_05.md) (#87) 참조.

## 0. 한 줄 요약

**현재는 MVP / Paper / Shadow 검증을 위한 연구·운영 플랫폼이며, 실거래 자동
매매가 허가된 상태가 *아닙니다*.** LIVE 관련 flag 는 모두 `false` 가 default
이고, 실거래 활성화는 별도 PR + 사용자 명시 승인이 필요합니다.

## 1. 현재 main 상태

| 항목 | 값 |
|---|---|
| 최신 머지 | `#87 system audit` (#88 이전 baseline) |
| frontend 버전 | `1.0.0` (`frontend/package.json` + `appInfo.js`) |
| Python | 3.12 권장 (CI 매트릭스) |
| Node | 20 (CI cache) |
| DB | SQLite (local default), PostgreSQL (운영 권장) |
| frontend test | **1603 PASS** (#86 시점) |
| backend test | **2926 PASS, 5 skipped** (#87 시점, 사전 환경 실패 7건은 main 동일) |

## 2. 운영 모드 (`backend/app/core/modes.py::OperationMode`)

| 모드 | 정의 | 실주문 | 현재 가용 |
|---|---|---|---|
| `SIMULATION` | 모의 데이터 + MockBroker | ❌ | ✅ **default** |
| `PAPER` | 실 시세 + KIS 모의투자 (가상 자금) | ❌ | ✅ |
| `LIVE_SHADOW` | 실 시세 read-only, ShadowTrade 추정 기록 | ❌ | ✅ |
| `LIVE_MANUAL_APPROVAL` | 사용자 승인 후 실거래 | ✅ | ⏳ 후속 PR (KIS LIVE 활성화 필요) |
| `LIVE_AI_ASSIST` | AI 제안 + 사용자 승인 | ✅ | ⏳ |
| `LIVE_AI_EXECUTION` | AI 자동 실행 | ✅ | 🛑 **현 단계 영구 차단** (#75) |
| `VIRTUAL_AI_EXECUTION` | 가상 데이터 + AI 자동 체결 | ❌ | ✅ |

## 3. 안전 flag (`backend/.env.example` default)

| 변수 | 기본값 | 현재 변경 가능 |
|---|---|---|
| `DEFAULT_MODE` | `SIMULATION` | 운영자 `.env` 편집 |
| `ENABLE_LIVE_TRADING` | `false` | 별도 옵트인 PR + 명시 승인 |
| `ENABLE_AI_EXECUTION` | `false` | 동일 |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | **영구 차단 (#76)** |
| `KIS_IS_PAPER` | `true` | LIVE 활성화 시에만 false 가능 |
| `MARKET_DATA_PROVIDER` | `mock` | `yfinance` 로 교체 가능 |
| `ENABLE_FILL_POLLING` | `false` | 운영 시 true |

> **본 PR (#88) 은 위 default 를 변경하지 않습니다.** Repository hygiene 만.

## 4. 매매기법 (6종, 코드 검증 — #87)

| 내부 ID | 초보자명 | 위험도 | backtest | paper | live |
|---|---|---|---|---|---|
| `sma_crossover`    | 단기/장기 이동평균 교차    | MEDIUM | ✅ | ✅ | ❌ |
| `rsi_reversion`    | RSI 과매도/과매수 회복    | MEDIUM | ✅ | ✅ | ❌ |
| `vwap_strategy`    | VWAP 평균 회귀            | MEDIUM | ✅ | ✅ | ❌ |
| `orb_vwap`         | ORB + VWAP 돌파           | HIGH   | ✅ | ✅ | ❌ |
| `volume_breakout`  | 거래량 급증 돌파          | HIGH   | ✅ | ✅ | ❌ |
| `pullback_rebreak` | 눌림목 재돌파             | HIGH   | ✅ | ✅ | ❌ |

상세: [`docs/system_audit_2026_05.md`](../system_audit_2026_05.md) §1.

## 5. 최근 완료 체크리스트 (#80 ~ #88)

| # | 제목 | 핵심 산출물 |
|---|---|---|
| #80 | Pre-market Checklist | 11개 카테고리 / 모드별 required check |
| #81 | Strategy Registry beginner metadata | 6개 전략 한글명 + `recommended_mode` + 가짜 전략명 영구 금지 |
| #82 | Strategy displayName UI 적용 | 6곳 UI 에 `displayName + (internal_id)` carry |
| #83 | Strategy displayName UI 추가 4곳 | Approvals / ApprovalQueue / AgentMemory / ExecutionRecommender |
| #84 | Strategy Signal Aggregator | 4 단타 전략 vote → 종목별 통합 후보 |
| #85 | Strategy Selection Agent | 시장 상태 + 4 전략 신호 → 최적 조합 선택 |
| #86 | Desktop Installer (Tauri v2) skeleton | `src-tauri/` + UpdateChecker mock + 6개 가이드 문서 |
| #87 | System Audit 2026-05 | 단일 진실 카탈로그 + 22 invariant 테스트 |
| #88 | System Hygiene (본 PR) | `.gitignore` 정비 + status docs + dependency policy |

전체 체크리스트 (#60~#88):
[`docs/status/completed_checklist_060_088.md`](completed_checklist_060_088.md).

## 6. Paper / Shadow 검증 준비 상태

| 게이트 | 정책 | 현재 |
|---|---|---|
| Paper 4주 운용 (#72) | `evaluate_paper_gate` — ≥28일 / ≥100건 / PF≥1.2 / MDD≤15% / 손실한도 위반 0 | **대기** (운영 데이터 축적 필요) |
| Live Manual Approval (#73) | `evaluate_live_manual_gate` — Paper PASS + opt-in + 1회≤5만원 / 일일≤1만원 | **대기** |
| AI Assist 검증 (#74) | `evaluate_ai_assist_gate` — ≥100 제안 / 손실율≤55% / 운영자 거절율≤50% | **대기** |
| AI Execution 활성화 (#75) | 모든 상위 게이트 PASS + 1회≤3만원 / 종목 whitelist / 시간 09:30~14:30 | **대기** — `is_live_authorization=False` 영구 |

## 7. 본 PR (#88) 의 변경 범위

- `.gitignore` 명확화 (`backend/.venv-310/` 등 명시 추가)
- `docs/status/*.md` (current_state / completed_checklist_060_088 /
  known_risks / next_steps)
- `docs/dependency_policy.md`
- `docs/system_hygiene_report.md`
- README 의 #88 링크 + 상태 배너
- `backend/tests/test_repository_hygiene.py` (정적 검사 only)

**미변경**: `app/` 운영 로직, `.env*` 값, broker / OrderExecutor /
`route_order`, Strategy 6종, RiskManager, DB schema, Alembic migrations.

## 8. 참고

- [`docs/system_audit_2026_05.md`](../system_audit_2026_05.md) — 전 영역 카탈로그 (#87)
- [`docs/status/completed_checklist_060_088.md`](completed_checklist_060_088.md) — 체크리스트 표
- [`docs/status/known_risks.md`](known_risks.md) — 현재 알려진 위험
- [`docs/status/next_steps.md`](next_steps.md) — 다음 단계 우선순위
- [`CLAUDE.md`](../../CLAUDE.md) — 9개 절대 원칙
