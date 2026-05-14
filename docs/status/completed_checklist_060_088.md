# 완료 체크리스트 #60 ~ #88

> 본 표는 *완료된* 체크리스트의 단일 진실 인덱스다. 자세한 정책은 각 항목의
> `docs/*.md` 링크 참조.

> **공통 안전 invariant** (모든 항목 만족):
> 1. broker / OrderExecutor / route_order 직접 호출 금지
> 2. `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` default `false`
> 3. API Key / Secret / 계좌번호 commit 0건
> 4. 정적 grep + dataclass 가드로 invariant lock

## 표

| # | 제목 | 핵심 산출물 / 정책 문서 | invariant lock |
|---|---|---|---|
| #60 | emergency_stop hard-reject | `RiskManager` 가 LEVEL_1+에서 모든 주문 REJECTED | `EmergencyStopRule` |
| #61 | LIVE flag gates approval queue | `ENABLE_LIVE_TRADING=false` 면 LIVE_* 모드 큐 차단 | `PermissionGate` |
| #62 | KIS adapter is_paper guard | `place_order(is_paper=False)` `NotImplementedError` | `kis.py` |
| #63 | PWA installation | `manifest.webmanifest` + `sw.js` (API 응답 캐시 0건) | `pwa_installation.md` |
| #64 | Telegram notifications | NoOp default / token backend `.env` 만 | `notification_policy.md` |
| #65 | P0 모듈 테스트 정책 | RiskManager / OrderGuard / StrategyBase / BacktestEngine 테스트 강제 | `unit_test_coverage_map.md` |
| #66 | Strategy promotion gate | 백테스트 + walk-forward + paper → live 정책 | `promotion_policy.md` |
| #67 | Staging environment | `docker-compose.staging.yml` LIVE flag 강제 false | `staging_environment.md` |
| #68 | Unified audit event log | `AuditEvent` 모델 + append-only + Secret 거부 | `audit_log_policy.md` |
| #69 | DB backup / restore | `backups/` gitignore + 일별 retention | `backup_restore.md` |
| #70 | Monitoring policy | API / 데이터 / 주문 안정성 read-only 집계 | `monitoring_policy.md` |
| #71 | MVP completion | MVP 판정 자동 요약 — 실거래 허가 아님 | `mvp_completion.md` |
| #72 | Paper Gate | Paper 4주 운용 평가 — PASS=Live Manual 검토 가능 | `paper_gate_policy.md` |
| #73 | Live Manual Gate | Live Manual Approval 진입 readiness | `live_manual_gate.md` |
| #74 | AI Assist Gate | AI 제안 품질 검증 — 시스템 검증 자료 (투자 조언 X) | `ai_assist_gate.md` |
| #75 | AI Execution Gate | AI 자동 실행 활성화 readiness — `READY_FOR_REVIEW≠실 활성화` | `ai_execution_gate.md` |
| #76 | Futures Promotion Policy | 선물 7단계 승격 + `FUTURES_AI_EXECUTION` 영구 BLOCKED | `futures_promotion_policy.md` |
| #77 | Alpha Decay Monitor | 전략 알파 감쇠 read-only 분석 — 자동 비활성 금지 | `alpha_decay_monitor.md` |
| #78 | Correlation Guard | sector/theme 신규 BUY 집중도 제한 (SELL/EXIT 우회) | `correlation_guard_policy.md` |
| #79 | Loss Tagging | 손실 *추정* 원인 7카테고리 25태그 — append+review only | `loss_tagging_policy.md` |
| #80 | Pre-market Checklist | 11카테고리 + 모드별 required + manual_ack 비우회 | `pre_market_check_policy.md` |
| #81 | Strategy Registry beginner metadata | 6개 전략 한글명 + recommended_mode + 가짜 전략명 영구 금지 | `strategy_registry.md` |
| #82 | Strategy displayName UI 적용 (1차) | 6곳 UI (`AuditLog` / `LiveEngine` / `AgentStats`) | `strategy_registry.md` |
| #83 | Strategy displayName UI 적용 (2차) | 4곳 UI (`Approvals` / `ApprovalQueue` / `AgentMemory` / `ExecutionRecommender`) | `strategy_registry.md` §8.2 |
| #84 | Strategy Signal Aggregator | 4 단타 전략 vote → 종목별 통합 advisory | `strategy_signal_aggregator.md` |
| #85 | Strategy Selection Agent | 시장 상태 + 4 전략 → 최적 조합 + blocked reason | `strategy_selection_agent.md` |
| #86 | Desktop Installer skeleton | Tauri v2 + UpdateChecker mock + 6개 가이드 | `desktop_packaging.md` |
| #87 | System Audit 2026-05 | 단일 진실 카탈로그 + 22 invariant 테스트 | `system_audit_2026_05.md` |
| #88 | System Hygiene (본 PR) | `.gitignore` + status docs + dependency policy | `system_hygiene_report.md` |

## 누락 / 메모

- #60 이전 항목 (예: #46 LIVE_SHADOW, #47 FuturesBroker contract, …) 은
  `CLAUDE.md` 본문 + 각 정책 문서에 카탈로그됨. 본 표는 *#60 이후* 만 다룬다.
- 본 표는 새 체크리스트 머지 시 *반드시 함께 갱신* (CLAUDE.md "변경 시 동기화"
  정책의 5번 — 새 docs 추가).
