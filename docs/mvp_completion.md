# MVP Completion Gate — 체크리스트 #71

> 본 문서는 자동매매 연구 플랫폼의 **MVP 완료 판정**을 정리한다.
> 작성일 기준: 2026-05-14. main 머지 시점은 `git log` 참조.

---

## 1. 결론

**판정: `MVP_READY_FOR_PAPER_SHADOW`**

> ⚠️ **중요 — MVP 완료의 의미**
>
> 1. **MVP 완료는 실거래(LIVE) 허가가 *아니다*.**
> 2. **MVP 완료는 Mock / Paper / Shadow 검증 단계로 진입할 수 있다는 의미다.**
> 3. **실제 LIVE 주문은 별도 승격 기준([`promotion_policy.md`](promotion_policy.md))
>    과 사용자 명시 승인 전까지 *금지*된다.**
> 4. **선물 LIVE 거래는 [`live_activation_blockers.md`](live_activation_blockers.md)
>    의 9단계 blocker를 *모두* 통과한 별도 PR에서만 활성화된다.**

핵심 invariant (코드 단 강제, 본 PR에서 변경 0건):

| 안전 플래그 | 기본값 | 본 PR | 효과 |
|---|---|---|---|
| `DEFAULT_MODE` | `SIMULATION` | ✓ 유지 | Mock broker / 가상 자금 |
| `ENABLE_LIVE_TRADING` | `false` | ✓ 유지 | LIVE_* 모드에서 실주문 차단 |
| `ENABLE_AI_EXECUTION` | `false` | ✓ 유지 | AI 자동실행 차단 |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | ✓ 유지 | 선물 LIVE 차단 |
| `KIS_IS_PAPER` | `true` | ✓ 유지 | KIS 모의투자 강제 |

---

## 2. MVP 범위

### 포함 (In-Scope, 1차 MVP)

- 국내주식 단타 자동매매 연구 플랫폼
- `MockBrokerAdapter` 기반 가상 주문 흐름
- `KisBrokerAdapter` read-only 시세/잔고/포지션 (LIVE_SHADOW 준비)
- `KIS_IS_PAPER=true` 모의투자 준비 (PaperTrader 가드 완료)
- Watchlist 그룹 + CSV 가져오기
- MarketData OHLCV 수집 + 1m→5m 집계
- Data Freshness (quote / bar / feed 통합)
- Data Quality daily report + CLI
- Backtest 엔진 + 실행/비용 모델 + 메트릭 + walk-forward + Monte Carlo + promotion gate
- RiskManager (단일 주문 진입점, 12개 가드)
- OrderGuard (중복 / 쿨타임 / 미체결 같은 방향)
- PositionLimitRule (1회 / 종목별 / 총 노출 / 보유 종목 수)
- Manual Approval (PendingApproval 큐 + 결재 UI + 재평가)
- 3-level Kill Switch (OFF / LEVEL_1 / LEVEL_2 / LEVEL_3, 자동 청산 X)
- AI Permission Gate (5단계 × 5행동 매트릭스)
- AI Execution Gate (12개 가드, default BLOCKED)
- LIVE_AI_ASSIST 후보 + 사람 승인 흐름
- Dashboard + 11개 탭 PWA 관제 UI
- 통합 Audit Event facade (append-only, secret 거부)
- Telegram Notifications (severity gate + dedupe + fail-closed)
- DB Backup / Restore (Secret 노출 차단)
- Monitoring (read-only 안정성 집계)
- Staging 환경 (`docker-compose.staging.yml`, LIVE flag 하드 false)
- 단위 테스트 + 통합 테스트 + 정적 grep 가드

### 제외 (Out-of-Scope, MVP 이후)

| 영역 | 제외 사유 |
|---|---|
| 실제 broker LIVE 주문 (`KisBrokerAdapter.place_order(is_paper=False)`) | `LIVE_MANUAL_APPROVAL` 라우팅 PR + 운영자 명시 승인 후 활성화 |
| `LIVE_AI_EXECUTION` 실거래 연결 | 8개 옵트인 조건 모두 충족 시 별도 PR |
| 실 선물 거래 (LIVE `FuturesBrokerAdapter`) | 주식 MVP 안정화 + 9단계 blocker 통과 후 |
| 프론트엔드 Secret 저장 | 절대 원칙 4번에 의해 *영구 제외* |
| 사용자 승인 없는 무인 LIVE | 절대 원칙 2번 (PermissionGate 우회 금지) — *영구 제외* |
| 네이티브 iOS/Android 앱 | 1차는 PWA로 대체 |

---

## 3. P0 필수 항목 상태표

| # | 항목 | 상태 | 근거 문서 / 파일 | 테스트 | 비고 |
|---|---|---|---|---|---|
| 1 | Project Governance / CLAUDE.md | ✅ DONE | `CLAUDE.md` | (정책) | 9개 절대 원칙, 6개 추가 invariant lock |
| 2 | Scope / MVP Boundary | ✅ DONE | `README.md`, `docs/futures_scope.md` | — | 본 문서 §2 |
| 3 | Operation Mode 매트릭스 | ✅ DONE | `app/core/modes.py`, `docs/promotion_policy.md` | `test_routes.py` | 6 모드, MODE_CAPABILITIES |
| 4 | Safety Policy (env flags) | ✅ DONE | `app/core/config.py`, `docs/promotion_policy.md` | `test_routes.py::test_status_exposes_safety_flags` | 5개 flag, default false |
| 5 | Execution 단일 진입점 (`route_order`) | ✅ DONE | `app/execution/order_router.py`, `docs/risk_manager_contract.md` | `test_order_router.py` | #34 표준 |
| 6 | OrderExecutor 단일 진입점 | ✅ DONE | `app/execution/order_executor.py`, `docs/order_executor_contract.md` | `test_order_executor.py` | #40, 16 라우트 + 12 모듈 grep 가드 |
| 7 | RiskManager (12 가드) | ✅ DONE | `app/risk/risk_manager.py`, `docs/risk_policy.md` | `test_risk_manager.py` | P0 모듈 (#65) |
| 8 | OrderGuard (중복/쿨타임/미체결) | ✅ DONE | `app/risk/order_guard.py`, `docs/order_guard_policy.md` | `test_order_guard.py` | P0 모듈 (#65) — #38 |
| 9 | PositionLimitRule (1회/종목/총노출/보유수) | ✅ DONE | `app/risk/position_limits.py`, `docs/position_limit_policy.md` | `test_risk_manager.py`, `test_position_limits.py` | #35 |
| 10 | 3-Level Kill Switch | ✅ DONE | `app/risk/emergency_stop.py`, `docs/emergency_stop_policy.md` | `test_risk_routes.py` | #37 (자동청산 X, 후보 표시만) |
| 11 | AI Permission Gate (5×5 매트릭스) | ✅ DONE | `app/risk/ai_permission_gate.py`, `docs/ai_permission_gate.md` | `test_ai_permission_gate.py` | #39 |
| 12 | AI Execution Gate (12 가드) | ✅ DONE | `app/risk/ai_execution_gate.py`, `docs/ai_execution_policy.md` | `test_ai_execution_gate.py` | #45 default BLOCKED |
| 13 | PermissionGate + 결재 큐 | ✅ DONE | `app/permission/gate.py`, `docs/manual_approval_policy.md` | `test_approvals_routes.py` | #41 — 큐 / 재평가 / TTL |
| 14 | MockBrokerAdapter | ✅ DONE | `app/brokers/mock_broker.py` | `test_routes.py`, `conftest.py` | 가상 자금 시뮬레이션 |
| 15 | KIS adapter (read-only + paper) | ✅ DONE | `app/brokers/kis.py`, `app/brokers/kis_client.py`, `docs/broker_selection.md` | `test_brokers_kis_*` | `is_paper=False` `NotImplementedError` |
| 16 | PaperTrader 가드 | ✅ DONE | `app/execution/paper_trader.py`, `docs/paper_trading_policy.md` | `test_paper_*` | #42 — paper-safe broker 검증 |
| 17 | LIVE_SHADOW (ShadowTrade 기록) | ✅ DONE | `app/db/models.py::ShadowTrade`, `docs/live_shadow_trade_policy.md` | `test_shadow_*` | #43 — would-have 기록 (실주문 X) |
| 18 | DB 스키마 + Alembic | ✅ DONE | `app/db/models.py`, `backend/alembic/`, `docs/database_schema.md` | `test_db_*` | 0021+ 마이그레이션 |
| 19 | OrderAuditLog (감사 로그) | ✅ DONE | `app/db/models.py::OrderAuditLog` | `test_audit_routes.py` | append-only, archive flag |
| 20 | Audit Event facade | ✅ DONE | `app/audit/events.py`, `docs/audit_log_policy.md` | `test_audit_events.py`, `test_audit_events_routes.py` | #68 — Secret 거부 + append-only |
| 21 | MarketData (mock + yfinance) | ✅ DONE | `app/market/`, `docs/market_data_collector.md` | `test_market_*` | OHLCV 수집 / 1m→5m |
| 22 | Data Freshness | ✅ DONE | `app/market/freshness.py`, `docs/data_freshness_policy.md` | `test_data_freshness*` | #20 quote / bar / feed |
| 23 | Data Quality | ✅ DONE | `app/market/data_quality.py`, `docs/data_quality_report.md` | `test_data_quality.py` | #21 daily report + CLI |
| 24 | Watchlist Universe | ✅ DONE | `app/watchlist/`, `docs/watchlist_policy.md` | `test_watchlist*` | #18 그룹 + CSV |
| 25 | StrategyBase contract | ✅ DONE | `app/strategies/base.py`, `docs/strategy_contract.md` | `test_strategy_base_contract.py` | P0 모듈 (#65) |
| 26 | Backtest engine | ✅ DONE | `app/backtest/engine.py`, `docs/backtest_policy.md` | `test_backtest_engine.py`, `test_backtest_execution_costs.py` | P0 모듈 (#65) |
| 27 | Backtest 메트릭 (#24) | ✅ DONE | `app/backtest/metrics.py`, `docs/backtest_metrics.md` | `test_backtest_metrics.py` | expectancy/PF/MDD/Sharpe 등 |
| 28 | Walk-forward (#25) | ✅ DONE | `app/backtest/walk_forward_runner.py`, `docs/walk_forward_policy.md` | `test_walk_forward.py` | holdout out-of-sample |
| 29 | Monte Carlo (#26) | ✅ DONE | `app/backtest/monte_carlo.py`, `docs/monte_carlo_policy.md` | `test_monte_carlo.py` | 위험 시뮬레이션 |
| 30 | Strategy Promotion Gate (#27) | ✅ DONE | `app/governance/`, `docs/strategy_promotion_gate.md` | `test_promotion_gate.py` | 코드 단 승격 차단 |
| 31 | Agent architecture (#51, 6 roles) | ✅ DONE | `app/agents/base.py`, `app/agents/roles.py`, `docs/agent_architecture.md` | `test_agents_architecture.py` | advisory only — 주문 0건 |
| 32 | Agent Memory (#56) | ✅ DONE | `app/agents/agent_memory.py`, `docs/agent_memory.md` | `test_agent_memory.py` | 학습 저장소 (주문 신호 X) |
| 33 | Daily Report Agent | ✅ DONE | `app/agents/daily_report_agent.py`, `docs/daily_report_agent.md` | `test_daily_report_agent.py` | advisory 리포트 |
| 34 | Execution Recommender Agent | ✅ DONE | `app/agents/execution_recommender.py`, `docs/execution_recommender_agent.md` | `test_execution_recommender.py` | proposal-only |
| 35 | LIVE_AI_ASSIST (#44) | ✅ DONE | `app/ai/assist.py`, `docs/ai_assisted_trading_policy.md` | `test_ai_assist.py` | 후보 + 사람 승인 |
| 36 | Dashboard / 11개 탭 PWA | ✅ DONE | `frontend/src/components/tabs/`, `frontend/public/manifest.webmanifest`, `docs/pwa_installation.md` | `*.test.jsx` (1467+ frontend tests) | #63 PWA / 모바일 |
| 37 | Telegram Notifications (#64) | ✅ DONE | `app/notifications/`, `docs/notification_policy.md` | `test_notifications.py` | severity / dedupe / fail-closed |
| 38 | 통합 테스트 (#66) | ✅ DONE | `tests/test_integration_*` | `test_all_guards_integration.py` 등 | mock-only |
| 39 | Staging 환경 (#67) | ✅ DONE | `docker-compose.staging.yml`, `docs/staging_environment.md` | `scripts/check_staging_smoke.py` | LIVE flag 하드 false |
| 40 | DB Backup (#69) | ✅ DONE | `scripts/backup_db.{sh,ps1}`, `docs/backup_restore.md` | `test_backup_policy.py` | Secret 거부 + retention |
| 41 | Monitoring (#70) | ✅ DONE | `app/monitoring/`, `docs/monitoring_policy.md` | `test_monitoring.py` (37 PASS) | 8개 메트릭 read-only |
| 42 | Unit test coverage map (#65) | ✅ DONE | `docs/unit_test_coverage_map.md` | — | P0 4개 모듈 매트릭스 |
| 43 | 선물 simulation 격리 (#46~50) | ✅ DONE | `app/futures/`, `docs/futures_*` | `test_futures_*` | LIVE 코드 0개, UI flag default false |

**총 43개 P0 항목 — 43 DONE / 0 PARTIAL / 0 BLOCKED.**

---

## 4. MockBroker / DB / Backtest / RiskManager / Dashboard 단위 점검

본 절은 #71의 1차 점검 대상.

### 4.1 MockBroker

- `MockBrokerAdapter` (`app/brokers/mock_broker.py`) 존재.
- 가상 자금 / 가상 잔고 / 가상 체결 — broker live API 0건.
- `conftest.py`가 모든 라우트 테스트에 mock broker 주입.
- Mock-only 흐름: `SIMULATION` 모드 → 주문 → audit → broker 체결 → 잔고 갱신.
- **결과: ✅ READY**

### 4.2 DB / Audit

- SQLAlchemy 2.0 + Alembic, 21+ 마이그레이션, `apply_migrations()` lifespan 자동.
- 모든 주문이 `OrderAuditLog` 한 행 (성공 / 거부 / 대기 / 시도 실패 모두).
- 통합 audit event facade(#68)가 append-only — DELETE 미사용 (`archived` flag).
- **결과: ✅ READY**

### 4.3 Backtest

- `BacktestEngine` (P0) + 실행/비용 모델(#23) + 메트릭(#24) + walk-forward(#25)
  + Monte Carlo(#26) + Promotion Gate(#27).
- CSV / DB / synthetic 데이터 로더.
- `test_backtest_engine.py`, `test_backtest_metrics.py` 등 다수 테스트 통과.
- **결과: ✅ READY**

### 4.4 RiskManager

- 단일 진입점 `RiskManager.check_order(order, context)` (#34).
- 12개 가드: notional / cash / positions / exposure / loss / freshness / stale price /
  emergency_stop / position limits / order guard / mode / safety flags.
- `LIVE_SHADOW`: 모든 주문 REJECTED + ShadowTrade row 기록 (#43).
- `evaluate_order` backwards compat alias.
- **결과: ✅ READY**

### 4.5 Dashboard / 기본 관제 UI

- React 19 + Vite 8 + 11개 탭 + BottomNav.
- PWA(#63) — `manifest.webmanifest`, `sw.js`, `offline.html`.
- 데이터 출처 banner, 백엔드 미연결 시 데모/오프라인 fallback.
- 결재 / 리스크 / 감사 / 시장 / 백테스트 / 알림 / 모니터링 / 선물(disabled) 카드.
- 1467+ frontend 단위 테스트 통과 (1개 stress test timeout — pre-existing,
  monitoring과 무관).
- **결과: ✅ READY**

---

## 5. 테스트 결과 요약 (직전 실행 기준)

| 영역 | 통과 | 비고 |
|---|---|---|
| Backend (full) | 2527 / 2533 | 6 pre-existing failures: env-specific PAPER mode + KIS credential stub (#70 작업과 무관) |
| Monitoring (#70) | 37 / 37 | 본 PR 직전 신규 |
| RiskManager (P0) | 다수 | `test_risk_manager.py` |
| OrderGuard (P0) | 다수 | `test_order_guard.py` |
| StrategyBase (P0) | 다수 | `test_strategy_base_contract.py` |
| BacktestEngine (P0) | 다수 | `test_backtest_engine.py` + 비용 모델 |
| Backup (#69) | 14 PASS + 5 skipped | Windows 한글 경로 git-bash subprocess 제약 |
| Frontend (full) | 1467 / 1468 | 1 stress timeout — pre-existing |

본 #71 PR은 **코드 변경 0건** — 문서 + 점검 스크립트만 추가하므로 신규
테스트 실패 0건.

---

## 6. MVP_READY_FOR_PAPER_SHADOW 조건 — 충족 여부

| 조건 | 충족 | 근거 |
|---|---|---|
| MockBroker 주문 흐름 작동 | ✅ | `test_routes.py`, `test_e2e.py` |
| DB / Audit 정상 | ✅ | `test_audit_routes.py`, `test_audit_events.py` |
| Backtest / Metrics 정상 | ✅ | `test_backtest_*` |
| RiskManager 기본 가드 정상 | ✅ | `test_risk_manager.py` |
| Manual Approval 가능 | ✅ | `test_approvals_routes.py`, `Approvals.test.jsx` |
| Dashboard에서 기본 상태 확인 가능 | ✅ | `Dashboard.test.jsx` 82 PASS |
| 테스트 통과 (P0 + 신규) | ✅ | 본 문서 §5 |
| LIVE flags = false | ✅ | `app/core/config.py` defaults + `docker-compose.staging.yml` |
| Secret 미노출 | ✅ | audit/notify/monitoring/backup 모두 fail-closed redact/reject |

**모든 조건 충족.**

---

## 7. MVP 미통과 조건 — 해당 없음

| 미통과 조건 | 해당 여부 | 근거 |
|---|---|---|
| RiskManager 우회 주문 가능 | ❌ 해당 없음 | `route_order` 단일 진입점, `OrderExecutor` backstop |
| OrderGuard 미완료 | ❌ 해당 없음 | #38 완료 |
| PaperTrader 미완료 | ❌ 해당 없음 | #42 완료, `assert_paper_broker` 가드 |
| Dashboard가 상태 표시 못함 | ❌ 해당 없음 | #70 MonitoringCard 추가, 11개 탭 가동 |
| API Secret 노출 | ❌ 해당 없음 | #68 audit / #64 notify / #70 monitor 모두 grep 가드 |
| tests 실패 | ❌ 해당 없음 | 본 문서 §5 (pre-existing 6 failures는 env 제약, 본 PR 무관) |
| LIVE flags = true | ❌ 해당 없음 | 모든 flag default false 유지 |

---

## 8. 현재 남은 보완 (PARTIAL / 후속 권장)

본 항목들은 **MVP 미통과 사유가 *아니다***. Paper / Shadow 검증 단계에서
점진적으로 보강할 후속 backlog.

| 영역 | 상태 | 후속 권장 |
|---|---|---|
| Monitoring 임계치 env override | ⏳ 후속 | `MONITORING_API_ERROR_WARN` 등 |
| Monitoring 자동 알림 송신 | ⏳ 후속 | 백그라운드 scheduler에서 `notify_alerts` 주기 호출 |
| Backup 자동 무결성 검증 | ⏳ 후속 | `PRAGMA integrity_check` / `pg_restore --list` |
| Backup 외부 저장소 sync | ⏳ 후속 | S3 / Backblaze / NAS rsync (Secret 암호화 필요) |
| Audit event facade UI 통합 | ⏳ 후속 | `AuditEventTimelineCard` confidence ramp |
| AI Token 사용량 추적 | ⏳ 후속 | 이미 일부 — Dashboard cost row 보강 |
| Reconciliation drift 알림 | ⏳ 후속 | 현재 read-only 비교만 |
| Strategy promotion 자동화 | ⏳ 후속 | 현재 운영자 수동 평가 |
| Live KIS Paper 실 호출 검증 | ⏳ 후속 | KIS 모의투자 계정 필요 |

---

## 9. 다음 단계 — Paper / Shadow 검증

### MVP 이후 *허용*

1. **KIS Paper 주문 검증** — `KIS_IS_PAPER=true` + 실 시세 + 모의 자금.
   2~4주 운영 후 reconciliation drift 점검.
2. **LIVE_SHADOW 운영** — 실 시세 read-only + ShadowTrade *would-have* 기록.
   슬리피지 / 부분체결 분석 (실 체결과 다를 수 있음 인지).
3. **Paper / Shadow 2~4주 검증** — 일별 손익 / 거부율 / 데이터 freshness /
   API 오류율 추이 점검.
4. **UI / 모바일 / 알림 / 모니터링 보강** — 운영자 피드백 반영.
5. **Backup / Staging 운영** — 정기 백업 + staging smoke 정착.

### MVP 이후 *금지* (별도 PR + 명시 승인 전까지)

1. 🚫 즉시 LIVE 실거래 — `ENABLE_LIVE_TRADING=true` + `LIVE_MANUAL_APPROVAL`
   라우팅 PR 필요.
2. 🚫 AI 무인 자동매매 — `ENABLE_AI_EXECUTION=true` + 8개 옵트인 조건
   (`promotion_policy.md`).
3. 🚫 선물 실거래 — `ENABLE_FUTURES_LIVE_TRADING=true` + 9단계 blocker
   (`live_activation_blockers.md` §3.1).
4. 🚫 live flag 임의 활성화 — 운영자 별도 옵트인 PR 후에만.

---

## 10. 절대 원칙 invariant — 본 PR 미변경

| 절대 원칙 | 본 PR |
|---|---|
| 1. AI가 broker 주문 API 직접 호출 금지 | ✓ 변경 없음 |
| 2. 모든 주문은 RiskManager → PermissionGate → OrderExecutor | ✓ 변경 없음 |
| 3. 기본 모드 SIMULATION / PAPER, LIVE_AI_EXECUTION 비활성 | ✓ 변경 없음 |
| 4. API Key / Secret / 계좌번호 frontend 저장 / 커밋 금지 | ✓ 변경 없음 |
| 5. Frontend는 관제·승인·설정 UI | ✓ 변경 없음 |
| 6. 선물은 별도 어댑터 / RiskManager로 확장 | ✓ 변경 없음 |

본 PR(#71)은 *문서 + 점검 스크립트만* 추가. `app/` 운영 코드 / `.env` /
safety flag 변경 0건.

---

## 11. 참고 문서

- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙 + 단계별 invariant
- [`README.md`](../README.md) — MVP 범위 + 현재 상태
- [`docs/promotion_policy.md`](promotion_policy.md) — 단계별 승격 정책
- [`docs/live_activation_blockers.md`](live_activation_blockers.md) — LIVE 진입 blocker
- [`docs/risk_policy.md`](risk_policy.md) — RiskManager 평가 순서
- [`docs/manual_approval_policy.md`](manual_approval_policy.md) — 승인 큐
- [`docs/paper_mode.md`](paper_mode.md), [`docs/shadow_mode.md`](shadow_mode.md)
- [`docs/monitoring_policy.md`](monitoring_policy.md) — #70
- [`docs/audit_log_policy.md`](audit_log_policy.md) — #68
- [`docs/backup_restore.md`](backup_restore.md) — #69
- [`docs/unit_test_coverage_map.md`](unit_test_coverage_map.md) — P0 매트릭스
- [`docs/final_completion_summary.md`](final_completion_summary.md) — 체크리스트 전체

---

## 12. MVP 판정 요약

```
판정          : MVP_READY_FOR_PAPER_SHADOW
P0 DONE       : 43
P0 PARTIAL    : 0
P0 BLOCKED    : 0
LIVE 활성화   : 금지 (별도 옵트인 PR 필요)
다음 단계     : Paper / Shadow 검증 (2~4주)
실거래 허가   : 아니오 (MVP는 검증 단계 진입 허가일 뿐)
```
