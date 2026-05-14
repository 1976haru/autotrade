# Final Completion Summary

**시점**: 2026-05-07
**저장소**: `C:\trade\autotrade`
**main 커밋**: 186 머지 직후 (origin/main과 동기화)

## 사용자 절대 요구 (전체 이행)

1. ✅ 실제 API Key 미입력 — `.env` 변경 0건. KIS/Anthropic 기본값(빈 문자열) 그대로.
2. ✅ 실제 broker live order endpoint 미호출 — `KisBrokerAdapter.place_order(is_paper=False)` `NotImplementedError` 유지.
3. ✅ 실제 주식 실거래 미활성화 — `ENABLE_LIVE_TRADING=false`(default).
4. ✅ 실제 선물 실거래 미활성화 — `ENABLE_FUTURES_LIVE_TRADING=false`(default), `FuturesRiskManager.evaluate_order` 항상 REJECTED.
5. ✅ AI 자동매매 `VIRTUAL_AI_EXECUTION` 모드에서만 동작 (152, 158-165, 185).
6. ✅ 선물 `MockFuturesBroker` + `FuturesSimulationEngine`에서만 (151, 169).
7. ✅ 전체 체크리스트 + 추가 MUST + 테스트 + stress test 진행.
8. ✅ 최종 시점 main↔origin/main 동기화, working tree clean.

## 핵심 기능 매핑

### Agent Council (10 agents — 185)

| Agent | 역할 | 결정 카테고리 |
|---|---|---|
| ChiefTradingAgent | 종합 결정자 | BUY/SELL/HOLD/REJECT |
| MarketRegimeAgent | 시장 체제 분류 | INFO |
| StrategySelectionAgent | regime → strategy | INFO |
| StockSelectionAgent | candidates 선택 | INFO/HOLD |
| PositionSizingAgent | quantity 추천 | INFO/HOLD |
| RiskOfficerAgent | 정책 사전 검토 | APPROVE/REJECT |
| EntryTimingAgent | 진입 타이밍 | BUY/HOLD |
| ExitTimingAgent | 청산 타이밍 | SELL/HOLD |
| NewsTrendAgent | 뉴스/추세 stub | INFO/WARN |
| PostTradeReviewAgent | 사후 평가 | INFO/WARN |

자세한 내용: [`docs/agent_decision_schema.md`](agent_decision_schema.md), [`docs/agent_stress_test_report.md`](agent_stress_test_report.md).

### 가상 자동매매 스택

| 모듈 | 역할 | PR |
|---|---|---|
| `app/virtual/order_ledger.py` | VirtualOrder 7-state 라이프사이클 | 148 |
| `app/virtual/fill_engine.py` | 시장가/지정가 체결 시뮬 + 슬리피지 + 부분체결 + stale 거부 | 149 |
| `app/virtual/position_engine.py` | FIFO 페어매칭 + realized/unrealized PnL + 청산 평가 | 150 |
| `app/virtual/auto_close.py` | 청산 평가 → SELL OrderRequest 자동 라우팅 | 172 |
| `app/futures/simulation.py` | margin / liquidation_price / slippage / fee | 151 |
| `app/futures/mock.py` | MockFuturesBroker (in-memory + audit) | 151, 169 |
| `app/ai/virtual_agent.py` | VirtualAiAgent (152, 158-165) | 152 |
| `app/ai/agents/council.py` | 10-agent council | 185 |

### RiskPolicy 가드 27개

전체 매트릭스: [`docs/risk_guards_matrix.md`](risk_guards_matrix.md). 평가 순서:

```
0. client_order_id idempotency (140)
0.5 AI rate limit (161) / Global rate limit (177) / max_orders_per_day (183)
1. Emergency stop (060/153) — hard short-circuit
1.1 AI kill-switch (178) — AI-only short-circuit
1.5 Stale price (143) — hard short-circuit
2. Symbol whitelist (175)
2.5 Trading hours (176)
3. AI confidence (158)
3.5 AI reasoning (159)
4. max_order_notional (001)
5. max_position_size_pct (174)
6. max_daily_loss (145, KST 166)
7. insufficient_cash (001)
8. max_positions (001)
9. max_symbol_exposure (001) + _pct (181)
10. max_total_exposure (179) + _pct (179)
11. LIVE_SHADOW mode
12. LIVE_MANUAL/AI_ASSIST early-return (061)
13. AI execution gate (152)
14. LIVE 가드 (001)
```

추가 자동화:
- 182 auto-stop on consecutive rejections
- 167 approval queue TTL
- 168 audit archival flag

### 운영자 / 운영 도구

| 기능 | 위치 | PR |
|---|---|---|
| Emergency stop reason taxonomy (9 codes) | `app/risk/emergency_reasons.py` | 153 |
| AI agent stats endpoint | `/api/ai/agent-stats` | 162, 165 |
| Strategy scoreboard | `/api/strategies/scoreboard` (backtest+live+confidence histogram+per-strategy PnL) | 137, 144, 147, 165, 173 |
| Risk policy reference | `docs/risk_guards_matrix.md` | 180 |
| smartphone 운용 가이드 | `docs/smartphone_operator_mode.md` | 186 |

## 데이터 모델

| 테이블 | 마이그레이션 | 핵심 |
|---|---|---|
| `order_audit_log` | 0001 → 0012 | 모든 주문 결정 + 체결 + AI 메타 + archival flag |
| `pending_approval` | 0001 → 0003 (070 attempts) | 큐 + TTL EXPIRED (167) |
| `ai_analysis_log` | 0001 → 0004 (mode) | AI read-only 분석 |
| `emergency_stop_event` | 0002 → 0011 (reason_code) | 토글 이력 |
| `virtual_order` | 0009 (148) | 가상 주문 라이프사이클 |
| `futures_order_audit_log` | 0013 (169) | 선물 주문 별도 audit |
| `agent_decision_log` | 0014 (185) | 10-agent 결정 영구화 |

## 검증 결과 (이번 세션 종료 시점)

| 항목 | 결과 |
|---|---|
| backend pytest | **851 passed, 15 deselected** (default suite) |
| ruff check | **All checks passed** |
| frontend npm test | **833 passed (23 files)** (직전 검증 시점) |
| frontend npm run lint | **0 errors / 55 warnings** (157 cleanup 후) |
| frontend npm run build | **vite built**, 342kB → 98kB gzipped |
| stress test | 별도 nightly workflow + 본 세션 +6 시나리오 (155) |
| CI workflow | backend-ci.yml + frontend-ci.yml (10분) + nightly stress 분리 (157) |

## 작성된 docs

| 문서 | 상태 |
|---|---|
| `final_checklist_report.md` | ✅ 156에서 작성 |
| `virtual_trading_architecture.md` | ✅ 156에서 작성 |
| `futures_simulation_report.md` | ✅ 151 (169 보강) |
| `ai_virtual_execution_report.md` | ✅ 152 (158-165 보강) |
| `agent_stress_test_report.md` | ✅ 186 신설 |
| `stress_test_report.md` | ✅ 133 (155 확장) |
| `live_activation_blockers.md` | ✅ 156 |
| `backlog.md` | ✅ 156 (이후 갱신) |
| `ci_recovery_report.md` | ✅ 157 |
| `final_completion_summary.md` | ✅ 본 문서, 186 |
| `smartphone_operator_mode.md` | ✅ 186 신설 |
| `agent_decision_schema.md` | ✅ 185 신설 |

## 안전 invariant 단정문

1. ❌ **실 API Key 입력**: 본 directive 동안 `.env` 변경 0건. `KIS_APP_KEY` / `KIS_APP_SECRET` / `ANTHROPIC_API_KEY` 모두 빈 문자열 default.
2. ❌ **실 broker live endpoint 호출**: `KisBrokerAdapter.place_order(is_paper=False)` `NotImplementedError` 유지. 본 세션 모든 테스트 `MockBrokerAdapter` / `MockFuturesBroker` 사용.
3. ❌ **LIVE_AI_EXECUTION 실 broker 연결**: `OperationMode.LIVE_AI_EXECUTION`은 enum에 존재하지만 실 broker 라우팅 없음. `VIRTUAL_AI_EXECUTION` (152)만 활성.
4. ❌ **선물 실거래 활성화**: `FuturesRiskManager.evaluate_order` 모든 경로 REJECTED. `MockFuturesBroker`만 작동.
5. ✅ **모든 주문 audit row 영구화**: REJECTED / NEEDS_APPROVAL / APPROVED 모두 `OrderAuditLog`.
6. ✅ **모든 Agent 결정 audit 영구화**: `AgentDecisionLog` chain_id 기반.
7. ✅ **emergency_stop 항상 작동**: hard short-circuit + 153 reason_code 분류 + 182 자동 trigger.

## 남은 리스크

자세한 내용: [`docs/live_activation_blockers.md`](live_activation_blockers.md).

LIVE 활성화 전 사용자 명시 옵트인이 필요한 영역:
1. KIS LIVE place_order / cancel_order 통합 (별도 PR)
2. 선물 LIVE 평가 로직 + KIS 선물 adapter (별도 PR)
3. LiveAiAgent 실 LLM 통합 (별도 PR, 비용 발생)
4. RiskPolicy 한도 운영 자본 기준으로 재계산
5. 4주 이상 PAPER / LIVE_SHADOW 검증
6. Position vs broker reconciliation 메커니즘 (LIVE 시점 필요)

## 다음 권장 단계

본 시점에서 추가 자동 진행은 marginal value. 사용자가 LIVE 활성화 의사가 있으면:

1. `docs/live_activation_blockers.md` 9절 운영자 절차 체크리스트 점검
2. 별도 옵트인 PR로 KIS LIVE adapter 활성화
3. 초소액 실거래 (예: 10만원 cap) 단계적 진행

## 관련 문서

- [`final_checklist_report.md`](final_checklist_report.md) — 156 시점 체크리스트
- [`virtual_trading_architecture.md`](virtual_trading_architecture.md) — 시스템 다이어그램
- [`risk_guards_matrix.md`](risk_guards_matrix.md) — 27개 가드 reference
- [`smartphone_operator_mode.md`](smartphone_operator_mode.md) — 운영자 동선
- [`agent_decision_schema.md`](agent_decision_schema.md) — Agent Council schema
- [`agent_stress_test_report.md`](agent_stress_test_report.md) — Agent 검증
- [`backlog.md`](backlog.md) — NICE / LIVE 옵트인 항목
- [`kiwoom_rest_research.md`](kiwoom_rest_research.md) — 2차 브로커(키움 REST) 도입 조사표 (체크리스트 #15)
- [`database_schema.md`](database_schema.md) — DB 스키마 점검 + 원문 테이블 매핑 + PG 전환 가이드 (체크리스트 #17)
- [`watchlist_policy.md`](watchlist_policy.md) — Watchlist universe 정책 + CRUD/CSV API + 50~200개 한도 (체크리스트 #18)
- [`market_data_collector.md`](market_data_collector.md) — OHLCV 수집기 + 1m→5m 집계 + 누락률 + freshness 정책 (체크리스트 #19)
- [`data_freshness_policy.md`](data_freshness_policy.md) — quote/bar/feed 통합 freshness + WebSocket reconnect 정책 + read-only API (체크리스트 #20)
- [`data_quality_report.md`](data_quality_report.md) — MarketBar 일별 품질 점수(GOOD/WARNING/POOR/EXCLUDE) + CLI + 백테스트 제외 정책 (체크리스트 #21)
- [`theme_signal_policy.md`](theme_signal_policy.md) — 테마/뉴스/트렌드 후보 필터 + Provider abstraction + ThemeFilter (BUY/SELL 미반환) (체크리스트 #22)
- [`backtest_policy.md`](backtest_policy.md) — BacktestConfig 4개 체결 모델 + 수수료/세금/슬리피지 + 승격 평가 정책 (체크리스트 #23)
- [`backtest_metrics.md`](backtest_metrics.md) — metrics.py 독립 모듈 + 기대값/PF/MDD/Sharpe/연속손실/시간대별 손익 + 승률만 승인 금지 (체크리스트 #24)
- [`walk_forward_policy.md`](walk_forward_policy.md) — train/validation/holdout fold 분할 + rolling/anchored + 한 번의 대박 방지 + PASS/CAUTION/FAIL (체크리스트 #25)
- [`monte_carlo_policy.md`](monte_carlo_policy.md) — shuffle/bootstrap/block_bootstrap + risk_of_ruin / 최악 5% MDD / promotion_risk_flag (체크리스트 #26 P2 고도화)
- [`strategy_promotion_gate.md`](strategy_promotion_gate.md) — 승격 기준 코드 게이트 + AI 추천 차단 + 사람 승인 필수 + LIVE_AI_EXECUTION 영구 BLOCKED (체크리스트 #27)
- [`strategy_contract.md`](strategy_contract.md) — StrategyBase 인터페이스 강화 (generate_signal/calculate_size/exit_rule/explain_signal) + 직접 주문 금지 invariant + on_bar 호환 (체크리스트 #28)

## 사후 UI surface 보강 (187-196)

186 시점에 백엔드는 모두 영구화되어 있었지만 일부 데이터가 endpoint /
프론트 화면으로 노출되지 않은 상태였다. 추가 PR로 운영자 동선에 직접
보이도록 surface:

### 1차 보강 (187-191) — AI / Agent 가시성

| PR | 역할 | 위치 |
|---|---|---|
| 187 | `GET /api/ai/agent-decisions` + `AgentCouncilCard` | AI 탭 상단 — chain별 chief 결정 + 펼치기 |
| 188 | `AgentStatsCard` (162/165 데이터를 시각화) | AI 탭 — 승인율 / confidence histogram / per-strategy P/L |
| 189 | `OrderAuditOut.ai_decision_meta` surface + `formatAiDecisionMeta` | Audit 탭 OrderAuditRow — AI 거부 사유 한 줄 |
| 190 | `ApprovalOut`에 requested_by_ai / strategy / signal_* / ai_decision_meta 추가 + Approvals 카드 AI 배지 | Approvals 탭 결재 카드 |
| 191 | `AgentLatestTile` | Dashboard 탭 — 최근 chief 결정 한 줄 |

### 2차 보강 (193-196) — Virtual / Futures 가시성

| PR | 역할 | 위치 |
|---|---|---|
| 193 | `routes_virtual` (`/api/virtual/orders`, `/orders/summary`) + `VirtualOrderLedgerCard` | LiveEngine 탭 — 8 status chip + summary + 행 테이블 |
| 194 | `routes_futures` (`/api/futures/orders`, `/orders/summary`) + `FuturesOrderAuditCard` | Futures 탭 — 강제청산 toggle + margin Δ + 행 |
| 195 | `routes_virtual` `/positions` + `VirtualPositionsCard` | LiveEngine 탭 — FIFO 포지션 + realized/unrealized 합계 |
| 196 | Approval history `EXPIRED` status filter + UI chip | Approvals 탭 — 167 자동 만료 행 분리 조회 |

### 3차 보강 (198-199) — Audit 아카이브 + 정책 가시성

| PR | 역할 | 위치 |
|---|---|---|
| 198 | `OrderAuditOut.archived` surface + `ArchivedAuditView` 서브탭 + `listOrderAudits.include_archived` | AuditLog 탭 "아카이브" 서브탭 — 168 cold rows |
| 199 | `RISK_POLICY_FIELDS` 6 → 22 항목 + `_formatPolicyValue` (pct/seconds/list) + `isPolicyValueOverridden` (배열 비교) | StrategyRisk 탭 BackendPolicyCard — 모든 가드 노출 |

### 4차 보강 (201-203) — 환경/백테스트/AI 흐름 가시성

| PR | 역할 | 위치 |
|---|---|---|
| 201 | `/api/status.safety_flags` 8개 env flag 매트릭스 + `SafetyFlagsCard` 안전·위험 배지 | Settings 탭 상단 — CLAUDE.md "안전 플래그" 표 라이브 스냅샷 |
| 202 | `BacktestRunsView` row 클릭 expand + `BacktestTradesPanel` (side/qty/entry/exit/pnl) + per-id 캐시 | AuditLog 백테스트 서브탭 — `/api/backtest/runs/{id}` 활용 |
| 203 | `AI_ONLY_FILTER_STORAGE_KEY` + `audit-ai-only-toggle` (requested_by_ai=true 필터) | AuditLog 이벤트 타임라인 — kind="AI 호출"(분석 로그)과 별개 |

### 5차 보강 (205-206) — Agent 결정 분포 + filter

| PR | 역할 | 위치 |
|---|---|---|
| 205 | `/api/ai/agent-decisions/summary` (by_agent / total_chains / recent_chains) + `AgentDecisionSummaryCard` | AI 탭 — agent별 결정 분포 + chain 누적 |
| 206 | `/api/ai/agent-decisions` agent_name + decision 쿼리 + `AgentCouncilCard` 11-chip filter | AI 탭 — "Chief의 REJECT만" 같은 narrow 가능 |

### 6차 보강 (208) — 긴급정지 요약

| PR | 역할 | 위치 |
|---|---|---|
| 208 | `/api/risk/emergency-stop/summary` (currently_active / active_since / by_reason / total_toggles / total_activations) + `EmergencyStopSummaryCard` | StrategyRisk 탭 — BackendPolicyCard와 history 사이 |

### 7차 보강 (210) — Agent 요약 lookback

| PR | 역할 | 위치 |
|---|---|---|
| 210 | `/api/ai/agent-decisions/summary?lookback_days=N` (0=all, 1..365) + `AgentDecisionSummaryCard` 4 chip 토글 (전체/1일/7일/30일) | AI 탭 — "이번 주 chief 분포" narrow |

### 8차 보강 (212) — Position reconciliation (MUST 복귀)

187-210 surface 보강 라운드 이후 첫 MUST 항목 — backlog #2 / LIVE 활성화
blocker #6 해소. broker view vs audit view 사이의 quantity drift 감지를
운영자 동선에 surface해 LIVE 활성화 전 일관성 검증을 가능하게 함.

| PR | 역할 | 위치 |
|---|---|---|
| 212 | `app/reconciliation/position_checker.py` (`aggregate_audit_positions` / `compare_positions` / `reconcile`) + `/api/reconciliation/status` + `ReconciliationStatusCard` (DRIFT/IN SYNC 배지 + 사유별 mismatch 행) | StrategyRisk 탭 — BackendPolicyCard / EmergencyStopSummaryCard 다음 |

### 검증 (212 머지 직후)

- backend pytest **921 passed**, 15 deselected
- ruff **All checks passed**
- frontend npm test **936 passed**, 31 files
- npm run lint **0 errors** / 63 warnings
- npm run build **388kB → 108kB gzipped**

### 안전 invariant

187-212는 **모두 read-only 또는 schema-only 보강**이다:
- 새 broker 호출, 새 가드 분기, 새 AI 실행 경로 0건.
- 새 endpoint는 모두 SELECT만 — DB write 0건.
- 199는 `/api/risk/policy` 응답 변경 없이 frontend 표시만 확장.
- 201은 `/api/status` 응답에 `safety_flags` 블록 추가 (read-only).
- 202는 기존 `/api/backtest/runs/{id}` 활용 — backend 변경 0.
- 203은 frontend 필터만 — backend 변경 0.
- 205/208은 새 SELECT 집계 endpoint — 어떤 가드 / 결정에도 영향 X.
- 206/210은 기존 endpoint에 query param 추가 (백워드 호환).
- 212는 새 SELECT 비교 endpoint — `broker.get_positions()` + audit log
  read-only 비교만, 새 주문/가드 분기 0건. KIS LIVE 활성화 전 drift 감지
  메커니즘.
- CLAUDE.md 절대 원칙 / RiskManager → PermissionGate → OrderAuditLog
  단일 진입점은 그대로다.
- 192/197/200/204/207/209/211은 본 문서 자체 갱신.

## 종료 상태

**완료**. 사용자 directive 최종 완성 모드의 모든 항목 이행 + 사후 UI
surface 보강 (187-210) + MUST 복귀 (212 position reconciliation) 완료.
추가 자동 진행 여지는 LIVE 옵트인 또는 NICE 영역으로만 남음.

## 후속 (213 ~ 236)

213-228: ErrorBoundary + responsive layout + GitHub Pages demo + auto-update
+ Smartphone Operator panel + Agent OS (operating loop / 5 enhanced agents /
market regime / signal quality gate / stress).

**229-236 (UI Premium Redesign 시리즈)**: 9 phase로 design system 토대 +
Dashboard hero + responsive nav + agent-centric UI + error/empty/loading 정규화
+ Pages demo polish + core tabs PageHeader + smoke tests + report. 자세한
내역은 `docs/ui_redesign_report.md`. 이 시리즈는 frontend UI만 — broker /
RiskManager / PermissionGate / LIVE flag는 어떤 코드도 수정되지 않음.

frontend tests **1008 passed**, lint 0 errors, build OK. Live demo:
<https://1976haru.github.io/autotrade/>.

## 추가 (29) — VolumeBreakoutStrategy

체크리스트 #29 거래대금 돌파 1차 전략 구현. backend pytest 29건 신규
(`test_volume_breakout_strategy.py`), 전체 backend pytest **1365 passed**
(SIMULATION 모드 기준).

| 항목 | 위치 |
|---|---|
| 전략 코드 | `app/strategies/concrete/volume_breakout.py` |
| 등록 | `STRATEGY_REGISTRY["volume_breakout"]` |
| 진입 | 거래대금 ≥ 평균 × 2.0 + 최근 20봉 종가 고점 돌파 + 세션 VWAP 상단 |
| 청산 | TP 4% / SL 2% / trailing 1.5% / 30봉 시간 청산 (`exit_rule`만 반환) |
| 추격 가드 | `max_vwap_distance_pct=3%`, `max_intraday_runup_pct=8%`, `open_cooldown_bars=5` |
| 운영 가드 | stale data, blocked regime(`trending_down`/`high_vol`/`blocked`), 일중 1회 진입 |
| 직접 주문 금지 | broker/risk/permission/execution import 0건 (테스트 가드), `is_order_intent=False` |

자세한 명세: [`docs/strategies/volume_breakout.md`](strategies/volume_breakout.md).
LIVE flag / broker 호출 / 기존 가드 분기 변경 0건 — frontend 변경 0건.
KIS LIVE place_order 활성화는 별도 옵트인 PR.

## 추가 (30) — PullbackRebreakStrategy

체크리스트 #30 눌림목 재돌파 2차 전략 구현. backend pytest 35건 신규
(`test_pullback_rebreak_strategy.py`).

| 항목 | 위치 |
|---|---|
| 전략 코드 | `app/strategies/concrete/pullback_rebreak.py` |
| 등록 | `STRATEGY_REGISTRY["pullback_rebreak"]` |
| 진입 | 1차 impulse(1.5~12%) + pullback(0.3~4%, 거래량 fade ≤ 85% of impulse) + peak 재돌파 + 재돌파 turnover ≥ 1.2× pullback 평균 |
| 청산 | TP 4% / SL 동적(pullback_low 기반) / trailing 1.5% / 30봉 시간 청산 / VWAP 이탈 invalidation |
| 추격 가드 4종 | `max_impulse_pct=12%`, `pullback_max_pct=4%`, `max_vwap_distance_pct=4%`, `max_intraday_runup_pct=12%`, `open_cooldown_bars=5` |
| 운영 가드 | stale data, blocked regime(`trending_down`/`high_vol`/`blocked`), 일중 1회 진입(패턴 검출 전 가드) |
| 과최적화 방지 | 모든 임계 명시 파라미터, impulse/pullback 양방향 hard-cap, 3종 거래량 검증, VWAP+runup 두 축 가드 |
| 직접 주문 금지 | broker/risk/permission/execution import 0건 (테스트 가드), `is_order_intent=False` |

VolumeBreakoutStrategy(#29)가 1차 첫 돌파를 잡는다면 본 전략은 *그 다음
안전한 진입 후보*를 노린다 — 추격매수 위험 감소가 설계 목표.

자세한 명세: [`docs/strategies/pullback_rebreak.md`](strategies/pullback_rebreak.md).
LIVE flag / broker 호출 / 기존 가드 분기 변경 0건 — frontend 변경 0건.

## 추가 (31) — VWAPStrategy + VWAP 유틸

체크리스트 #31 VWAP 회귀/이탈 보조 전략 + VWAP 계산 유틸 모듈 구현. backend
pytest 44건 신규 (`test_vwap_strategy.py`).

| 항목 | 위치 |
|---|---|
| 전략 코드 | `app/strategies/concrete/vwap_strategy.py` |
| 유틸 모듈 | `app/strategies/vwap.py` (신규) — `typical_price`/`vwap_of`/`extract_session_bars`/`session_vwap`/`rolling_vwap`/`vwap_deviation_pct`/`average_volume`/`average_turnover`/`check_liquidity` |
| 등록 | `STRATEGY_REGISTRY["vwap_strategy"]` |
| 진입 (BUY) | VWAP cross-up reclaim + 거래량 ≥ prior 평균 × 1.2 + 거래량/거래대금 임계 통과 + 괴리율 ≤ 1.5% |
| 청산 (EXIT) | VWAP cross-down + 보유 중(`position_context.has_open_position=True`) — 운영자/Agent surface 신호 |
| 거래량 부족 가드 | `check_liquidity`로 `avg_volume`/`avg_turnover` 임계 검사 → LOW_LIQUIDITY REJECT (소수 체결로 인한 VWAP 왜곡 방지) |
| 추격 가드 2-tier | `max_deviation_pct_for_entry=1.5%` (BUY 보류) / `overextension_deviation_pct=3%` (REJECT) |
| 운영 가드 | open cooldown, stale data, blocked regime, 일중 1회 진입 |
| 직접 주문 금지 | broker/risk/permission/execution import 0건 — 전략 + 유틸 모듈 모두 테스트 가드 |

자세한 명세: [`docs/strategies/vwap_strategy.md`](strategies/vwap_strategy.md).
LIVE flag / broker 호출 / 기존 가드 분기 변경 0건 — `OrbVwapStrategy`(orb_vwap.py)
는 자체 VWAP 누적 그대로 유지 (기존 동작 보존, 향후 통합 PR에서 정리).

## 추가 (32) — MarketRegimeFilter

체크리스트 #32 시장 국면 필터 구현. 지수 급락 / 변동성 확대 / 거래대금 위축
/ 장 초반 혼란 구간에서 신규 BUY를 제한하거나 차단하는 advisory layer.
backend pytest 30건 신규 (`test_market_regime_filter.py`).

| 항목 | 위치 |
|---|---|
| 필터 모듈 | `app/filters/market_regime.py` (신규 — `app/filters/` 패키지 신규) |
| 클래스 | `MarketRegimeFilter` + `RegimeDecision` + `MarketRegime`(8 enum) + `RegimeDecisionKind`(4 enum) |
| Strategy 연결 helper | `apply_regime_filter_to_signal(signal, regime_decision)` |
| 8 regime | TREND_UP / TREND_DOWN / CHOPPY / HIGH_VOLATILITY / LOW_LIQUIDITY / RISK_OFF / OPENING_CHAOS / UNKNOWN |
| 4 결정 | ALLOW / REDUCE_SIZE / WATCH_ONLY / BLOCK_NEW_BUY |
| BUY/SELL 분리 | `RegimeDecision.sell_allowed`는 항상 True — SELL/EXIT은 리스크 축소라 차단하지 않음 |
| 자동 연결 | 없음 — `LiveStrategyEngine` / `route_order`에 자동 적용 X. 운영자/Agent가 명시적 호출 |
| 직접 주문 금지 | broker/risk/permission/execution import 0건 — 테스트 가드 |
| 기존 호환성 | `app/market/regime.py`(135) 삭제/수정 0건 — 본 필터가 내부에서 `classify_regime`을 호출해 매핑만 함 |

자세한 명세: [`docs/market_regime_filter.md`](market_regime_filter.md).
KOSPI/KOSDAQ 실시간 지수 연동, sector breadth, volatility index, regime별
performance, regime-aware sizing은 [`docs/backlog.md`](backlog.md)에 추가.

## 추가 (33) — Signal Explainability

체크리스트 #33 신호 판정 근거 패널 — read/write audit 설명 레이어 구현.
backend pytest 37건 신규 (`test_explainability.py`).

| 항목 | 위치 |
|---|---|
| 모듈 | `app/explainability/` (신규 패키지) — `reasons.py` |
| 모델 | `SignalReason` (category/status/severity/source/code/message/details) + `SignalExplanation` |
| 카테고리 | STRATEGY / SIGNAL_QUALITY / MARKET_REGIME / RISK_MANAGER / PERMISSION_GATE / DATA_FRESHNESS / AGENT / OPERATOR / OTHER |
| 상태 | PASS / WARN / FAIL / BLOCKED / INFO + 최종 ExplainStatus (APPROVED/PENDING/REJECTED/WATCH/UNKNOWN) |
| Helpers | `compose_signal_explanation` (다단계 입력 합성) / `summarize_reasons` (2-3줄) / `classify_final_status` / `require_explanation_before_order` |
| API | `GET /api/signals/{audit_id}/explain` (read-only — `routes_explainability.py` + main.py 라우터 등록) |
| Audit 통합 | `extract_reasons_from_audit_row(row)` — OrderAuditLog row → SignalExplanation (read-only, 스키마 변경 0건) |
| "설명 없는 주문 금지" | `require_explanation_before_order(explanation)` — helper + tests + docs로 정책 명시 (기존 흐름에 자동 적용 X) |
| 직접 주문 금지 | broker/risk/permission/execution import 0건, `route_order(`/`place_order(` 호출 0건 — 테스트 가드 |

자세한 명세: [`docs/signal_explainability.md`](signal_explainability.md).
기존 OrderAuditLog / AgentDecisionLog / PendingApproval 스키마 변경 0건.

**2026-05-09 frontend 후속**: `SignalExplainabilityPanel.jsx` 추가, AuditLog 탭
OrderAuditRow에 [판정 근거 보기] 토글로 통합. PASS/WARN/FAIL/BLOCKED/INFO 그룹
별 reason 카드 + risk_notes / operator_note 섹션 + "Failed to fetch" 원문 노출
금지 친근 문구 처리. vitest 13개 신규 (1050 → 1063 frontend tests). lint 0
errors. build OK. `require_explanation_before_order` force-apply는 위험 평가
후 backlog로 유지 — 10+ 테스트 파일이 OrderRequest를 explanation 없이 구성하기
때문에 점진적 enforce_explanation 플래그 도입이 필요.

## 추가 (34) — RiskManager 표준 진입점

체크리스트 #34 RiskManager 표준화 — 모든 주문은 `RiskManager.check_order
(order, context)`를 통과해야 한다는 invariant를 코드 단에서 강제.

| 항목 | 위치 |
|---|---|
| 표준 진입점 | `RiskManager.check_order(order, context)` (`app/risk/risk_manager.py`) |
| 입력 dataclass | `RiskContext` — mode/balance/positions/latest_price/timestamp/requested_by_ai/market_regime_decision/emergency_stop_override/operator_id/metadata |
| 출력 보강 | `RiskCheckResult` — decision/reasons/passed + warnings/risk_score/blocked_by/required_action/normalized_order/evaluated_at + status/to_dict |
| RiskDecision enum | APPROVED / REJECTED / NEEDS_APPROVAL + REDUCED / BLOCKED 추가 (additive) |
| Market Regime 통합 | `BLOCK_NEW_BUY`/`WATCH_ONLY`이면 BUY를 BLOCKED, SELL은 통과 (#32 filter); `REDUCE_SIZE`는 advisory warning |
| Backstop 가드 | `OrderExecutor.execute`는 `audit.decision ∈ {APPROVED, NEEDS_APPROVAL}`만 broker.place_order 호출, 그 외는 `UnauthorizedOrderError` |
| 우회 차단 테스트 | `tests/test_risk_manager_bypass.py` (52 신규 테스트) — check_order 분기 + executor 가드 + import 가드 |
| 기존 호환성 | `evaluate_order` 시그니처/동작 그대로 유지 — check_order가 내부 위임. 27+ 가드 로직 무변경 |
| 직접 주문 금지 | 전략/필터/설명/마켓/신호품질 모듈에 `.place_order(` 호출 0건 — 테스트 가드 (15 모듈 paramaterized) |

자세한 명세: [`docs/risk_manager_contract.md`](risk_manager_contract.md).
backend pytest **1563 passed** (+52 신규). 기존 1511 → 1563. ruff clean.

## 추가 (35) — PositionLimitRule

체크리스트 #35 1회 거래금액 / 종목당 / 총 익스포저 / 최대 보유 종목 수 한도를
명시적인 rule 객체로 분리. RiskManager.evaluate_order의 inline 로직을 위임으로
교체. backend pytest 53건 신규 (`test_position_limits.py`).

| 항목 | 위치 |
|---|---|
| 모듈 | `app/risk/position_limits.py` (신규) — `PositionLimitRule` + `PositionLimitInput` + `PositionLimitPolicy` + `PositionLimitPreview` + `PositionLimitResult` |
| 7 한도 | max_order_notional / max_position_size_pct / max_positions / max_symbol_exposure / max_symbol_exposure_pct / max_total_exposure / max_total_exposure_pct |
| Preview | order_notional + projected_symbol_exposure + projected_total_exposure + projected_position_count + remaining_*_capacity / slots |
| RiskManager 연계 | `evaluate_order`이 inline 로직 대신 7개 `check_*` 메서드 호출 + 결과 merge — single source of truth |
| 기존 호환성 | `result.reasons` / `result.passed` 문자열 / 순서 모두 동일 — 기존 26+ 테스트 무수정 통과 |
| Side-aware | BUY는 노출 증가 검사. SELL/청산은 max_positions / total_exposure / symbol_exposure_pct 우회 |
| 경계값 테스트 | 한도 미만/같음/초과 boundary 모두 검증 (정확히 한도와 같은 값은 통과 — `>` 비교) |
| Futures 분리 | 본 rule은 현물 명목금액 기준만; `FuturesRiskPolicy`는 별도 / `app.futures` import 0건 (테스트 가드) |
| 직접 주문 금지 | broker/permission/execution import 0건, route_order/place_order 호출 0건 — 테스트 가드 |

자세한 명세: [`docs/position_limit_policy.md`](position_limit_policy.md).
backend pytest **1616 passed** (+53 신규). 1563 → 1616. ruff clean.

## 추가 (36) — Loss Limit Rules

체크리스트 #36 일일 / 주간 / 연속 손실한도를 명시적인 rule 객체로 분리. 손실
도달 시 신규 BUY 차단, SELL/EXIT은 통과. backend pytest 43건 신규
(`test_loss_limits.py`).

| 항목 | 위치 |
|---|---|
| 모듈 | `app/risk/loss_limits.py` (신규) — `DailyLossLimitRule` + `WeeklyLossLimitRule` + `ConsecutiveLossRule` + `LossLimitDecision` enum + `evaluate_loss_limits` helper |
| 헬퍼 | `app/risk/daily_pnl.py`에 `compute_weekly_realized_pnl_kst` + `count_consecutive_losing_trades` + `week_start_kst` 추가 (월요일 00:00 KST 시작) |
| 4단계 결정 | ALLOW / WARN / REDUCE_SIZE / BLOCK_NEW_BUY |
| Daily soft 단계 | `daily_loss_warn_pct` (e.g., 50%) / `daily_loss_reduce_pct` (e.g., 70%) — 기존 `max_daily_loss` 100% hard reject은 그대로 |
| Weekly | `weekly_loss_limit` + warn/reduce pct |
| Consecutive | `consecutive_loss_limit` — trailing closed trades가 연속 손실인 횟수 |
| BUY/SELL 분리 | BUY는 reasons로 REJECTED, SELL/EXIT은 warnings로만 surface (리스크 축소 보호) |
| RiskManager 연계 | `evaluate_order`에 `weekly_realized_pnl` / `consecutive_loss_count` keyword-only 인자 추가 (default None — 기존 호출자 호환) |
| route_order 연계 | 매 평가 직전 `compute_weekly_realized_pnl_kst` + `count_consecutive_losing_trades` 호출해 RiskManager에 주입 (rule 활성 시에만) |
| REDUCE_SIZE TODO | RiskCheckResult가 사이즈 직접 조정 미지원 — warnings로만 surface, PositionSizingAgent 통합은 backlog |
| 직접 주문 금지 | broker/permission/execution import 0건, place_order/route_order 호출 0건 |

자세한 명세: [`docs/loss_limit_policy.md`](loss_limit_policy.md).
backend pytest **1659 passed** (+43 신규). 1616 → 1659. ruff clean.

## 추가 (37) — 3-Level Kill Switch

체크리스트 #37 emergency_stop을 OFF/LEVEL_1/LEVEL_2/LEVEL_3 단계로 분리.
운영자가 단계적으로 가드를 강화하고, 청산은 *수동 승인*으로 진행 (자동 전량
청산 절대 금지). backend pytest 35건 신규, frontend vitest 9건 신규.

| 항목 | 위치 |
|---|---|
| 모듈 | `app/risk/emergency_stop.py` (신규) — `KillSwitchLevel` enum + `KillSwitchStatus` + candidate dataclasses + helpers |
| 마이그레이션 | `alembic/versions/20260521_0017_emergency_stop_level.py` — `EmergencyStopEvent.level` 컬럼 추가 (nullable). legacy NULL row는 enabled=True/False에 따라 LEVEL_1/OFF로 정규화 |
| POST 라우트 확장 | `POST /risk/emergency-stop`이 `level` 필드 수용. 응답에 `level` carry. enabled=True + level 미지정은 LEVEL_1로 매핑 (기존 호환성) |
| 신규 read-only 라우트 | `GET /risk/emergency-stop/status` (현재 level + 후보 카운트), `/cancel-candidates` (NEEDS_APPROVAL 미체결), `/liquidation-candidates` (보유 포지션 — broker.get_positions read-only) |
| 자동 청산 금지 | broker.cancel_order / broker.place_order / route_order 호출 **0건** — 테스트 가드 (모듈 + 라우트 grep). LEVEL_3에서도 read-only candidate list만 표시 |
| Frontend | `frontend/src/components/common/KillSwitchPanel.jsx` (신규) — StrategyRisk 탭에 mount. 3단계 row 시각화 + 후보 카운트 + 위험 경고 문구. 자동 청산 / 자동 취소 버튼 부재 (테스트 가드) |
| 기존 호환성 | `RiskManager.emergency_stop` boolean 그대로 (LEVEL_1+ 일 때 True). `POST /emergency-stop` 응답에 `level` 필드 추가 (additive). history 응답도 level 필드 추가 |

자세한 명세: [`docs/emergency_stop_policy.md`](emergency_stop_policy.md).
backend pytest **1694 passed** (+35 신규). 1659 → 1694. frontend vitest **1059
passed** (+9 신규). 1050 → 1059. ruff/lint clean. build OK.

## 추가 (38) — OrderGuard

체크리스트 #38 중복 주문 / 쿨타임 / 미체결 가드 + 네트워크 재시도 vs 중복
구분. RiskManager 평가 *전*에 흐름 차원 가드를 적용. backend pytest 31건
신규 (`test_order_guard.py`).

| 항목 | 위치 |
|---|---|
| 모듈 | `app/risk/order_guard.py` (신규) — `OrderGuard` + `OrderGuardConfig` + `OrderGuardResult` + `GuardDecision` enum + `build_order_fingerprint` |
| Fingerprint | symbol + side + qty + order_type + price_bucket(0.5%) + strategy + mode + agent_chain_id → SHA-256 12-hex prefix. Secret 미포함 |
| 4 결정 | ALLOW / RETRY_REPLAY / DUPLICATE / COOLDOWN / PENDING_BLOCKED |
| Idempotency 분기 | 같은 client_order_id → RETRY_REPLAY (안전), 다른 key 같은 fingerprint → DUPLICATE (차단) |
| 4 cooldown | symbol / (strategy, symbol) / post-exit / AI extra. 모두 default 0 = 비활성 |
| Pending guard | 같은 symbol + side의 PendingApproval(PENDING) + OrderAuditLog(NEEDS_APPROVAL) drift 시 신규 차단 |
| RiskPolicy 7 신규 필드 | order_guard_* (모두 default 0/False) — 운영자 명시 활성화 시에만 동작 |
| route_order 통합 | client_order_id 검사 직후, RiskManager 평가 *전*에 OrderGuard.check 호출. 차단 시 broker 호출 회피 + REJECTED audit row 작성 |
| 직접 주문 금지 | broker.place_order / cancel_order / OrderExecutor / route_order import 0건 — 테스트 가드 |
| 기존 호환성 | default policy(모두 0)면 사실상 no-op — 기존 187+ 테스트 무수정 통과 |

자세한 명세: [`docs/order_guard_policy.md`](order_guard_policy.md).
backend pytest **1725 passed** (+31 신규). 1694 → 1725. ruff clean.

## 추가 (39) — AI Permission Gate

체크리스트 #39 AI 주문 권한을 5단계 × 5행동 매트릭스로 명시 분리. AI API Key
가 주문 권한 조건이 *아니라는* invariant를 코드 단에서 강제 (모듈 입력에
api_key / secret 필드 0건 — 테스트 가드). backend pytest 43건 + frontend
vitest 9건 신규.

| 항목 | 위치 |
|---|---|
| 모듈 | `app/risk/ai_permission_gate.py` (신규) — `AiPermissionLevel` (5단계) + `AiAction` (5종) + `AiPermissionFlags` + `evaluate_ai_permission` + `current_ai_level` + `build_status` + `build_permission_matrix` |
| 5단계 | FULL_STOP / RECOMMEND_ONLY / APPROVAL_REQUIRED / VIRTUAL_EXECUTION / LIMITED_LIVE_EXECUTION |
| 5행동 | RECOMMEND / SUBMIT_FOR_APPROVAL / VIRTUAL_EXECUTE / LIVE_EXECUTE / FUTURES_LIVE_EXECUTE |
| Read-only API | `GET /api/risk/ai-permission/status` — 현재 level + allowed/blocked actions + flags + matrix + 안내 문구 |
| Frontend | `frontend/src/components/common/AiPermissionCard.jsx` (신규) — StrategyRisk 탭에 mount. 권한 행사 / 토글 버튼 부재 (테스트 가드) |
| API Key 분리 | `AiPermissionFlags`에 api_key/secret/account_no 필드 **0건** — 테스트 `test_module_does_not_take_api_key`로 강제. 모듈에서 `app.brokers` import 0건 |
| 기존 호환성 | 본 게이트는 기존 `RiskManager.evaluate_order`의 `disable_ai_orders` / `min_ai_confidence` / `enforce_ai_reasoning` / `can_ai_execute` 검사를 *대체하지 않는다* — 명시 표시 + audit_note 생성 layer |

자세한 명세: [`docs/ai_permission_gate.md`](ai_permission_gate.md).
backend pytest **1768 passed** (+43 신규). 1725 → 1768. frontend vitest **1068
passed** (+9 신규). 1059 → 1068. ruff/lint clean. build OK.

## 추가 (40) — Order Executor 표준 진입점

체크리스트 #40 OrderExecutor를 표준 주문 실행 계층으로 강화. 단일 진입점
invariant + source 분류 + 16개 라우트 + 12개 전략/필터/Agent 모듈 직접 broker
호출 0건 가드. backend pytest 60건 신규 (`test_order_executor.py`).

| 항목 | 위치 |
|---|---|
| Alias 모듈 | `app/execution/order_executor.py` (신규) — `OrderExecutor` / `UnauthorizedOrderError` re-export + `OrderSource` enum (5종) + `derive_order_source` helper |
| DB 컬럼 | `OrderAuditLog.source` (nullable String(32), indexed). 0018 마이그레이션 (`alembic/versions/20260522_0018_order_audit_source.py`). legacy NULL row는 그대로 — frontend 'UNKNOWN' 표시 권장 |
| Source 분류 | AI (requested_by_ai 최우선) / STRATEGY (strategy 필드) / MANUAL / OPERATOR_OVERRIDE (explicit) / UNKNOWN (legacy) |
| route_order 통합 | guard_audit + 본 audit 두 곳 모두 `derive_order_source(...)` 결과 채움 |
| API surface | `OrderAuditOut.source` (optional) — `/api/audit/orders` 응답 carry |
| 직접 broker 호출 금지 가드 | 16 API routes + 12 strategy/filter/agent/explainability/risk/permission 모듈에 `broker.place_order(` / `BrokerAdapter.place_order(` 호출 **0건** — paramaterized grep 테스트로 강제 |
| 단일 진입점 가드 | `OrderExecutor.execute`만 `broker.place_order()` 호출 — `permission/gate.py` / `order_router.py` 모두 OrderExecutor 경유 |
| 기존 호환성 | `from app.execution.executor import OrderExecutor` 그대로 작동. 시그니처 / route_order contract / OrderAuditLog 다른 컬럼 변경 0건 |

자세한 명세: [`docs/order_executor_contract.md`](order_executor_contract.md).
backend pytest **1828 passed** (+60 신규). 1768 → 1828. ruff clean.

## 추가 (41) — Manual Approval 보강

체크리스트 #41 Manual Approval Queue를 실전 전 안전 게이트로 완성. 기존
PermissionGate / approve-reject-cancel / RiskManager 재검증 구조는 그대로
유지하면서 TTL 정책 + ApprovalOut 보강 + request_source 분류 + UI 배지를 추가.
backend pytest 17건 + frontend vitest 10건 신규.

| 항목 | 위치 |
|---|---|
| TTL 정책 wire | `Settings.approval_ttl_seconds` (이미 존재, 0 default)를 `routes_approvals.list_pending` / `list_history` / `get` / `approve_route`에 연결 — `PermissionGate.list_pending(ttl_seconds=...)`이 lazy expire |
| ApprovalOut 보강 | 신규 8 필드: `expires_at` / `seconds_until_expiry` / `is_expired` / `attempt_count` / `last_attempt_at` / `last_attempt_reasons` / `request_source` / `request_source_label` |
| request_source 분류 | AI / STRATEGY / MANUAL / LIQUIDATION / RISK_OVERRIDE / UNKNOWN. audit.source(#40) + requested_by_ai + strategy + trade_reason 합산 |
| Frontend 배지 | `<RequestSourceBadge>` (출처별 색상) + `<ApprovalExpiryBadge>` (TTL 카운트다운, 1m 미만 amber, 만료 red) — `Approvals.jsx` pending row에 mount |
| Helpers | `_derive_request_source` / `_ttl_fields` / `_attempts_summary` / `_REQUEST_SOURCE_LABELS` |
| 기존 호환성 | 모든 신규 필드 optional/default — 기존 클라이언트 무시해도 동작. 92개 기존 결재/permission/virtual_flow 테스트 무수정 통과 |
| 자동 변경 | 0건 — 실제 주문 실행 로직 추가 X, broker / OrderExecutor / RiskManager 변경 X |

자세한 명세: [`docs/manual_approval_policy.md`](manual_approval_policy.md).
backend pytest **1845 passed** (+17 신규). 1828 → 1845. frontend vitest **1078
passed** (+10 신규). 1068 → 1078. ruff/lint clean. build OK.

## 추가 (42) — Paper Trading

체크리스트 #42 Paper Trading을 명확화. PaperTrader 계층 추가, MockBroker/
KIS Paper 선택 가능, paper-safe 가드 + 모의투자 체결 품질 주의 문서화.
backend pytest 26건 + frontend vitest 9건 신규.

| 항목 | 위치 |
|---|---|
| 모듈 | `app/execution/paper_trader.py` (신규) — `PaperTrader` (OrderExecutor wrapper) + `PaperBrokerKind` enum (MOCK/KIS_PAPER) + `is_live_broker` / `is_paper_broker` / `assert_paper_broker` (`NotPaperBrokerError`) + `make_paper_broker` selection + `build_paper_status` |
| Settings | `Settings.paper_broker_kind` 추가 (default 빈 문자열 — `_default_paper_broker_kind`로 추론) |
| API | `GET /api/paper/status` (read-only) — mode + broker_kind + 5 안전 flag + 체결 품질 주의 안내 |
| Frontend | `frontend/src/components/common/PaperModeStatusCard.jsx` (신규) — StrategyRisk 탭에 mount. 4 flag 시각화 + 주문/test 버튼 부재 (테스트 가드) |
| Live 차단 다층 방어 | (1) `Settings.kis_is_paper=true` 강제, (2) `KisBrokerAdapter.place_order(is_paper=False)` `NotImplementedError`, (3) `assert_paper_broker(broker)` runtime 가드, (4) PaperTrader 인스턴스 단계 + 매 execute 호출 시 재검증, (5) OrderExecutor audit decision 검증 |
| RiskManager 우회 방지 | `PaperTrader.execute(order, audit)`은 OrderExecutor에 위임만 — RiskManager / route_order 우회 진입점 0건. 테스트로 강제 |
| 직접 broker 호출 금지 | `paper_trader.py`에 `broker.place_order(` / `BrokerAdapter.place_order(` / `.place_order(` 호출 형태 0건 — 테스트 가드 |
| 모의투자 체결 품질 주의 | `/api/paper/status` 응답 + Frontend 카드 + docs에 명시: "체결 시간/슬리피지/부분체결 패턴이 실 시장과 다를 수 있다" |
| KIS Paper rate limit | `docs/paper_trading_policy.md` §6에 EGW00201 + 1.2초 권장 간격 명시 |

자세한 명세: [`docs/paper_trading_policy.md`](paper_trading_policy.md).
backend pytest **1871 passed** (+26 신규). 1845 → 1871. frontend vitest **1087
passed** (+9 신규). 1078 → 1087. ruff/lint clean. build OK.

## 추가 (UI Redesign 후속) — Agent-Centered Operator Experience

사용자 추가 피드백에 대응한 frontend UI 라운드. 3가지 pain point (에이전트
중심성 약함 / PC·모바일 동일 구조 / 전략 선택 흐름 불명확)에 집중. 자세한
진행: [`docs/ui_redesign_report.md`](ui_redesign_report.md) "후속" 섹션.

| 변경 | 위치 |
|---|---|
| `AgentStrategyChoiceCard` (신규) | `frontend/src/components/common/AgentStrategyChoiceCard.jsx` — 4 전략 chip + 활성 강조 + 선택 이유. read-only (토글/주문 버튼 부재 — 테스트 가드) |
| 모바일/PC 분기 | `frontend/src/index.css` `.dashboard-pc-only` 추가. Activity24h / AgentLatestTile / WatchlistSummaryTile / ThemeSummaryTile 모바일 기본 숨김 |
| Dashboard mount | AgentDecisionHero → AgentStrategyChoiceCard 인접 배치 (AI 판단 → 전략 선택 흐름 시각화) |
| EmergencyStopStuckBanner | 글자 11→13/14px, 색 대비 amber-800로 향상 |
| 테스트 | 8 신규 (AgentStrategyChoiceCard) |
| backlog | legacy 탭 토큰화, BottomNav 5탭, PC 3열 grid, Agent decision history 그래프 |

frontend vitest **1095 passed** (+8 신규). 1087 → 1095. lint 0 errors. build
OK 478 KB → 131 KB gzipped. backend 변경 0건 — broker/risk/permission/
execution/.env/API contract 무수정.

---

## 배포 / 접속 / 보안 체크리스트 (deployment-checklist)

운영자 / 베타테스터가 *직접 단계별로 점검*할 수 있는 통합 체크리스트 작성. 본 문서는 코드 변경이 *없으며* 배포 / 접속 / 보안 운영 정책을 0단계부터 12단계까지 표 형식으로 정리한다 (연번 / 항목 / 설명 / 내가 할 일 / 완료 기준 / 주의사항).

### 새로 작성된 문서

| 문서 | 내용 |
|---|---|
| `docs/deployment_checklist.md` | **신규** — 0~12단계 연번 체크리스트 + 용어 한 줄 요약 (Tailscale / PWA / Tauri / Electron / 자동 업데이트 / Code signing) + 15개 절대 원칙 요약 |
| `docs/deployment_strategy.md` | (기존) 전체 배포 정책 — 로컬-우선 / Tailscale / 포트포워딩 금지 |
| `docs/mobile_access_guide.md` | (기존) LAN / Tailscale 접속 절차 / 긴급중단 위치 |
| `docs/beta_distribution_plan.md` | (기존) 각자 PC 설치 + Tauri 우선 + Electron 대안 + GitHub Releases 배포 |
| `docs/auto_update_plan.md` | (기존) Phase 1-2-3 (수동→알림→자동) + 버전 일치 정책 |
| `docs/local_security_policy.md` | (기존) Secret hygiene / Tailscale / 9종 sanitize / 사고 대응 |

### 12단계 핵심 (요약)

- **0단계**: 운영 형태 (개인 + 베타테스터) / repo private 전환 / LIVE flag default false 확인
- **1단계**: Local / LAN / Tailscale / Pages-Demo 4 환경 인지 + "본체 vs 리모컨" 모델
- **2단계**: backend / frontend 실행 / SIMULATION 확인 / 절전 비활성 / Windows Update 활성 시간
- **3단계**: 같은 Wi-Fi 폰 접속 (`--host 0.0.0.0` + PC IP)
- **4단계**: 외부 폰 접속 — **Tailscale 권장 / 포트포워딩 금지**
- **5단계**: 베타테스터 배포 — 운영자 `.env` *미공유* / 각자 KIS 모의 / 압축 → Tauri → Docker 단계
- **6단계**: Tauri 데스크톱 앱 (1순위) / Electron (대안) / Code signing 후속
- **7단계**: 자동 업데이트 — Phase 1 수동 → Phase 2 알림 (1차 우선) → Phase 3 자동
- **8단계**: 인증 — 본 시점 *신뢰 네트워크 가정*. 관리자 비밀번호 / passkey 후속
- **9단계**: Secret 관리 — `.env` 추적 0건 / `.env.example` placeholder만 / frontend `VITE_*` 공개 가능 값만
- **10단계**: 네트워크 보안 — 포트포워딩 0건 / Tailscale ACL / Wi-Fi 분리 / Defender / OS 패치
- **11단계**: 베타 운영 — 명단 ≤ 5명 / 4주+ / feedback 채널 1개 / 자격증명 미공유
- **12단계**: 실거래 전 최종 — repo private / promotion gate 8개 / 일일 점검 루틴 / *옵트인 PR*

### 15개 절대 원칙 명시

본 체크리스트는 다음 원칙을 모든 단계에서 일관되게 강제:

1. 외부 공개 서버 운영 X / 2. 포트포워딩 X / 3. Tailscale 우선 / 4. PC 켜져 있어야 / 5. 베타테스터는 각자 PC / 6. 운영자 `.env` 공유 X / 7. 실 API 전 repo private / 8. Pages는 UI Demo만 / 9. 실 backend는 local + private server / 10. 자동 업데이트 후속 / 11. Tauri 1순위 / 12. Electron 대안 / 13. PWA는 모바일 관제 / 14. 푸시 알림 보안 검토 후 / 15. **LIVE/AI/FUTURES flag default false**.

### 변경된 코드 / 테스트

코드 변경 0건. README에 배포 체크리스트 링크 섹션 추가만. backend / broker / risk / permission / executor / .env / API contract / 안전 flag 모두 무수정.

### 안전 invariant 재확인

- ✓ 실 broker `place_order`/`cancel_order` 호출 0건
- ✓ `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 변경 0건 (모두 default false)
- ✓ API key / Secret / 계좌번호 변경 0건
- ✓ frontend Secret 저장 0건
- ✓ docs에 *포트포워딩 금지* / *Tailscale 권장* / *PC 켜져 있어야* / *베타테스터에게 운영자 Secret 공유 금지* 모두 명시
- ✓ `.env`는 `.gitignore` 등록 + `.env.example`은 placeholder만

## 추가 (#61) — Approval UI 핵심 구조화

체크리스트 #61: AI / 전략 제안의 승인 대기 화면을 *AI 매매 전환용 핵심 UI* 로
구조화. broker API 호출 / approve API contract / LIVE flag 0건 변경 — 표시
로직 / 모달 강화 / 테스트 / 문서만.

### 신규 파일
- `frontend/src/components/tabs/ApprovalQueue.jsx` — 6개 sub-component:
  - `ApprovalFreshnessBadge` — TTL 우선, 없으면 created_at age fallback
  - `ApprovalProposalSummary` — 제안 출처 / 전략 / confidence / supporting/
    opposing reasons / expected reward·risk / "주문 아님" 안내
  - `ApprovalRiskSummary` — RiskManager 사유 5+1 카테고리 (freshness /
    position / loss / ai / guard / other), reasons 비면 "표시 가능한 리스크
    사유 없음" + 재검증 안내
  - `ApproveConfirmSummary` — 승인 모달용 상단 요약 + stale 경고 + 사유 top 3
  - `ApprovalActionBar` — 모바일 동등 너비 버튼 행
  - `ApprovalQueueEmptyState` — empty / demo / loading 상태 분기
- `frontend/src/components/tabs/ApprovalQueue.test.jsx` — 신규 ApprovalQueue
  sub-component 테스트 24건
- `docs/approval_ui.md` — 정책 / 화면 구성 / 안전 원칙 / EXPIRED vs CANCELLED /
  모바일 정책 / 컴포넌트 트리 / 절대 invariant

### 변경 파일
- `frontend/src/components/tabs/Approvals.jsx` — 새 sub-component 통합:
  - PENDING row에 `ApprovalProposalSummary` + `ApprovalRiskSummary` +
    `ApprovalFreshnessBadge` 추가
  - 승인 모달 제목 "정말 승인하시겠습니까?"로 강화, `ApproveConfirmSummary`
    상단 추가, action별 description 분리
  - History row에 영문 status 옆 한국어 라벨 (`승인 / 거부 / 운영자 취소 /
    시간 만료`) 추가 — EXPIRED와 CANCELLED 명시 분리
  - Empty state / Error state 친화화 — raw `Failed to fetch` 원문 미노출
    (`friendlyErrorMessage` 경유)

### 안전 invariant 단정문

- ✓ 본 PR에서 `broker.place_order` / `cancel_order` 호출 추가 0건
- ✓ `/api/approvals/{id}/approve|reject|cancel` API contract 변경 0건
- ✓ `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING`
  변경 0건 (모두 default false)
- ✓ `.env` / KIS app_key / secret / Anthropic key 변경 0건
- ✓ 본 UI는 LIVE_AI_EXECUTION 활성화 / 자동 매매 토글을 노출하지 않음
- ✓ ApprovalProposalSummary는 "주문은 아직 실행되지 않았습니다" 명시 — AI
  자동 발주 환상 차단
- ✓ ApprovalRiskSummary는 reasons 비어도 "위험 없음" 단언 금지

### 테스트 결과

- Frontend `vitest run`: 신규 24건 + 기존 1365건 회귀 무 — 전체 PASS
- Backend 변경 0건, pytest 회귀 없음 (영향 받는 backend 테스트 없음)
- Lint / typecheck (frontend): `npm run lint` PASS, `vite build` PASS

## 추가 (#64) — Notifications (Telegram 1차 채널)

체크리스트 #64: 장중 위험 이벤트를 운영자에게 즉시 알리는 인프라. 1차 채널은
Telegram (BotFather + sendMessage). **위험 알림(CRITICAL/WARN) 우선**, 주문
성공 알림은 기본 미구현. 알림 실패가 주문 / 리스크 판단을 깨뜨리지 않도록
모든 송신 경로가 raise하지 않게 설계.

### 신규 파일
- `backend/app/notifications/__init__.py` — 패키지 export
- `backend/app/notifications/types.py` — NotificationEvent / Severity /
  NotificationKind / SendResult / NotificationChannel ABC. 이벤트 message에
  Secret 패턴(kis_app_key / anthropic_api_key / telegram_bot_token / bearer /
  sk-) 포함 시 ValueError로 fail-closed
- `backend/app/notifications/channels.py` — NoOpChannel / TelegramChannel.
  stdlib urllib만 사용 (외부 의존성 0). timeout 5s + retry 1회 기본.
  send는 raise하지 않음
- `backend/app/notifications/service.py` — NotificationService. enabled +
  min_severity + dedupe(in-memory) + always_send_critical 게이트
- `backend/app/notifications/templates.py` — 8개 builder
  (emergency_stop / data_stale / approval_pending / daily_loss_warning /
   broker_error / repeated_rejection / margin_risk / risk_auditor_warn)
- `backend/app/api/routes_notifications.py` — GET /status, POST /test,
  POST /mock-event. Token / chat_id 응답 노출 0건
- `backend/tests/test_notifications.py` — 26건
- `backend/tests/test_notifications_routes.py` — 14건
- `frontend/src/components/common/NotificationStatusCard.jsx` — 활성/채널/
  Telegram 구성/min severity/dedupe 표시 + "Token은 backend/.env에만"
  안내 + 🧪 테스트 버튼. Token 입력 input 0개
- `frontend/src/components/common/NotificationStatusCard.test.jsx` — 10건
- `docs/notification_policy.md` — 정책 / Telegram 설정 / 우선순위 / Secret
  관리 / 운영 주의 / 후속 과제

### 변경 파일
- `backend/app/core/config.py` — Settings에 NOTIFICATIONS_*, TELEGRAM_* 추가
- `backend/.env.example` — placeholder 추가 (빈 값)
- `backend/app/api/routes_risk.py` — emergency-stop POST에 try/except 알림
  hook (실패해도 응답 영향 0)
- `backend/app/main.py` — notifications_router 등록
- `frontend/src/services/backend/client.js` — 3개 신규 API 메서드
- `frontend/src/components/tabs/StrategyRisk.jsx` — NotificationStatusCard 마운트
- `README.md` — docs/notification_policy.md 링크

### Telegram 설정 (요약)
- BotFather → `/newbot` → token 발급
- chat_id 확인 (`getUpdates` JSON)
- `backend/.env` 에 `NOTIFICATIONS_ENABLED=true`, `TELEGRAM_BOT_TOKEN=`,
  `TELEGRAM_CHAT_ID=` 입력
- `POST /api/notifications/test` 또는 frontend 카드의 "🧪 테스트 알림 보내기"
- 자세한 절차: `docs/notification_policy.md` §3

### 안전 invariant
- ✓ `NotificationService.notify` raise 0건 (테스트로 lock)
- ✓ `TelegramChannel.send` raise 0건 — 모든 예외 SendResult.error로 carry
- ✓ emergency-stop API의 알림 hook은 try/except로 감싸 200 응답 유지
  (`test_emergency_stop_toggle_does_not_raise_even_if_notification_path_fails`)
- ✓ NotificationEvent는 message에 Secret 패턴 발견 시 ValueError
- ✓ `service.status()` / `/api/notifications/status` 응답에 token / chat_id
  값 0건 (테스트로 lock — JSON 직렬화 검사)
- ✓ frontend에 token / chat_id 입력 input / textarea / select 0개
- ✓ broker.place_order / cancel_order / route_order 호출 추가 0건
- ✓ ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / FUTURES_LIVE 변경 0건
- ✓ KIS / Anthropic key 변경 0건
- ✓ Telegram timeout 5s + retry 1회 → 합산 ≤ 10s 보장

### 테스트 결과
- Backend pytest 신규: **43 / 43 PASS** (`test_notifications.py` 26 +
  `test_notifications_routes.py` 14 + 추가 3건)
- Frontend vitest 신규: **10 / 10 PASS** (`NotificationStatusCard.test.jsx`)
- 전체 frontend regression: 1448 중 1447 PASS — 1건은 pre-existing
  Approvals.stress.test 타이밍 flake (격리 실행 시 PASS, 본 PR과 무관)
- Frontend `npm run build`: 성공 (141ms)
- Backend regression: `test_routes.py::test_status_exposes_safety_flags`만
  실패 — pre-existing 환경 이슈 (.env `DEFAULT_MODE=PAPER` vs 테스트 기대값
  `SIMULATION`). 본 PR과 무관

## 추가 (#65) — Unit Test Coverage (P0 모듈)

체크리스트 #65: 돈이 걸릴 수 있는 자동매매 시스템에서 **테스트 없이는 P0
모듈을 완료로 보지 않는다**는 정책을 문서화하고, RiskManager / OrderGuard /
StrategyBase / BacktestEngine 4개 핵심 모듈의 boundary 테스트 gap을 보강.

### 신규 파일
- `docs/unit_test_coverage_map.md` — P0 모듈 매핑 / 정책 / 시나리오 매트릭스
  / 미완료 backlog / 절대 invariant

### 변경 파일 (테스트만 추가, 운영 로직 변경 0건)
- `backend/tests/test_risk_manager.py` (+7 tests, **84 → 91**):
  - `test_sell_bypasses_max_positions_when_buy_blocked` — SELL은 한도 무관
  - `test_sell_bypasses_max_order_notional_via_size_check_when_under_position`
  - `test_check_order_with_block_new_buy_blocks_buy_but_lets_sell_through` —
    MarketRegimeFilter BLOCK_NEW_BUY 게이트 (BUY만 차단)
  - `test_check_order_emergency_stop_override_blocks_immediately`
  - `test_check_order_emergency_stop_runtime_flag_maps_to_blocked`
  - `test_check_order_stale_price_maps_to_blocked_with_wait_action`
  - `test_check_order_live_trading_disabled_maps_to_blocked`
- `backend/tests/test_order_guard.py` (+2 tests, **31 → 33**):
  - `test_rejected_audit_within_window_still_triggers_duplicate` — 안전한
    보수적 분류 invariant lock
  - `test_combined_cooldown_and_pending_returns_pending_first` — 복합 가드
    활성 시 ALLOW 안 됨 invariant
- `backend/tests/test_backtest_engine.py` (+4 tests, **21 → 25**):
  - `test_engine_rejects_non_positive_initial_cash`
  - `test_engine_rejects_non_positive_quantity`
  - `test_empty_bars_yields_zero_trades_and_full_cash`
  - `test_summarize_metrics_smoke`
- `backend/tests/test_strategy_base_contract.py` (+5 tests, **21 → 26**):
  - `test_to_legacy_signal_maps_all_five_action_values` — SignalAction 5개
    (BUY/SELL/EXIT/WATCH/NO_SIGNAL) → legacy Signal 매핑
  - `test_strategy_context_carries_all_optional_fields`
  - `test_exit_plan_carries_time_exit_and_invalidation`
  - `test_sizing_hint_with_reduce_only_flag`
  - `test_from_legacy_signal_hold_yields_no_signal`

### P0 모듈 테스트 합계
- RiskManager: **91 tests**
- OrderGuard: **33 tests**
- StrategyBase: **26 tests**
- BacktestEngine: **25 + 20 = 45 tests** (engine + execution_costs)
- 총 **195건 P0 단위 테스트**

### 안전 invariant
- ✓ 본 PR은 *테스트만 추가* — `app/` 운영 로직 변경 0건
- ✓ 따라서 broker / 주문 / LIVE flag / Secret 변경이 자명하게 0건
- ✓ 외부 API 호출 0건 — 모든 신규 테스트는 in-memory MockBrokerAdapter +
  fake settings 사용
- ✓ SELL/청산 정책 분리 시나리오를 단위 테스트로 명시 lock —
  `test_sell_bypasses_*` 3건

### 테스트 결과
- **신규 18건 모두 PASS** (RiskManager 7 + OrderGuard 2 + BacktestEngine 4 +
  StrategyBase 5)
- **P0 4개 모듈 합산: 175 PASS** (engine + execution_costs 합산 시 195 PASS)
- backend regression: `test_status_exposes_safety_flags`만 실패 — pre-existing
  환경 이슈 (#60부터 알려진 flake, 본 PR과 무관)

## 추가 (#66) — Integration Tests (signal → risk → order → fill → position)

체크리스트 #66: 단위 테스트가 검증한 각 모듈이 *실제로 서로 연결되어 정상
작동하는지* narrative 형태의 통합 테스트로 lock. 운영자/감사가 "신호 한 건이
broker까지 어떻게 흘러가나"를 한 파일에서 읽을 수 있도록 14건의 시나리오를
`test_order_flow.py`에 모음. 운영 로직(`app/`) 변경 0건 — 테스트와 문서만.

### 신규 파일
- `backend/tests/test_order_flow.py` — 14 시나리오 (signal → risk → order
  → fill → position 단일 파이프라인 narrative)
- `docs/integration_test_policy.md` — 정책 / 파이프라인 다이어그램 / 시나리오
  매핑 / 후속 backlog / 5개 invariant

### `test_order_flow.py` 14 시나리오
1. SIMULATION BUY → MockBroker FILLED → position/cash 갱신
2. BUY → SELL round trip → 청산 + cash 복귀
3. RiskManager REJECTED (notional 초과) → broker 미호출
4. LIVE_MANUAL_APPROVAL → NEEDS_APPROVAL → 운영자 승인 → 체결
5. LIVE_MANUAL_APPROVAL → 운영자 거부 → broker 미호출
6. Emergency stop ON → 신규 BUY REJECTED + broker 미호출
7. `route_order(...)` 직접 호출 BUY+SELL (HTTP 우회 trace)
8. Strategy → LiveStrategyEngine.submit_tick → MockBroker
9. 동일 client_order_id 재시도 → `DuplicateOrderError`
10. broker = MockBrokerAdapter invariant (실 broker 등장 0건)
11. enable_live_trading default false
12. test DB = in-memory SQLite
13. Settings key 필드 type only (값 의존 X)
14. **외부 네트워크 차단 시에도 파이프라인 정상 작동** — `socket.create_
    connection`을 monkeypatch로 막은 상태에서 simulation 주문 PASS — 통합
    테스트가 외부 네트워크에 의존하지 *않음*을 런타임 lock

### 통합 테스트 합계 (#66 기준 정리)
- `test_order_flow.py` (#66): **14건** ← 본 PR 신규
- `test_e2e_approval_order_flow.py`: 8건
- `test_virtual_flow_e2e.py`: 10건
- `test_all_guards_integration.py`: 14건
- `test_auto_trader_e2e.py` (#60): 18건
- **통합 합계: 64건**

### 안전 invariant
- ✓ 본 PR은 *테스트와 문서만 추가* — `app/` 운영 로직 변경 0건
- ✓ 모든 통합 테스트는 `MockBrokerAdapter`만 사용 (test_invariant_test_broker_
  is_mock_not_kis_live로 lock)
- ✓ 외부 네트워크 호출 0건 — `socket.create_connection` monkeypatch로 런타임
  lock
- ✓ ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / FUTURES_LIVE 변경 0건
- ✓ KIS / Anthropic / Telegram key 변경 0건. Settings.kis_app_key /
  telegram_bot_token / anthropic_api_key는 type only 검증, 값 의존 0건
- ✓ in-memory SQLite — 운영 DB 영향 0건
- ✓ 모든 주문 경로가 `route_order` 단일 진입점 통과 (#34 절대 원칙 2)

### 테스트 결과
- **신규 14 / 14 PASS** (`tests/test_order_flow.py`)
- **전체 backend pytest: 2441 PASS / 6 FAIL** — 6건 모두 pre-existing 환경
  이슈 (#60부터 알려진 `.env DEFAULT_MODE=PAPER` vs SIMULATION + 실 KIS 키
  + Windows subprocess CWD). 본 PR과 무관
- 외부 네트워크 호출 0건 (테스트로 lock)

## 추가 (#67) — Staging Environment (docker-compose + LIVE flag 격리)

체크리스트 #67: 운영 서버와 *완전히 별개*의 staging 환경. 실거래 코드가
머지 직후 운영에 가지 않도록 docker-compose 기반 격리 stack을 만들고,
**LIVE flag를 staging에서 강제 비활성화**.

운영 로직(`app/`) 변경 0건 — Docker 빌드 자산 / compose / smoke script /
문서만 추가.

### 신규 파일
- `docker-compose.staging.yml` — 4 서비스 (backend / frontend / postgres /
  redis), 포트 18000/15173/15432/16379, LIVE flag "false" 하드코딩
- `backend/Dockerfile` + `backend/.dockerignore` — Python 3.13 multi-stage,
  non-root user(uid 10001), healthcheck, LIVE flag default false, `.env*`
  격리
- `frontend/Dockerfile` + `frontend/.dockerignore` — Node 20 multi-stage,
  vite preview, build-time VITE_* args만 사용 (Secret 입력 0개), non-root
- `.env.staging.example` — placeholder만, 실 키 0건
- `scripts/check_staging_smoke.py` — backend/frontend/안전 flag/Secret 누출
  점검 stdlib 전용 smoke runner
- `docs/staging_environment.md` — 정책 / 서비스 / 실행 가이드 / mode 매트릭스
  / 검증 시나리오 / 문제 해결 / 12개 invariant / 후속 backlog

### 변경 파일
- `.gitignore` — `!.env.staging.example` allowlist 추가
- `README.md` — staging 문서 링크
- `CLAUDE.md` — "Staging 환경 정책 (#67)" 섹션 추가 (3 라인: LIVE flag 금지
  + Secret 격리)

### 서비스 / 포트
| 서비스 | host 포트 | 컨테이너 포트 |
|---|---|---|
| backend-staging | **18000** | 8000 |
| frontend-staging | **15173** | 5173 |
| postgres-staging | **15432** | 5432 |
| redis-staging | **16379** | 6379 |

운영 default(8000 / 5173)와 *반드시* 다른 1xxxx 대역 — 운영자가 포트로
환경을 즉시 식별.

### 안전 invariant
- ✓ `docker-compose.staging.yml`에 `ENABLE_LIVE_TRADING="false"` /
  `ENABLE_AI_EXECUTION="false"` / `ENABLE_FUTURES_LIVE_TRADING="false"` /
  `KIS_IS_PAPER="true"` 하드코딩
- ✓ backend Dockerfile에도 ENV로 default false — 컨테이너만 떠도 LIVE 금지
- ✓ `.env.staging.example`은 빈 placeholder만 — 실 키 0건 (grep으로 lock)
- ✓ `.env.staging`은 `.gitignore`에 의해 git 추적 0건
- ✓ `.dockerignore`로 `.env*`를 이미지에서 격리 — 이미지에 Secret 0건
- ✓ frontend `VITE_*` build args는 *공개 가능 값*만 (Token / 계좌번호 입력 X)
- ✓ smoke script는 staging 컨테이너만 호출. 실 broker / 외부 API 호출 0건
- ✓ smoke script가 `/api/status` 응답에 token / chat_id / app_secret 패턴
  없음 확인
- ✓ `app/` 운영 코드 변경 0건 — 본 PR은 Docker / compose / 문서만

### 실행 가이드 (요약)
```bash
cp .env.staging.example .env.staging                      # placeholder 유지
docker compose -f docker-compose.staging.yml --env-file .env.staging up --build -d
python scripts/check_staging_smoke.py                     # PASS 확인
docker compose -f docker-compose.staging.yml down         # 종료
```

자세한 절차 / 문제 해결: [`docs/staging_environment.md`](staging_environment.md)

### 테스트 결과
- `app/` 변경 0건 → backend pytest / frontend vitest 회귀 불필요 (테스트 추가
  + 운영 코드 미변경)
- YAML 문법: docker-compose.staging.yml은 표준 compose v3 형식, build /
  environment / depends_on / healthcheck / network / volume 모두 키 검증
  통과
- Secret 누출 grep: 모든 신규 파일에서 실 token / KIS key / chat_id 패턴
  검출 0건

### 남은 backlog (out-of-scope for #67)
- CI/CD 자동 배포 (PR 머지 → staging build → 자동 smoke) — 별도 워크플로 PR
- nginx / Caddy reverse proxy + TLS
- 운영용 docker-compose (별도 LIVE flag opt-in PR — staging과 *완전히 분리*)
- Postgres → 운영 DB 마이그레이션 tooling
- 컨테이너 image registry push (Harbor / GHCR)
- 로그 집계 (Loki / OpenTelemetry)

## 추가 (#68) — Audit Log facade (append-only / Secret 거부)

체크리스트 #68: 모든 신호 / 주문 요청 / 승인 / 거절 / AI 제안 / 리스크 차단 /
긴급정지를 통합 감사 이벤트 timeline에 영구화. 기존 도메인 테이블(OrderAuditLog
/ PendingApproval / AgentDecisionLog / EmergencyStopEvent / VirtualOrder /
FuturesOrderAuditLog)을 *대체하지 않고* 그 위에 cross-cutting facade를 추가.

### 신규 파일
- `backend/app/audit/events.py` — AuditEvent dataclass + EventType / Severity /
  SourceKind enum + `log_audit_event()` + `archive_event()` + 5개 builder
  helpers + Secret 패턴 fail-closed 거부
- `backend/app/db/models.py` — `AuditEvent` ORM 모델 추가
- `backend/alembic/versions/20260525_0021_audit_event.py` — 신규 테이블
  migration (DELETE/UPDATE 안전)
- `backend/tests/test_audit_events.py` — 21건 (Secret 거부 + archive 멱등 +
  builder + delete 함수 0개 invariant + broker import 0건 invariant)
- `backend/tests/test_audit_events_routes.py` — 14건 (list / get / POST
  OPERATOR_NOTE / PATCH archive / DELETE 엔드포인트 0개 OpenAPI 검증 +
  emergency-stop hook 자동 INSERT + audit hook 실패 격리)
- `frontend/src/components/common/AuditEventTimelineCard.jsx` — read-only
  timeline + archive 확인 모달 + 삭제/수정 버튼 0개
- `frontend/src/components/common/AuditEventTimelineCard.test.jsx` — 10건
  (append-only banner + 삭제 버튼 0개 invariant + archive 확인 모달 + filter
  chip)
- `docs/audit_log_policy.md` — 정책 / 감사 대상 / Secret 거부 / 삭제 방지 /
  AI 상세 로그 기준 / API / UI / 13개 invariant

### 변경 파일
- `backend/app/audit/__init__.py` — events 모듈 export
- `backend/app/api/routes_audit.py` — `/events` GET/POST/PATCH + AuditEventOut
  schema (DELETE 엔드포인트 *없음*)
- `backend/app/api/routes_risk.py` — emergency-stop POST에 try/except로
  `log_audit_event` hook 추가
- `frontend/src/services/backend/client.js` — auditEventsList / Get / Note /
  Archive 메서드 (DELETE 메서드 *없음*)
- `frontend/src/components/tabs/AuditLog.jsx` — `events` sub-tab에
  `AuditEventTimelineCard` 마운트 (기존 EventTimelineView와 공존)
- `README.md` + `docs/final_completion_summary.md` — #68 결과

### 통합 AuditEvent 구조
`audit_event` 테이블 (alembic 0021):
- id / created_at
- event_type (SIGNAL / ORDER_REQUEST / APPROVAL_DECISION / RISK_BLOCK /
  AI_PROPOSAL / EMERGENCY_STOP / VIRTUAL_ORDER / FUTURES_RISK / NOTIFICATION /
  OPERATOR_NOTE / STRATEGY_CHANGE / DATA_QUALITY / SYSTEM)
- severity (INFO / WARN / CRITICAL / SECURITY)
- source (STRATEGY / AI / MANUAL / SYSTEM / OPERATOR / SCHEDULER)
- actor / symbol / strategy / mode
- target_kind / target_id — 기존 도메인 row 참조
- summary / reason / details (JSON)
- archived / archived_at / archived_by / archive_note

### log_audit_event 유틸
```python
log_audit_event(
    db,
    event_type=EventType.RISK_BLOCK,
    summary="risk manager blocked BUY 005930",
    severity=Severity.WARN,
    source=SourceKind.STRATEGY,
    actor="agent-1",
    symbol="005930",
    target_kind="OrderAuditLog", target_id=audit_row.id,
    details={"reasons": [...], "requested_by_ai": False},
)
# Secret 패턴 감지 시 SecretLeakError raise — fail-closed (redaction 아님)
```

### AI 상세 로그 기준
AI 이벤트(`AI_PROPOSAL`, `RISK_BLOCK` with `requested_by_ai=true`)는 details에
`model` / `confidence` / `supporting_reasons` / `opposing_reasons` /
`risk_note` / `is_order_intent=false` (#56 invariant) / `analysis_log_id`
(원본 ai_analysis_log row 참조)를 carry. `target_kind="OrderAuditLog"` /
`target_id=<audit row>`로 주문과 연결.

### 삭제 / 수정 방지
- Python 모듈에 `delete*` / `remove*` / `drop*` public 함수 0개 (테스트로 lock)
- HTTP `DELETE /api/audit/events/*` 엔드포인트 0개 (OpenAPI 검증 테스트로 lock)
- frontend 삭제 / 수정 버튼 0개 (button textContent regex로 lock)
- archive는 *멱등* — 이미 archived인 row에 다시 호출해도 archived_by / note
  덮어쓰지 않음 (첫 archive 정보 보존)
- archive는 *삭제의 대체* — row 영구 보존, `include_archived=true`로 다시 조회

### UI 변경
`AuditLog` 탭의 `events` sub-tab에 `AuditEventTimelineCard` 마운트. 기존
`EventTimelineView`와 공존. severity / source chip 필터 + archived 토글 +
archive 확인 모달 (운영자명 / 사유 입력).

### 안전 invariant
- ✓ `app/audit/events` 모듈에 broker / OrderExecutor / route_order import 0건
  (정적 grep 테스트로 lock)
- ✓ row delete 함수 / API / 버튼 모두 0건
- ✓ Secret 패턴 fail-closed 거부 (redaction 아님) — caller가 sanitize 후 재시도
- ✓ audit hook 실패가 emergency-stop API 응답을 깨지 않음 (try/except로 lock,
  테스트로 검증)
- ✓ 기존 audit 테이블 schema 변경 0건 — 본 PR은 *새 테이블 1건*만 추가
- ✓ broker / order / LIVE flag / Secret 변경 0건

### 테스트 결과
- **신규 backend**: 21 + 14 = **35 / 35 PASS** (`test_audit_events.py` +
  `test_audit_events_routes.py`)
- **신규 frontend**: **10 / 10 PASS** (`AuditEventTimelineCard.test.jsx`)
- AuditLog 회귀: 358건 통합 PASS (#68 UI mount 후)
- backend regression: 기존 `test_status_exposes_safety_flags`만 실패 — pre-
  existing 환경 이슈 (#60부터)

### 남은 backlog
- route_order / approve / cancel / AI assist / VirtualOrder / FuturesRisk /
  NotificationService 점진적 hook 추가 (각각 별도 PR)
- Postgres trigger / view로 audit_event UPDATE/DELETE SQL 차단 (DB 레벨 invariant)
- archive batch tooling (cron)
- 외부 SIEM 연동 (운영 환경 옵트인 후)

## 추가 (#69) — DB Backup & Restore 정책

체크리스트 #69: 장애 발생 시 OrderAuditLog / PendingApproval / AgentDecisionLog
/ AuditEvent / VirtualOrder / FuturesOrderAuditLog / Watchlist / BacktestRun
등 운영 기록을 복구 가능하도록. **API Secret / app secret / 계좌번호 / 토큰은
어떤 백업 파일에도 포함되지 않는다** — DB만 백업한다.

운영 로직(`app/`) 변경 0건 — scripts / docs / 테스트만.

### 신규 파일
- `scripts/backup_db.sh` — bash backup runner (SQLite + PostgreSQL). DATABASE_
  URL redact + secret 패턴 abort + retention + dry-run + WAL/SHM 안전 sqlite3
  `.backup` 우선
- `scripts/backup_db.ps1` — Windows PowerShell 보조 (sqlite + Postgres). UTF-8
  console, password redaction 동일 정책
- `scripts/restore_db.sh` — SQLite + PostgreSQL 복구. 운영자 `OVERWRITE` 확인
  또는 `--yes` 필수, 현재 DB는 *자동 보호 백업*(`.pre_restore_*.bak`)
- `backend/tests/test_backup_policy.py` — 19건 (14 정적 + 5 smoke).
  `.env` 미백업 / redaction helper / secret abort / .gitignore + .dockerignore /
  SQLite tmp smoke / dry-run / secret URL 거부 / missing DATABASE_URL 거부
- `docs/backup_restore.md` — 정책 / 백업 대상 / 제외 / SQLite + Postgres 절차
  / 복구 / cron / Windows Task Scheduler / 운영 주의 / 13개 invariant
- `backups/.gitkeep` — placeholder만 git 추적

### 변경 파일
- `.gitignore` — `backups/*` / `*.sql.gz` / `*.db.backup` / `*.sqlite.bak`
  추가. `backups/.gitkeep`만 allowlist
- `backend/.dockerignore` — `backups/` + 백업 파일 패턴 격리 (이미지 굽기 0건)
- `README.md` — `docs/backup_restore.md` 링크

### 백업 방식
- **SQLite**: `sqlite3 .backup` 우선(WAL/SHM 안전) → 실패 시 파일 copy fallback
- **PostgreSQL**: `pg_dump --format=plain --no-owner --no-privileges` + gzip
- 출력: `backups/autotrade_backup_YYYYMMDD_HHMMSS.sqlite` 또는 `.sql.gz`
- 환경 변수: `BACKUP_DIR` / `BACKUP_RETENTION_DAYS=14` / `BACKUP_COMPRESS=true`
  / `BACKUP_DRY_RUN=false`
- DATABASE_URL은 `redact_url()` 경유해 password를 `***`로 가린 form만 log

### 복구 방식
- SQLite: 파일 교체 (현재 DB는 `<path>.pre_restore_<ts>.bak`로 자동 보호 백업)
- PostgreSQL: `psql < backup.sql` 또는 `gunzip -c backup.sql.gz | psql`
- 운영자 명시 동의(`OVERWRITE` 입력 또는 `--yes` 플래그) 필수
- 후속 안내: `alembic current` / backend 재시작 / `/api/status` / row count

### Secret 백업 금지 방식
1. **스크립트 source 검사** — `cp .env` / `Copy-Item .env` / `tar .env`
   패턴 0건 (정적 테스트로 lock)
2. **DATABASE_URL 패턴 abort** — `KIS_APP_KEY=` / `KIS_APP_SECRET=` /
   `TELEGRAM_BOT_TOKEN=` / `ANTHROPIC_API_KEY=` / `OPENAI_API_KEY=` 검출 시
   즉시 exit 2 (smoke test로 검증)
3. **`redact_url()`** — password / token이 URL에 들어 있어도 log에는
   `://user:***@host` 형태만 출력
4. **`.gitignore`** — `backups/*` + `*.sql.gz` 등 백업 파일 git 추적 0건
5. **`.dockerignore`** — 이미지 빌드 시 `backups/` 격리

### 스케줄링 예시
- **Linux cron**: `0 3 * * * cd /path/autotrade && DATABASE_URL=... bash scripts/backup_db.sh >> backups/cron.log 2>&1`
- **Windows Task Scheduler**: pwsh.exe + scripts/backup_db.ps1
- **Docker compose staging**: host에서 직접 실행

### 안전 invariant (테스트로 lock)
- ✓ scripts에 `.env` / `KIS_APP_KEY=` / `ANTHROPIC_API_KEY=` 복사 패턴 0건
- ✓ DATABASE_URL을 raw log에 echo 0건 (redact 경유)
- ✓ Secret-like URL → 즉시 exit (smoke test로 검증)
- ✓ `.gitignore`에 backup 파일 패턴 등록 (테스트로 검증)
- ✓ `backend/.dockerignore`에 `backups/` 등록 (테스트로 검증)
- ✓ `app/` 운영 코드 변경 0건
- ✓ broker.place_order / cancel_order / route_order 호출 추가 0건
- ✓ ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / FUTURES_LIVE 변경 0건
- ✓ KIS / Anthropic / Telegram key 변경 0건
- ✓ `.env` 변경 0건

### 테스트 결과
- **신규 backend**: 정적 14건 PASS + smoke 5건 skipped (현 Windows 한글 경로
  + git-bash subprocess 인코딩 제약 — 운영 ASCII 경로 `C:\trade\autotrade`
  에서는 모두 실행됨)
- 수동 smoke 검증: tmp SQLite 백업 정상 (8192 bytes), DATABASE_URL에
  `KIS_APP_KEY=` 포함 시 즉시 거부 확인
- 전체 backend pytest regression: 본 PR은 `app/` 변경 0건이라 영향 없음

### 남은 backlog
- 백업 파일 무결성 자동 검증 (sqlite PRAGMA integrity_check / pg_restore --list)
- 백업 파일 암호화 (age / GPG) — 외부 저장소 upload 전 필수
- 외부 저장소 sync (S3 / Backblaze / rsync to NAS)
- 주별 / 월별 retention 분리
- 백업 결과 알림 (#64 NotificationService와 통합)
- 자동 restore smoke (staging에 매일 자동 복구 후 row count diff)
- DB 마이그레이션 시점의 *pre-migration* 자동 백업

---

## #70 Monitoring (read-only 안정성 집계)

> 본 체크리스트는 *시스템 안정성* 모니터링 — 수익률이 아니라 장애 조기 발견용.

### 생성 / 수정 파일
- `backend/app/monitoring/__init__.py` (신규)
- `backend/app/monitoring/types.py` (신규) — MetricStatus / Metric / AlertCandidate / MonitoringSnapshot
- `backend/app/monitoring/api_metrics.py` (신규) — `ApiMetricsRegistry` in-memory ring
- `backend/app/monitoring/middleware.py` (신규) — `ApiMetricsMiddleware` ASGI
- `backend/app/monitoring/service.py` (신규) — `MonitoringService` + `notify_alerts` helper
- `backend/app/api/routes_monitoring.py` (신규) — `/api/monitoring/{health,metrics,alerts}`
- `backend/app/main.py` (수정) — middleware + router 등록
- `backend/tests/test_monitoring.py` (신규, 37 PASS)
- `frontend/src/services/backend/client.js` (수정) — monitoring helper 3개 추가
- `frontend/src/store/useMonitoring.js` (신규)
- `frontend/src/components/tabs/MonitoringCard.jsx` (신규)
- `frontend/src/components/tabs/MonitoringCard.test.jsx` (신규, 10 PASS)
- `frontend/src/components/tabs/Dashboard.jsx` (수정) — MonitoringCard 노출
- `docs/monitoring_policy.md` (신규)
- `README.md` (수정) — 정책 링크 추가

### Health endpoint
- `GET /api/monitoring/health`  — overall + metrics_summary + alert_count (가벼운 liveness)
- `GET /api/monitoring/metrics` — 전체 `MonitoringSnapshot` JSON
- `GET /api/monitoring/alerts`  — WARN/CRITICAL 후보 *조회 only* (송신 X)

### 수집 메트릭 (8개)
- `server` — uptime / pid / started_at
- `database` — `SELECT 1` ping
- `api_error_rate` — ring buffer 5분 윈도우 (WARN ≥ 5%, CRITICAL ≥ 20%)
- `order_failure_rate` — `OrderAuditLog` REJECTED 24h (WARN ≥ 30%, CRITICAL ≥ 60%, 최소 5건)
- `approval_queue` — `PendingApproval` oldest age (WARN ≥ 10분, CRITICAL ≥ 30분)
- `risk_events` — `EmergencyStopEvent` 60분 카운트 (WARN ≥ 3, CRITICAL ≥ 8)
- `data_freshness` — `freshness` 모듈 sample carry
- `notification` — `NotificationService.status()` carry

### 알림 후보 기준
- WARN / CRITICAL 메트릭에서 한 건씩 `AlertCandidate` 생성 (OK / UNKNOWN 제외)
- `notify_alerts(service, alerts)` helper가 `NotificationService.notify()` 위임
- helper는 raise 금지 — 채널 실패해도 시스템 중단 X (시스템 안정성 우선)
- 본 endpoint는 *후보 표시만* — 실제 송신은 backend / scheduler가 결정

### UI 변경
- Dashboard 상단 `MarketRegimeBadge` 아래에 `MonitoringCard` 노출
- 모바일 요약: 3개 카드 (시스템 / 데이터 / 주문&리스크) — 그룹별 worst status
- 데스크탑: 8개 메트릭 행 + 알림 후보 목록 + "수익률 아님" 안내 banner
- BUY/SELL/HOLD/긴급정지 토글/LIVE 활성화 버튼 0개 (테스트로 lock)

### 안전 invariant (테스트로 lock)
- ✓ `app/monitoring/*` + `routes_monitoring.py`에 broker / OrderExecutor / route_order import 0건
- ✓ DB write (INSERT/UPDATE/DELETE/db.add/db.commit/db.flush) 0건 — SELECT만
- ✓ ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / FUTURES_LIVE 변경 0건
- ✓ emergency_stop 토글 0건
- ✓ 응답 본문에 Secret / API Key / Telegram Bot Token / 계좌번호 패턴 0건
- ✓ frontend에 send / 토글 / 적용 버튼 0개 (read-only)
- ✓ `notify_alerts()`는 service None / channel raise 모두 graceful

### 테스트 결과
- **신규 backend**: 37 PASS (DTO 3 + ApiMetrics 5 + collectors 14 + snapshot 3 + routes 4 + notify_alerts 3 + 정적 grep 4)
- **신규 frontend**: 10 PASS (badge / 메트릭 행 / 알림 / 모바일 요약 / invariant)
- Regression: Dashboard 82 PASS, client 6 PASS, 다른 backend 모듈 무회귀

### 남은 monitoring backlog
- 임계치 환경변수 override (`MONITORING_API_ERROR_WARN` 등)
- 백그라운드 scheduler에서 주기적 `notify_alerts` 자동 호출
- Prometheus / Grafana exporter 통합 (`/api/monitoring/metrics` JSON → metrics endpoint)
- WebSocket feed freshness 자동 수집 (현재는 sample carry only)
- 모니터링 카드 클릭 시 메트릭별 drill-down (예: order_failure_rate → AuditLog REJECTED 필터)
- 알림 *진압* / *해제* 운영자 토글 (현재는 dedupe만)
- staging vs production 임계치 분리 프로파일

---

## #71 MVP Completion Gate (문서 / 점검 전용)

> 본 체크리스트는 *판정 문서*와 *자동 요약 스크립트*만 추가한다.
> `app/` 운영 코드 / `.env` / safety flag / live order 코드 변경 0건.

### 생성 / 수정 파일
- `docs/mvp_completion.md` (신규) — MVP 판정 + P0 43개 상태표 + Paper/Shadow 진입 조건
- `scripts/summarize_mvp_status.py` (신규) — read-only 자동 요약 CLI (markdown / json + secret check)
- `backend/tests/test_mvp_completion_doc.py` (신규) — 판정 문서 / 스크립트 정적 가드 + smoke 실행
- `README.md` (수정) — MVP Completion 섹션 + 정책 링크
- `docs/final_completion_summary.md` (수정) — 본 항목

### MVP 판정
- **`MVP_READY_FOR_PAPER_SHADOW`**
- P0 DONE: **43**, PARTIAL: 0, BLOCKED: 0
- live flag default 모두 false (`enable_live_trading`, `enable_ai_execution`,
  `enable_futures_live_trading`) — 스크립트가 `app/core/config.py` *문자열*
  파싱으로 자동 검증
- Secret 의심 패턴: 0건 (`--check-secrets` 통과)

### 자동 요약 스크립트
- `python scripts/summarize_mvp_status.py --format markdown` (운영자 콘솔)
- `python scripts/summarize_mvp_status.py --format json` (CI / 외부 통합)
- `--check-secrets` 옵션으로 docs / README에 Secret 패턴이 있으면 exit 1
- 본 스크립트는 *읽기만* 함 — KIS / Anthropic / Telegram / DB / broker
  호출 0건, `.env` 접근 0건 (테스트로 lock)

### 실거래 금지 invariant — 본 PR 유지
- ✓ `ENABLE_LIVE_TRADING=false` 유지
- ✓ `ENABLE_AI_EXECUTION=false` 유지
- ✓ `ENABLE_FUTURES_LIVE_TRADING=false` 유지
- ✓ `KIS_IS_PAPER=true` 유지
- ✓ `app/` 코드 변경 0건
- ✓ 절대 원칙 1~6 모두 변경 없음

### 테스트 결과
- `test_mvp_completion_doc.py`: 14 PASS (문서 존재 / verdict / live flag 언급 /
  스크립트 invariant / smoke 실행 / Secret 검사 / cross-link)
- Backend regression: 본 PR은 `app/` 변경 0건이라 영향 없음

### 다음 권장 단계
- KIS Paper 주문 검증 (`KIS_IS_PAPER=true`)
- LIVE_SHADOW 운영 (실 시세 read-only, ShadowTrade *would-have*)
- Paper / Shadow 2~4주 검증 — 일별 손익 / 거부율 / freshness / API 오류율 추이
- 운영자 피드백으로 UI / 모바일 / 알림 / 모니터링 보강
- LIVE 실거래는 *별도 옵트인 PR + 사용자 명시 승인* 후에만

### 남은 backlog (Paper/Shadow 단계에서 보강)
- Monitoring 임계치 env override + 자동 알림 송신 scheduler
- Backup 무결성 자동 검증 + 외부 저장소 sync (Secret 암호화)
- Reconciliation drift 알림
- AI Token 사용량 상세 추적
- Strategy promotion 자동화

---

## #72 Paper Gate (실시간 운용 평가 게이트)

> Paper 모드 4주 이상 운용 결과를 promotion_policy 기준으로 평가하는 코드
> 단 게이트 + CLI + 선택 API. **PASS는 Live Manual Approval *검토 가능*
> 을 의미하며 실거래 자동 허가가 *아니다*.**

### 생성 / 수정 파일
- `backend/app/governance/paper_gate.py` (신규) — `PaperGateInput` /
  `PaperGateResult` / `PaperGateVerdict` / `PaperGateThresholds` +
  `evaluate_paper_gate()` + `render_markdown_report()`
- `backend/app/governance/paper_gate_collector.py` (신규) — read-only DB →
  PaperGateInput 빌더 (OrderAuditLog SELECT only)
- `backend/app/api/routes_governance.py` (수정) — `POST /governance/paper-gate/evaluate`
- `scripts/evaluate_paper_gate.py` (신규) — CLI (markdown / json, dry-run 지원)
- `backend/tests/test_paper_gate.py` (신규, 36 PASS)
- `docs/paper_gate_policy.md` (신규)
- `docs/promotion_policy.md` (수정) — Paper 단계에 게이트 링크 추가
- `README.md` (수정) — 정책 링크 추가

### Paper Gate 기준 (PASS = 모두 충족)
1. 운영 기간 ≥ 28일
2. 매매 신호 ≥ 100건
3. expectancy > 0
4. Profit Factor ≥ 1.2
5. MDD ≤ 15% (초기 자본)
6. 손실한도 위반 = 0
7. OrderAuditLog 누락 = 0
8. stale / duplicate 위반 = 0
9. FillPolling 정합성 OK
10. client_order_id idempotency OK

CAUTION 사유: 하루 의존도 > 50%, rejection > 30%, 시간대 손실 집중 > 60%,
Paper vs Backtest PF 괴리 > 50%.

### 리포트 생성 방식
- **markdown**: `python scripts/evaluate_paper_gate.py --strategy X --format markdown --output reports/...`
- **JSON**:    `python scripts/evaluate_paper_gate.py --strategy X --format json`
- **API**:     `POST /api/governance/paper-gate/evaluate`
- exit code: PASS/CAUTION/UNKNOWN=0, FAIL=1, 실행 오류=2

### 안전 invariant (테스트로 lock)
- ✓ broker / OrderExecutor / route_order / paper_trader / 외부 HTTP / AI SDK import 0건
- ✓ `broker.place_order(` / `route_order(` / `OrderExecutor(` 호출 0건
- ✓ `submit_candidate(` 호출 0건
- ✓ DB write (INSERT/UPDATE/DELETE/.add/.commit/.flush) 0건
- ✓ `settings.enable_*_trading =` / `os.environ["ENABLE_*"]` mutate 0건
- ✓ `PaperGateResult.is_live_authorization=True` 생성 불가 (ValueError)
- ✓ `PaperGateResult.is_order_signal=True` 생성 불가 (ValueError)
- ✓ 리포트 / 응답에 BUY/SELL/HOLD 신호 문구 0건
- ✓ 응답에 Secret 패턴 0건

### 테스트 결과
- **신규 backend**: 36 PASS (DTO invariant 3 + happy path 1 + PF 변형 3 +
  FAIL paths 10 + CAUTION 4 + threshold override 1 + markdown 2 +
  collector 3 + API 3 + 정적 grep 4 + CLI smoke 2)
- Regression: paper_gate + monitoring + mvp + strategy_promotion 합쳐
  130 PASS, 0 fail

### CLI/API 사용법
```bash
# CLI (dry-run, DB 없이도 사용 가능)
python scripts/evaluate_paper_gate.py --dry-run \
  --strategy sma_cross \
  --trade-count 120 --active-days 22 --expectancy 350 \
  --pf-numerator 200000 --pf-denominator 150000 \
  --max-drawdown-value 800000

# CLI (운영 DB + 자동 28일 윈도우)
python scripts/evaluate_paper_gate.py --strategy sma_cross --format json

# API
POST /api/governance/paper-gate/evaluate
{ "strategy_name": "sma_cross", "trade_count": 120, ... }
```

### 실거래 금지 invariant — 본 PR 미변경
- ✓ `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `ENABLE_FUTURES_LIVE_TRADING=false`, `KIS_IS_PAPER=true`
- ✓ `app/core/config.py` 변경 0건
- ✓ `.env` / Secret / 계좌번호 변경 0건
- ✓ 절대 원칙 1~6 모두 유지

### 남은 Paper Gate backlog
- env override (`PAPER_GATE_MIN_PROFIT_FACTOR` 등)
- 자동 backtest ↔ paper PF drift 계산 (현재 운영자 수동 입력)
- 시간대 손실 집중 자동 계산
- 일별 손익 자동 산출 (체결 ledger 통합)
- LIVE_SHADOW 사전 통과 검증 연동 (#43 ShadowTrade row)
- 운영자 승인 / reject 이력 carry (signed by + note)
- PASS / FAIL → NotificationService 알림 연계

---

## #73 Live Manual Gate (초소액 LIVE_MANUAL_APPROVAL 진입 readiness)

> 본 체크리스트는 *진입 readiness 평가*만 추가한다. **본 PR로 어떤 LIVE
> 플래그도 활성화되지 않으며**, 실거래는 별도 옵트인 PR + 사용자 명시 승인
> 후에만 가능.

### 생성 / 수정 파일
- `backend/app/governance/live_manual_gate.py` (신규) —
  `LiveManualGateInput` / `LiveManualGateResult` / `LiveManualGateVerdict` /
  `LiveManualGateThresholds` + `evaluate_live_manual_gate()` + `render_markdown_report()`
- `backend/app/governance/live_manual_gate_collector.py` (신규) —
  `summarize_live_manual_period(db, start, end)` read-only 집계
- `backend/app/api/routes_governance.py` (수정) — 두 endpoint 추가
- `backend/tests/test_live_manual_gate.py` (신규, **33 PASS**)
- `frontend/src/services/backend/client.js` (수정) — 2개 helper 추가
- `frontend/src/components/tabs/LiveManualGateCard.jsx` (신규)
- `frontend/src/components/tabs/LiveManualGateCard.test.jsx` (신규, **8 PASS**)
- `docs/live_manual_gate.md` (신규)
- `docs/promotion_policy.md` (수정) — Live Manual 단계 게이트 링크
- `docs/live_activation_blockers.md` (수정) — 8번 체크리스트에 #72/#73 게이트 항목 추가
- `CLAUDE.md` (수정) — #73 invariant 명시
- `README.md` (수정) — 정책 링크
- `docs/final_completion_summary.md` (수정) — 본 항목

### Live Manual Gate 기준
- **PASS** (모두 충족 시):
  1. Paper Gate PASS
  2. Promotion Gate PASS
  3. 운영자 explicit opt-in
  4. `approval_required=True`
  5. `ENABLE_AI_EXECUTION=false`
  6. `ENABLE_FUTURES_LIVE_TRADING=false`
  7. 1회 주문 ≤ 50,000원
  8. 일일 손실한도 ≤ 10,000원
  9. 동시 보유 종목 ≤ 3개
  10. `system_errors=0`
  11. `audit_missing_count=0`
  12. `approval_bypass_attempts=0`
- **CAUTION**: 운영 기간 < 30일 / `ENABLE_LIVE_TRADING` 이미 true / allowed_symbols 미지정
- **BLOCKED**: 위 PASS 항목 중 하나 이상 미달
- **UNKNOWN**: 데이터 부족

### Approval API 강제 방식
- 모든 `LIVE_MANUAL_APPROVAL` 모드 주문 → `route_order` → RiskManager →
  `NEEDS_APPROVAL` → `PendingApproval` 큐 → 운영자 `POST /api/approvals/{id}/approve`
  → `PermissionGate.approve` (broker 호출 전 RiskManager 재검증 #070) →
  `OrderExecutor.execute` (유일한 broker 호출 지점 #40).
- 우회 시도 탐지: `OrderAuditLog.executed=True` 인데 `PendingApproval` 큐
  row 없음 → `approval_bypass_attempts`로 카운트 → BLOCKED.

### 운영 로그 요약 (`summarize_live_manual_period`)
read-only SELECT only, broker 호출 0건, DB write 0건:
- `total_live_manual_orders` / `approved_orders` / `needs_approval_orders` / `rejected_orders`
- `pending_approval_rows` / `approved_via_queue` / `expired_or_cancelled`
- `approval_bypass_attempts` / `emergency_stops_in_period` / `operating_days`

### CLI/API 사용법
```http
POST /api/governance/live-manual-gate/evaluate
{
  "strategy_name": "sma_cross",
  "paper_gate_passed": true,
  "promotion_gate_passed": true,
  "user_explicit_opt_in": true,
  "approval_required": true,
  "current_max_order_notional_krw": 30000,
  "current_max_daily_loss_krw": 8000,
  "current_max_open_positions": 2,
  "allowed_symbols": ["005930"],
  "operating_days": 30
}

GET /api/governance/live-manual-gate/period-summary?period_start=...&period_end=...
```

### 안전 invariant (테스트로 lock)
- ✓ broker / OrderExecutor / route_order / paper_trader / 외부 HTTP / AI SDK import 0건
- ✓ `broker.place_order(` / `route_order(` / `OrderExecutor(` / `submit_candidate(` 호출 0건
- ✓ DB write (INSERT/UPDATE/DELETE/.add/.commit/.flush) 0건 (evaluator + collector)
- ✓ `from app.core.config import` / `get_settings(` 호출 0건 (evaluator는 입력 DTO만 사용)
- ✓ `settings.enable_*_trading =` / `os.environ["ENABLE_*"]` mutate 0건
- ✓ `LiveManualGateResult.is_live_authorization=True` 생성 불가 (ValueError)
- ✓ `LiveManualGateResult.is_order_signal=True` 생성 불가 (ValueError)
- ✓ UI 카드에 "실거래 활성화" / "실거래 시작" / "LIVE 켜기" / "Place Order" / "주문 실행" / "ENABLE_LIVE_TRADING" 라벨 버튼 0개
- ✓ UI 화면에 BUY / SELL / HOLD / 긴급정지 토글 문구 0건
- ✓ 응답 / 화면에 Secret 패턴 0건
- ✓ 위험 문구 "PASS는 실거래 자동 허가가 아니라, 초소액 수동승인 검토 가능 상태입니다." UI에 *항상* 노출

### 테스트 결과
- **신규 backend**: 33 PASS (DTO invariant 3 + happy 1 + BLOCKED paths 11 +
  CAUTION 2 + threshold override 1 + markdown 2 + collector 3 + API 4 +
  정적 grep 5 + Secret 비노출 1)
- **신규 frontend**: 8 PASS (PASS 배지 / BLOCKED 차단 사유 / 위험 문구 영구
  노출 / 활성화 버튼 0개 / BUY-SELL 0건 / Secret 비노출 / 한도 표시 / 평가
  버튼 라벨)
- Regression: paper_gate + monitoring + mvp + strategy_promotion + 본 신규 합산 무회귀

### 실거래 금지 invariant — 본 PR 미변경
- ✓ `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `ENABLE_FUTURES_LIVE_TRADING=false`, `KIS_IS_PAPER=true`
- ✓ `app/core/config.py` 변경 0건
- ✓ `.env` / Secret / API Key / 계좌번호 변경 0건
- ✓ 절대 원칙 1~6 모두 유지

### PASS = 실거래 허가가 아님 (강조)
PASS verdict는 *LIVE_MANUAL_APPROVAL 모드 진입 검토 가능* + *초소액* + *모든
주문 수동승인* 상태를 의미할 뿐. 실거래 진입에는 별도 옵트인 PR + 사용자 명시
승인 + `ENABLE_LIVE_TRADING=true` + KIS 실주문 라우팅 활성화 PR 모두 필요.

### 남은 Live Manual Gate backlog
- env override (`LIVE_MANUAL_MAX_ORDER_NOTIONAL` 등)
- KIS Paper 운영에서 자동 metrics carry (현재는 운영자 입력)
- Promotion Gate 자동 통합 (PaperGate + LiveManualGate 단일 evaluate)
- Notification 연계 — verdict 변동 시 운영자 알림
- Frontend Settings 탭에 `user_explicit_opt_in` 토글 (현재는 입력으로만)
- `live_activation_blockers.md` 의 9단계 checklist 자동 검증

---

## #74 AI Assist Gate (LIVE_AI_ASSIST 품질 검증)

> AI 자동매매 진입 *전 필수* 검증 단계. **본 PR로 LIVE_AI_EXECUTION 활성화 0건**.
> 본 리포트는 **투자 조언이 아니라 시스템 검증 자료**.

### 생성 / 수정 파일
- `backend/app/governance/ai_assist_gate.py` (신규) —
  `AIAssistGateInput` / `AIAssistGateResult` / `AIAssistGateVerdict` /
  `AIAssistGateThresholds` + `AIAssistFailureReason` enum + `evaluate_ai_assist_gate()`
  + `render_markdown_report()`
- `backend/app/governance/ai_assist_gate_collector.py` (신규) —
  `build_ai_assist_gate_input(db, ...)` + `list_ai_assist_strategies()` read-only
- `backend/app/api/routes_governance.py` (수정) —
  `POST /governance/ai-assist-gate/evaluate`
- `scripts/evaluate_ai_assist_gate.py` (신규) — CLI (markdown/json, dry-run)
- `backend/tests/test_ai_assist_gate.py` (신규, **34 PASS**)
- `frontend/src/services/backend/client.js` (수정) — 1 helper 추가
- `frontend/src/components/tabs/AIAssistGateCard.jsx` (신규)
- `frontend/src/components/tabs/AIAssistGateCard.test.jsx` (신규, **8 PASS**)
- `docs/ai_assist_gate.md` (신규)
- `docs/promotion_policy.md` / `docs/live_activation_blockers.md` /
  `CLAUDE.md` / `README.md` / `docs/final_completion_summary.md` (수정)

### AI Assist 평가 지표
- proposal_count / approved_proposals / risk_rejected / operator_rejected / expired
- approved_expectancy / approved_loss_rate
- risk_rejection_rate / operator_rejection_rate / expired_or_cancelled_rate
- confidence_calibration / avg_confidence
- rejected_but_would_have_won (CAUTION 신호)
- ai_decision_audit_drift / emergency_stops_in_period / active_days

### Failure Reason Tags (advisory only, *주문 신호 0개*)
`low_confidence` / `data_stale` / `price_gap` / `liquidity` / `risk_limit` /
`operator_rejected` / `approval_expired` / `emergency_stop` / `regime_mismatch` /
`news_or_theme_overheated` / `duplicate_or_cooldown` / `uncategorized`.
BUY / SELL / HOLD 0개 (테스트로 lock).

### PASS 기준 (모두 충족)
1. 제안 ≥ **100건**
2. 운영 기간 ≥ **28일**
3. expectancy > 0
4. 승인 손실율 ≤ **55%**
5. Risk 거절율 ≤ **60%**
6. 운영자 거절율 ≤ **50%**
7. confidence calibration ≥ **0.5**
8. audit drift = **0**
9. 긴급정지 ≤ **2회**

CAUTION: 만료/취소율 > 30% / calibration < 0.65 / rejected_but_would_have_won > 25% / 단일 failure reason > 40%.

### 리포트 생성 방식
- **CLI**: `python scripts/evaluate_ai_assist_gate.py --strategy X --format markdown --output reports/...`
- **JSON**: `python scripts/evaluate_ai_assist_gate.py --strategy X --format json`
- **API**: `POST /api/governance/ai-assist-gate/evaluate`
- exit code: PASS/CAUTION/UNKNOWN=0, FAIL=1, 실행 오류=2

### API / UI 변경
- 신규 API: `POST /api/governance/ai-assist-gate/evaluate`
- 신규 UI: `AIAssistGateCard` (frontend tabs) — 위험 문구 영구 노출, AI 자동매매 활성화 버튼 0개

### 안전 invariant (테스트로 lock)
- ✓ broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` / `app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` import 0건
- ✓ `broker.place_order(` / `route_order(` / `OrderExecutor(` / `submit_candidate(` / `AiClient(` 호출 0건
- ✓ DB write (INSERT/UPDATE/DELETE/.add/.commit/.flush) 0건
- ✓ `from app.core.config import` / `get_settings(` 호출 0건 (evaluator는 입력 DTO만 사용)
- ✓ `settings.enable_*_trading =` / `os.environ["ENABLE_*"]` mutate 0건
- ✓ `AIAssistGateResult.is_live_authorization=True` 생성 불가 (ValueError)
- ✓ `AIAssistGateResult.is_order_signal=True` 생성 불가 (ValueError)
- ✓ `AIAssistGateResult.is_investment_advice=True` 생성 불가 (ValueError)
- ✓ `AIAssistFailureReason` enum에 BUY/SELL/HOLD 값 0개
- ✓ UI에 "AI 자동매매 시작" / "LIVE_AI_EXECUTION 활성화" / "ENABLE_AI_EXECUTION" / "AI 자동 실행" / "Place Order" / "주문 실행" / "실거래 활성화" 라벨 버튼 0개
- ✓ UI에 BUY/SELL/HOLD/긴급정지 토글 문구 0건
- ✓ 응답 / 리포트에 Secret 패턴 0건

### 테스트 결과
- **신규 backend**: 34 PASS (DTO invariant 4 + evaluator happy/CAUTION/FAIL 15 + threshold override 1 + markdown 3 + collector 4 + API 3 + 정적 grep 5 — 5)
- **신규 frontend**: 8 PASS (PASS 배지 / FAIL 사유 + tags / 위험 문구 영구 / 활성화 버튼 0개 / BUY-SELL 0건 / Secret 비노출 / 평가 버튼 라벨 / 핵심 메트릭)
- Regression: ai_assist_gate + live_manual_gate + paper_gate + monitoring 합쳐 140 PASS, 0 fail

### 실거래 / AI 자동매매 금지 invariant — 본 PR 미변경
- ✓ `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `ENABLE_FUTURES_LIVE_TRADING=false`, `KIS_IS_PAPER=true`
- ✓ `app/core/config.py` 변경 0건
- ✓ `.env` / Secret / API Key / 계좌번호 변경 0건
- ✓ 실제 broker / KIS / Anthropic API 호출 0건 (테스트는 fake DB + MockBroker)
- ✓ 절대 원칙 1~6 모두 유지

### PASS = LIVE_AI_EXECUTION 허가가 아님 (강조)
PASS verdict는 *AI Assist 품질이 다음 검증 단계로 진입 가능* 만 의미. AI
자동매매 활성화는 `AIExecutionGate`(#45) + 별도 옵트인 PR + 사용자 명시
승인 모두 필요.

### 남은 AI Assist Gate backlog
- env override (`AI_ASSIST_MIN_PROPOSAL_COUNT` 등)
- 수익 메트릭 자동 산출 (현재는 운영자 입력)
- confidence calibration 정교화 (≥70 heuristic 보강)
- 종목 / 시간대 / regime별 AI 신호 분포 분석
- AI vs 비-AI 주문 결과 비교 (선택성 검증)
- Notification 연계 — 주간 자동 평가 + 운영자 알림
- AgentDecisionLog chain_id 통합 — 의사결정 사슬 trace

---

## #75 AI Execution Activation Gate (LIVE_AI_EXECUTION 최종 readiness)

> AI 자동매매 활성화 *최종* 안전 게이트. **본 PR로 LIVE_AI_EXECUTION 활성화 0건**.
> **선물 AI Execution은 본 게이트가 *영구* 허용하지 않는다** (`futures_allowed=false` 불변).

### 생성 / 수정 파일
- `backend/app/governance/ai_execution_gate.py` (신규) —
  `AIExecutionGateInput` / `AIExecutionActivationGateResult` / `AIExecutionGateVerdict` /
  `AIExecutionGateThresholds` + `evaluate_ai_execution_gate()` + `render_markdown_report()`
  + `get_policy_summary()`
- `backend/app/api/routes_governance.py` (수정) —
  `POST /governance/ai-execution-gate/evaluate` + `GET /governance/ai-execution-gate/policy`
- `backend/tests/test_ai_execution_gate_activation.py` (신규, **49 PASS**)
- `frontend/src/services/backend/client.js` (수정) — 2 helper 추가
- `frontend/src/components/tabs/AIExecutionGateCard.jsx` (신규)
- `frontend/src/components/tabs/AIExecutionGateCard.test.jsx` (신규, **9 PASS**)
- `docs/ai_execution_gate.md` (신규)
- `docs/promotion_policy.md` / `docs/live_activation_blockers.md` /
  `CLAUDE.md` / `README.md` / `docs/final_completion_summary.md` (수정)

### AI Execution Gate 기준 (READY_FOR_REVIEW = 모두 충족)

**전제 게이트 (5)**
1. Promotion Gate(#27) PASS
2. Paper Gate(#72) PASS
3. AI Assist Gate(#74) PASS
4. Live Manual Gate(#73) PASS
5. 운영자 explicit opt-in

**안전 인프라 (6)**
6. RiskManager 활성
7. OrderGuard 활성
8. AI Permission Gate 활성
9. AuditLog 완전 (누락 0)
10. KillSwitch 준비 (3-Level drill 완료)
11. Circuit Breaker 설정

**운영 기간 (2)**
12. Live Manual 운영 ≥ 28일
13. AI Assist 운영 ≥ 28일

**극소액 정책 (7)**
14. 1회 주문 ≤ **30,000원**
15. 일일 손실 ≤ **5,000원**
16. 일일 주문 ≤ **10건**
17. 동시 보유 ≤ **2개**
18. 종목 whitelist **1~5개**
19. 거래 시간 (KST) **09:30~14:30** 명시
20. AI confidence ≥ **75** / signal quality ≥ **70**

**시스템 안정성 (3)**
21. `system_errors = 0`
22. `audit_missing_count = 0`
23. `approval_bypass_attempts = 0`

### BLOCKED 조건
- 위 23개 항목 중 하나 이상 미달
- `futures_target=True` 또는 `enable_futures_live_trading=True` → 즉시 BLOCKED
- 모든 한도 미설정 (0값) → 즉시 BLOCKED

### 선물 AI Execution 지연 정책 (영구 차단)
- `AIExecutionActivationGateResult.futures_allowed=False` 불변
- `__post_init__`이 `futures_allowed=True` 생성 시 ValueError
- `GET /policy` 가 `"futures_allowed": false` *항상* 반환
- 선물 AI Execution은 [`live_activation_blockers.md`](live_activation_blockers.md) §3.1
  9단계 blocker + 별도 게이트 + 별도 PR 필요 — 본 게이트로는 *어떤 시나리오로도*
  활성화 검토 대상이 아님

### UI / API 변경
- 신규 API: `POST /api/governance/ai-execution-gate/evaluate` + `GET /policy`
- 신규 UI: `AIExecutionGateCard` — 활성화 평가 banner + 선물 영구 차단 banner +
  "활성화 검토 평가" 버튼만 (활성화 / 토글 / 주문 시작 버튼 0개)

### 안전 invariant (테스트로 lock)
- ✓ broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` / `app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` import 0건
- ✓ `broker.place_order(` / `route_order(` / `OrderExecutor(` / `submit_candidate(` / `AiClient(` 호출 0건
- ✓ DB write 0건
- ✓ `from app.core.config import` / `get_settings(` 호출 0건 (evaluator는 입력 DTO만 사용)
- ✓ `settings.enable_*_trading =` / `os.environ["ENABLE_*"]` mutate 0건
- ✓ `is_live_authorization=True` 생성 불가 (ValueError)
- ✓ `is_order_signal=True` 생성 불가
- ✓ `is_investment_advice=True` 생성 불가
- ✓ `futures_allowed=True` 생성 *영구* 불가
- ✓ UI에 "AI 자동매매 켜기" / "AI 자동매매 시작" / "AI 자동매매 활성화" / "LIVE_AI_EXECUTION 활성화" / "ENABLE_AI_EXECUTION" / "주문 시작" / "Place Order" / "실거래 활성화" / "활성화 토글" 라벨 버튼 0개
- ✓ UI에 BUY/SELL/HOLD/긴급정지 토글 문구 0건
- ✓ 응답 / 리포트에 Secret 패턴 0건

### 테스트 결과
- **신규 backend**: 49 PASS (DTO invariant 5 + happy 1 + BLOCKED paramaterized 11 +
  추가 BLOCKED 13 + futures forbidden 2 + CAUTION 3 + threshold override 1 +
  markdown 2 + policy summary 1 + API 4 + Secret 비노출 1 + 정적 grep 5)
- **신규 frontend**: 9 PASS (READY 배지 / BLOCKED 차단 사유 + actions / 활성화
  고지 영구 / 선물 영구 차단 banner / 활성화 버튼 0개 / BUY-SELL 0건 / Secret
  비노출 / 평가 버튼 라벨 / 극소액 정책 표시)
- Regression: ai_execution + ai_assist + live_manual + paper + monitoring 합쳐
  189 PASS, 0 fail
- Ruff 신규 파일: clean

### 실거래 / AI 자동매매 / 선물 LIVE 금지 invariant — 본 PR 미변경
- ✓ `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `ENABLE_FUTURES_LIVE_TRADING=false`, `KIS_IS_PAPER=true`
- ✓ `app/core/config.py` 변경 0건
- ✓ `.env` / Secret / API Key / 계좌번호 변경 0건
- ✓ 실제 broker / KIS / Anthropic / OpenAI / httpx / requests 호출 0건
- ✓ 절대 원칙 1~6 모두 유지

### READY_FOR_REVIEW = 실제 활성화 아님 (강조)
READY_FOR_REVIEW verdict는 *활성화 검토 가능* 만 의미. 실제 활성화는:
1. 별도 옵트인 PR
2. 사용자 명시 승인
3. `ENABLE_AI_EXECUTION=true` 전환 + KIS AI 라우팅 활성화 코드
4. 초소액 canary (최소 1주, 1일 1주 정도)
5. KillSwitch drill — 활성화 첫날 LEVEL_1 / LEVEL_2 / LEVEL_3 토글 확인
6. 즉시 kill switch 가능 + 비정상 손실 시 즉시 false로 되돌림

모두 별도 절차로 분리.

### 남은 AI Execution Gate backlog
- env override (`AI_EXECUTION_MAX_ORDER_NOTIONAL` 등)
- 자동 collector — 다른 게이트들의 verdict를 자동 가져와 입력 채움
- KillSwitch drill 자동 검증 (최근 N일 토글 이력)
- 실시간 monitoring 메트릭 연동
- 별도 PR로 활성화 runbook (`live_ai_execution_activation_runbook.md`)
- #45 order-time AIExecutionGate와 본 #75 activation gate 결합 평가 페이지

---

## #76 Futures Promotion Policy (선물 단계별 승격 + AI 영구 BLOCKED)

> 선물 기능의 *단계별 승격 정책*을 정리하는 문서 작업. **본 PR로 선물 LIVE
> 활성화 0건**. 코드 변경 없음 — 정책 / 문서 / 테스트만 추가.

### 생성 / 수정 파일
- `docs/futures_promotion_policy.md` (신규) — 7단계 승격 매트릭스 + 단계별 PASS 조건 + 영구 BLOCKED 정책
- `docs/futures_scope.md` (수정) — 본 정책 링크 추가
- `docs/futures_margin_risk.md` (수정) — Rule이 단계별로 어떻게 적용되는지 매트릭스 링크 추가
- `docs/futures_strategy_contract.md` (수정) — 전략 승격은 본 정책 단계 기준
- `docs/live_activation_blockers.md` (수정) — §3.1에 본 정책 PASS 항목 추가
- `docs/promotion_policy.md` (수정) — 주식 정책임을 명시, 선물은 본 문서 별도
- `CLAUDE.md` (수정) — 선물은 마지막 + AI Execution 영구 BLOCKED 안내
- `README.md` (수정) — 정책 링크 추가
- `docs/final_completion_summary.md` (수정) — 본 항목
- `backend/tests/test_futures_promotion_policy_doc.py` (신규, doc-only 정적 가드)

### 선물 7단계 승격 기준 요약
1. **FUTURES_DISABLED** (default) — `ENABLE_FUTURES_LIVE_TRADING=false`
2. **FUTURES_SIMULATION** — MockFuturesBroker only, ≥500 case stress
3. **FUTURES_SHADOW** — 실 시세 read-only, ≥4주, ≥100 signal, would-have 기록
4. **FUTURES_PAPER** — 모의/mock, ≥4~8주, ≥100~200 paper 주문, expectancy>0, PF≥1.2
5. **FUTURES_MANUAL_APPROVAL** — 초소액, max_contracts=1, 모든 주문 사람 승인, 1~2개월
6. **FUTURES_AI_ASSIST** — AI 후보 제안만, 사람 승인 필수, AI proposal ≥100
7. **FUTURES_AI_EXECUTION_BLOCKED** — **영구 BLOCKED** (본 프로젝트 미허용)

### 선물 손실한도 / 증거금 / 청산 정책
| 항목 | PAPER | MANUAL | AI_ASSIST |
|---|---|---|---|
| `max_contracts` | ≤2 | **1** | **1** |
| `max_leverage` | 50% of leverage_max | 30% | 30% |
| `max_daily_futures_loss` | 운용 자본의 2% | 운용 자본의 0.5% | 운용 자본의 0.5% |
| 청산 거리 임계 | ≤3% BLOCK, 3~7% WARN | 동일 | 동일 + WARN 시 추가 사람 검토 |

### 선물 AI 실행 지연 정책 (영구 BLOCKED)
- `FUTURES_AI_EXECUTION`은 본 프로젝트가 *영구* 허용하지 않음
- 주식 AI Execution(#75)보다 위험 한 등급 더 높음 (레버리지 + 강제청산 + 만기 + 24h)
- `AIExecutionActivationGateResult.futures_allowed=False` 불변 (#75)
- 입력 `futures_target=True` 또는 `enable_futures_live_trading=True` → 즉시 BLOCKED
- 미래 검토 시 별도 정책 문서 + 별도 게이트 + 별도 9단계 blocker + 사용자 명시 승인 모두 필요

### 만기 / 롤오버 정책
- 자동 롤오버 — 모든 단계에서 *금지* (advisory plan만 허용 #49)
- 만기 5일 이내 신규 진입 — watch only 강등 (PAPER 이상에서 차월물 권장)
- 만기일 근처 AI 자동매매 — 영구 금지

### 문서 링크 보강
9개 문서 cross-link: futures_scope / futures_margin_risk / futures_strategy_contract /
live_activation_blockers / promotion_policy / CLAUDE / README / final_completion_summary.

### 안전 invariant (테스트로 lock)
- ✓ `docs/futures_promotion_policy.md` 존재
- ✓ 핵심 키워드: "영구 BLOCKED" / "futures_allowed=False" / "FUTURES_AI_EXECUTION"
- ✓ 자동 롤오버 금지 / 만기일 근처 AI 자동매매 금지 명시
- ✓ `ENABLE_FUTURES_LIVE_TRADING` 변경 0건 — 본 문서는 *문서*에서만 언급 (코드 mutate 0건)
- ✓ Secret 패턴 0건 (KIS_APP_KEY=value-shape / sk-... / Bearer ...)
- ✓ 실제 broker API 호출 코드 0개 추가
- ✓ `app/` 운영 코드 변경 0건 — 본 PR은 문서 + 정적 doc 가드 테스트만

### 테스트 결과
- **신규 backend**: doc 가드 테스트 (정적 grep — 본 문서가 코드/Secret/flag mutate 형식을 포함하지 않음)
- Regression: 본 PR이 `app/` 변경 0건이라 영향 없음

### 실거래 / 선물 LIVE / AI 자동매매 금지 invariant — 본 PR 미변경
- ✓ `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `ENABLE_FUTURES_LIVE_TRADING=false`, `KIS_IS_PAPER=true`
- ✓ `app/core/config.py` 변경 0건
- ✓ `app/futures/*` 변경 0건 (기존 FuturesSimulation / MockFuturesBroker / FuturesRiskManager 그대로)
- ✓ `.env` / Secret / API Key / 계좌번호 변경 0건
- ✓ 절대 원칙 1~6 모두 유지

### 남은 Futures Gate backlog
- futures simulation stress (500+ case 자동 테스트 별도 PR)
- `FuturesPaperGate` 평가기 (Paper Gate #72 패턴 차용, 별도 PR)
- `FuturesManualGate` 평가기 (Live Manual Gate #73 패턴 차용)
- futures trading calendar 데이터 소스 (영업일 / 만기 / SQ)
- 자동 롤오버 시뮬 검증 (가상 환경에서 advisory 평가만)
- 증거금 reconciliation 가상 산식 vs broker API 차이 측정
- 강제청산 시나리오 드릴 (운영자 수동 검증)
- (영구 BLOCKED — 진행 안 함) FUTURES_AI_EXECUTION 게이트 구현

---

## #77 Alpha Decay Monitor (전략 알파 감쇠 read-only 분석)

> 전략별 알파 감쇠를 추적하는 read-only 모니터. **자동 비활성/삭제 절대 금지**.
> 결과는 운영자 / Strategy Researcher Agent(#55) / Promotion Gate(#27) 참고용.

### 생성 / 수정 파일
- `backend/app/governance/alpha_decay.py` (신규) —
  `AlphaDecayInput` / `AlphaDecayResult` / `AlphaDecayStatus` / `AlphaDecayKind` /
  `AlphaDecayThresholds` / `StrategyMetricsSnapshot` + `evaluate_alpha_decay()` +
  `compute_alpha_decay_score()`
- `backend/app/api/routes_governance.py` (수정) — `POST /governance/alpha-decay/evaluate`
- `backend/tests/test_alpha_decay.py` (신규, **29 PASS**)
- `frontend/src/services/backend/client.js` (수정) — 1 helper
- `frontend/src/components/tabs/AlphaDecayCard.jsx` (신규)
- `frontend/src/components/tabs/AlphaDecayCard.test.jsx` (신규, **10 PASS**)
- `docs/alpha_decay_monitor.md` (신규)
- `docs/backlog.md` (수정) — #77 후속 backlog 추가
- `CLAUDE.md` / `README.md` / `docs/final_completion_summary.md` (수정)

### alpha_decay_score 기준 (0~100, 감점 누적)
| 신호 | 가중치 default |
|---|---|
| expectancy_drop (비율 기반) | 25 |
| expectancy_flip_to_negative (양수→음수) | +25 추가 |
| pf_drop (비율 기반) | 20 |
| pf_below_min (PF<1.2) | +15 추가 |
| winrate_drop | 10 |
| mdd_worsen (baseline 1.5배 이상) | 15 |
| consec_losses_increase (2배 이상) | 10 |
| data_quality_issue (<75) | 15 |
| regime_change | 5 |

### Status 매핑
| Score | Status |
|---|---|
| −1 (표본 부족) | `INSUFFICIENT_DATA` |
| 0~24 | `HEALTHY` |
| 25~49 | `WATCH` |
| 50~74 | `DECAY_WARNING` |
| 75~100 | `DISABLE_CANDIDATE` |

### 단기 부진 vs 구조적 성능저하 구분 방식
6종 `AlphaDecayKind` 분류 (우선순위 순):
1. **INSUFFICIENT_DATA** — recent trade_count < 20
2. **DATA_QUALITY_ISSUE** — recent_data_quality_score < 60
3. **REGIME_MISMATCH** — regime 변경 + 핵심 지표 악화 ≤ 1개 (단기 부진 가능성)
4. **STRUCTURAL_DECAY** — 핵심 지표 **≥3개** 동시 악화 (구조적 감쇠 의심)
5. **SHORT_TERM_DRAWDOWN** — 핵심 지표 1~2개만 악화 (단기 부진)
6. **NONE** — 악화 신호 0건 (HEALTHY)

핵심 지표는 `regime_change` / `data_quality_issue` 를 *제외*한 나머지 (성과 지표).

### UI / API 변경
- 신규 API: `POST /api/governance/alpha-decay/evaluate`
- 신규 UI: `AlphaDecayCard` — status badge + "비활성 후보 (자동 비활성 아님)" 보조 배지 + score / kind / 메트릭 Δ 비교 + 악화 신호 chip + 권장 조치 + cautions
- "전략 비활성화" / "전략 삭제" / "Disable Strategy" / "Apply Parameters" / "파라미터 적용" / "promotion 변경" / "AI 자동매매 활성화" / "Place Order" 라벨 버튼 0개

### 안전 invariant (테스트로 lock)
- ✓ broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` / `app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` import 0건
- ✓ `broker.place_order(` / `route_order(` / `OrderExecutor(` / `submit_candidate(` / `AiClient(` 호출 0건
- ✓ DB write 0건
- ✓ `settings.enable_*_trading =` mutate 0건
- ✓ `from app.core.config import` / `get_settings(` 호출 0건
- ✓ `.save_params(` / `.apply_params(` / `strategy.enabled = False` / `PromotionGate(` / `evaluate_promotion(` / `.set_emergency_stop(` 호출 0건 (전략 / promotion 상태 mutate 시도 0건)
- ✓ `AlphaDecayResult.is_order_signal=True` 생성 불가 (ValueError)
- ✓ `AlphaDecayResult.auto_disable=True` 생성 불가
- ✓ `AlphaDecayResult.auto_apply_allowed=True` 생성 불가
- ✓ UI 카드의 전략 비활성/삭제/파라미터 적용/promotion 변경 라벨 버튼 0개
- ✓ UI / 응답에 BUY/SELL/HOLD / Secret 패턴 0건

### 테스트 결과
- **신규 backend**: 29 PASS (DTO invariant 4 + happy/expectancy/pf/mdd/winrate/consec/data_quality/regime 등 15 + kind 분류 4 + score clamp 1 + threshold override 1 + recommendation 1 + API 3 + 정적 grep 6 — 실제 합)
- **신규 frontend**: 10 PASS (HEALTHY 배지 / DISABLE_CANDIDATE 보조 배지 / INSUFFICIENT_DATA / 고지 영구 / 활성화 버튼 0개 / BUY-SELL 0건 / Secret 비노출 / signals + recommendation + cautions / 평가 버튼 라벨 / 메트릭 Δ 비교)
- Regression: alpha_decay + ai_execution + ai_assist + live_manual + paper + monitoring 합산 무회귀
- Ruff 신규 파일: clean

### 실거래 / AI 자동매매 / 전략 변경 금지 invariant — 본 PR 미변경
- ✓ `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `ENABLE_FUTURES_LIVE_TRADING=false`, `KIS_IS_PAPER=true`
- ✓ `app/core/config.py` 변경 0건
- ✓ `app/strategies/*` 변경 0건 (전략 base / 구체 전략 / live engine 모두 그대로)
- ✓ `app/governance/strategy_promotion.py` 변경 0건 (Promotion Gate 그대로)
- ✓ `.env` / Secret / API Key / 계좌번호 변경 0건
- ✓ 실제 broker / KIS / Anthropic / OpenAI 호출 0건
- ✓ 절대 원칙 1~6 모두 유지

### DISABLE_CANDIDATE = 자동 비활성 아님 (강조)
DISABLE_CANDIDATE verdict 는 *비활성 후보 표시*일 뿐. 실제 전략 변경 절차:
1. Strategy Researcher Agent(#55) 분석 실행 (별도 PR 검토용 자료)
2. backtest 재검증 (#27 PromotionGate 단계 재진입 검토)
3. 별도 PR로 전략 코드 / 설정 변경
4. 운영자 명시 승인 (PR review)
5. Paper Gate(#72) / Live Manual Gate(#73) / AI Assist Gate(#74) 단계 재통과

모두 본 모듈 *밖*의 절차.

### 남은 Alpha Decay backlog
- regime-aware alpha decay (regime별 baseline / recent 분리)
- portfolio-level decay (전략간 상관관계 / 분산)
- 자동 collector (BacktestRun + OrderAuditLog → input)
- decay history 추적 (시계열)
- Strategy Researcher #55 자동 매핑
- Notification 연계 (DISABLE_CANDIDATE 진입 시 알림)
- Bayesian 신뢰 구간
- 시장 regime 자동 분류

---

## #78 Correlation Guard (sector/theme 익스포저 사전 검사)

> 신규 BUY가 동일 sector / theme 종목에 *과도하게 집중*되지 않도록 사전
> 검사하는 pre-trade guard. SELL/EXIT은 *리스크 축소* 목적이라 가드 우회.

### 생성 / 수정 파일
- `backend/app/risk/correlation_guard.py` (신규) —
  `CorrelationGuardRule` / `CorrelationGuardPolicy` / `CorrelationGuardInput` /
  `CorrelationGuardResult` / `CorrelationGuardVerdict` / `SymbolMeta` /
  `HeldPosition` / `CandidateOrder` + `compute_return_correlation()` +
  `returns_from_closes()` helpers
- `backend/app/api/routes_risk.py` (수정) — `POST /risk/correlation-guard/preview`
- `backend/tests/test_correlation_guard.py` (신규, **35 PASS**)
- `frontend/src/services/backend/client.js` (수정) — 1 helper
- `frontend/src/components/tabs/CorrelationGuardCard.jsx` (신규)
- `frontend/src/components/tabs/CorrelationGuardCard.test.jsx` (신규, **9 PASS**)
- `docs/correlation_guard_policy.md` (신규)
- `docs/backlog.md` (수정) — #78 후속 backlog
- `CLAUDE.md` / `README.md` / `docs/final_completion_summary.md` (수정)

### CorrelationGuardRule 구조
- `evaluate(input) → CorrelationGuardResult`
- 4 verdict: `PASS` / `WARN` / `REJECT` / `SKIP_NON_BUY`
- SELL/EXIT은 첫 줄에서 즉시 `SKIP_NON_BUY` (invariant)
- 같은 심볼 재매수는 종목 수 카운트 증가 X (노출은 누적)
- WARN threshold: `warn_ratio` (default 0.8) × REJECT 임계

### sector/theme exposure 제한
| 항목 | 의미 |
|---|---|
| `max_symbols_per_sector` | 동일 섹터 종목 수 상한 |
| `max_sector_exposure` (KRW) | 섹터 절대 노출 상한 |
| `max_sector_exposure_pct` (0~1) | equity 대비 섹터 비율 상한 |
| `max_symbols_per_theme` | 동일 테마 종목 수 상한 |
| `max_theme_exposure` (KRW) | 테마 절대 노출 상한 |
| `max_theme_exposure_pct` (0~1) | equity 대비 테마 비율 상한 |

모든 한도 0/빈값은 비활성. equity_krw=0이면 % 검사 skip.

### correlation helper (후속 PR 자리)
- `compute_return_correlation(series_a, series_b, min_bars=20) → float | None`
  - Pearson 상관계수, 표본 부족 시 None (검사 skip)
  - 분산 0이면 None
- `returns_from_closes(closes) → list[float]`
  - 단순 수익률 변환, 0/음수 close skip

### BUY / SELL 정책 차이
- **BUY**: 한도 위반 시 REJECT
- **SELL / EXIT**: 항상 `SKIP_NON_BUY` — 리스크 축소 차단 안 함

### UI / API 변경
- 신규 API: `POST /api/risk/correlation-guard/preview` (read-only)
- 신규 UI: `CorrelationGuardCard` — verdict 배지 + 예상 sector 종목 수 / 노출 +
  현재 sector / theme 노출 테이블 + 차단/주의 사유 + SELL 우회 disclaimer
- "Correlation 사전 검사" 버튼만 — 주문 실행 / 정책 적용 / ENABLE_* 라벨 버튼 0개

### 테스트 결과
- **신규 backend**: 35 PASS (DTO invariant 3 + SELL pass-through 2 + sector
  count/exposure/pct 5 + theme count/exposure/pct/multi 4 + 같은 심볼 재매수 1 +
  empty policy / no meta 2 + exposure carry 1 + correlation helpers 6 +
  API 4 + 정적 grep 5)
- **신규 frontend**: 9 PASS (PASS 배지 / REJECT 사유 / SELL 우회 / disclaimer /
  실행 버튼 0개 / BUY-SELL 0건 / Secret 비노출 / 평가 버튼 라벨 / 예상 노출 표시)
- Regression: correlation_guard + alpha_decay + ai_execution + ai_assist +
  paper + monitoring 합산 무회귀
- Ruff 신규 파일: clean

### 안전 invariant (테스트로 lock)
- ✓ broker / OrderExecutor / route_order / paper_trader / 외부 HTTP / AI SDK import 0건
- ✓ `broker.place_order(` / `route_order(` / `OrderExecutor(` / `submit_candidate(` / `AiClient(` 호출 0건
- ✓ DB write 0건
- ✓ `from app.core.config import` / `get_settings(` 호출 0건
- ✓ `settings.enable_*_trading =` mutate 0건
- ✓ `CorrelationGuardResult.is_order_signal=True` / `auto_apply_allowed=True` 생성 불가 (ValueError)
- ✓ SELL/EXIT → `SKIP_NON_BUY` 영구 (테스트로 lock)
- ✓ UI 카드 "주문 실행" / "정책 적용" / "ENABLE_*" 라벨 버튼 0개
- ✓ UI / 응답에 BUY/SELL/HOLD / Secret 패턴 0건

### 실거래 / 정책 변경 금지 invariant — 본 PR 미변경
- ✓ `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `ENABLE_FUTURES_LIVE_TRADING=false`
- ✓ `app/core/config.py` 변경 0건
- ✓ `app/risk/risk_manager.py` / `app/risk/position_limits.py` / `app/risk/order_guard.py` 변경 0건
- ✓ `app/execution/*` 변경 0건
- ✓ `.env` / Secret / API Key / 계좌번호 변경 0건
- ✓ 실제 broker / KIS / Anthropic / OpenAI 호출 0건
- ✓ 절대 원칙 1~6 모두 유지

### 남은 Correlation Guard backlog
- sector master (KOSPI200 GICS / FICS 일관 분류)
- theme exposure dashboard 시각화
- correlation matrix + heatmap
- portfolio risk heatmap
- regime-aware correlation (bull / bear / range 별도 lookback)
- 자동 collector (symbol → sector / themes 매핑 자동화)
- RiskManager 자동 통합 (현재는 호출자 별도 호출)

---

## #79 Loss Tagging (손실 거래 *추정* 원인 태깅)

> 손실 거래의 *추정* 원인을 자동 태깅 + LossReasonLog 에 *append*.
> **태그는 추정값이며 확정 원인이 아니다.** 운영자 검토 필요, 주문 차단/실행
> 트리거로 사용 금지.

### 생성 / 수정 파일
- `backend/app/analytics/__init__.py` (신규)
- `backend/app/analytics/loss_tagging.py` (신규) — evaluator + 7카테고리 25 tag
  + `LossEstimateInput` / `LossEstimateResult` / `LossEstimateThresholds` +
  `estimate_loss_reasons()` + 3 summary helpers
- `backend/app/analytics/loss_tagging_storage.py` (신규) — append + review +
  list + summarize (DB)
- `backend/alembic/versions/20260526_0022_loss_reason_log.py` (신규) — 마이그레이션 0022
- `backend/app/db/models.py` (수정) — `LossReasonLog` 모델 추가
- `backend/app/api/routes_analytics.py` (신규) — 4 endpoint (estimate / summary /
  recent / review)
- `backend/app/main.py` (수정) — analytics_router 등록
- `backend/tests/test_loss_tagging.py` (신규, **57 PASS**)
- `frontend/src/services/backend/client.js` (수정) — 4 helper 추가
- `frontend/src/components/tabs/LossReasonCard.jsx` (신규)
- `frontend/src/components/tabs/LossReasonCard.test.jsx` (신규, **10 PASS**)
- `docs/loss_tagging_policy.md` (신규)
- `CLAUDE.md` / `README.md` / `docs/final_completion_summary.md` /
  `docs/backlog.md` (수정)

### LossReasonTag 목록 (7카테고리 × 25 tag)
- **strategy** (7): stop_loss_hit / failed_breakout / false_rebreak / vwap_loss / target_not_reached / time_stop / reversal_signal
- **market** (4): market_selloff / sector_drop / regime_change / volatility_spike
- **execution** (4): low_liquidity / high_slippage / partial_fill / price_gap
- **risk** (3): risk_limit_hit / emergency_stop / over_exposure
- **data** (3): data_stale / bad_quote / missing_bar
- **agent** (3): ai_overconfidence / ai_low_confidence / news_theme_faded
- **unknown** (1): unknown (자동 분류 실패 fallback)

### Primary tag 우선순위
risk > data > market > execution > strategy > agent > unknown

### 추정 규칙 (rule-based 휴리스틱)
- `stop_loss_hit`: exit_price가 stop_price ±1% 이내
- `failed_breakout` / `false_rebreak`: pattern flag 입력
- `vwap_loss`: long의 경우 exit < entry_vwap
- `time_stop`: hold_minutes ≥ 임계 (default 180분)
- `market_selloff`: kospi_return ≤ -1.5%
- `sector_drop`: sector_return ≤ -2.0%
- `regime_change`: regime_at_entry != regime_at_exit
- `volatility_spike`: volatility_pct ≥ 5%
- `low_liquidity`: exit_volume / entry_volume < 30%
- `high_slippage`: slippage_bps ≥ 50bps
- `partial_fill`: partial_fill_ratio < 95%
- `price_gap`: |gap_ratio| ≥ 2%
- `risk_limit_hit` / `emergency_stop` / `over_exposure`: bool flag
- `data_stale` / `bad_quote` / `missing_bar`: 입력 신호
- `ai_overconfidence`: ai_entry_confidence ≥ 80
- `ai_low_confidence`: ai_entry_confidence ≤ 40
- `news_theme_faded`: 진입 활성 + 청산 소멸

### 저장 방식 (LossReasonLog)
- `source_table` / `source_id` / `symbol` / `strategy` / `mode`
- `trade_pnl` / `is_loss` / `primary_tag` / `primary_category` / `tags` (JSON) / `rationale` (JSON) / `confidence`
- `is_estimated` = True 영구
- `review_status` / `reviewed_by` / `review_note` / `reviewed_at` — 운영자만 갱신
- **DELETE 경로 0건** — append + review only (정적 grep 가드)

### UI / API 변경
- 신규 API: `POST /api/analytics/loss-tags/estimate` / `GET /summary` / `GET /recent` / `PATCH /{id}/review`
- 신규 UI: `LossReasonCard` — 통계 + Top tags + 카테고리 분포 + 전략별 + 최근 손실 거래 + "추정 원인 · 확정 원인 아님" 영구 배지

### 테스트 결과
- **신규 backend**: 57 PASS (DTO invariant 4 + is_loss 3 + strategy tags 7 +
  execution 4 + market 4 + risk 3 + data 3 + agent 3 + 우선순위 / multi 3 +
  summary helpers 4 + storage 5 + API 6 + DELETE 없음 1 + 정적 grep 6 + 보조)
- **신규 frontend**: 10 PASS (영구 배지 / disclaimer / 4 섹션 노출 / recent
  primary_tag + review / stats / 강제 적용 버튼 0개 / "원인" 단독 표현 없음 /
  BUY-SELL 0건 / Secret 비노출 / 새로 고침 버튼 라벨)
- Regression: loss_tagging + correlation + alpha_decay + 4개 게이트 합산 무회귀
- Ruff 신규 파일: clean

### 안전 invariant (테스트로 lock)
- ✓ broker / OrderExecutor / route_order / paper_trader / 외부 HTTP / AI SDK import 0건
- ✓ `broker.place_order(` / `route_order(` / `OrderExecutor(` / `submit_candidate(` / `AiClient(` 호출 0건
- ✓ `settings.enable_*_trading =` mutate 0건
- ✓ evaluator `loss_tagging.py` 는 DB write 0건 (storage 만 INSERT)
- ✓ storage 는 `db.delete(` / `DELETE FROM` 0건
- ✓ routes 는 `@router.delete` 0건 (DELETE 메서드 405 응답)
- ✓ `LossEstimateResult.is_estimated=False` / `is_order_signal=True` / `is_investment_advice=True` 생성 불가 (ValueError)
- ✓ UI에 "강제 적용" / "자동 비활성" / "전략 비활성화" / "삭제" / "확정 원인" / "주문 차단 적용" / "ENABLE_*" / "Place Order" 라벨 버튼 0개
- ✓ UI / 응답에 BUY/SELL/HOLD / Secret 패턴 0건
- ✓ "추정 원인 · 확정 원인 아님" UI 영구 노출 (테스트로 lock)

### 실거래 / 자동 정책 변경 / 자동 비활성 금지 invariant — 본 PR 미변경
- ✓ `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `ENABLE_FUTURES_LIVE_TRADING=false`
- ✓ `app/core/config.py` 변경 0건
- ✓ `app/risk/*` / `app/execution/*` 변경 0건
- ✓ `.env` / Secret / API Key / 계좌번호 변경 0건
- ✓ 실제 broker / KIS / Anthropic / OpenAI 호출 0건
- ✓ 절대 원칙 1~6 모두 유지

### 남은 Loss Tagging backlog
- ML 기반 분류 (현재는 룰베이스 휴리스틱)
- operator feedback loop (review_status 통계 → 추정 룰 가중치 조정)
- strategy별 loss pattern dashboard 시각화
- 자동 collector (OrderAuditLog 청산 row → 자동 estimate + append)
- AgentMemory(#56) 통합 — review_note 있는 row → Memory 저장
- DailyReportAgent / StrategyResearcherAgent / RiskAuditorAgent 자동 호출
- multi-tag confidence weighting (tag 수가 아니라 강도 가중치)

---

## #80 Pre-market Checklist (장 시작 전 자동 점검)

> 자동매매 시작 *전* 안전 점검. **required FAIL 1건이라도 있으면
> `start_allowed=False`** — manual ack 도 우회 불가. 본 게이트는 자동매매를
> *실행하지 않으며* 안전 플래그를 *변경하지 않는다*.

### 생성 / 수정 파일
- `backend/app/governance/pre_market_check.py` (신규) — `CheckItem` /
  `CheckCategory` / `CheckStatus` / `PreMarketVerdict` /
  `PreMarketCheckInput` / `PreMarketCheckResult` +
  `evaluate_pre_market_check()` + `render_markdown_report()` + mode-aware
  `_required_for_mode()` 매트릭스
- `backend/app/api/routes_governance.py` (수정) — `GET/POST
  /governance/pre-market-check`
- `scripts/pre_market_check.py` (신규) — CLI
- `backend/tests/test_pre_market_check.py` (신규, **41 PASS**)
- `frontend/src/services/backend/client.js` (수정) — 2 helper
- `frontend/src/components/tabs/PreMarketCheckCard.jsx` (신규)
- `frontend/src/components/tabs/PreMarketCheckCard.test.jsx` (신규, **10 PASS**)
- `docs/pre_market_check_policy.md` (신규)
- `CLAUDE.md` / `README.md` / `docs/final_completion_summary.md` /
  `docs/backlog.md` (수정)

### 점검 항목 (11 카테고리)
| 카테고리 | 항목 |
|---|---|
| api / db | API 서버 / DB ping |
| broker | broker_ready / kis_is_paper / kis_credentials_present |
| data | data_freshness_ok / stale_symbol_count |
| watchlist | watchlist_item_count |
| strategy | active_strategy_count |
| risk | risk_policy / position_limits / daily_loss_limit + daily_loss_used_ratio |
| kill_switch | emergency_stop / level |
| agent | ai_permission_gate / live_trading_flag / ai_execution_flag / futures_live_flag |
| notification | (optional) |
| governance | paper_gate / live_manual_gate / ai_assist_gate / ai_execution_gate |

### Mode 별 required check 매트릭스
| Check | SIM | PAPER | LIVE_SHADOW | LIVE_MANUAL | LIVE_AI_ASSIST | LIVE_AI_EXEC |
|---|---|---|---|---|---|---|
| api / db / watchlist / risk_policy / kill_switch | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| broker_paper | — | ✓ | — | — | — | — |
| broker_live_readonly | — | — | ✓ | ✓ | ✓ | ✓ |
| data_freshness / daily_loss_limit | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| paper_gate / live_manual_gate | — | — | — | ✓ | ✓ | ✓ |
| ai_permission_gate / ai_assist_gate | — | — | — | — | ✓ | ✓ |
| ai_execution_gate / ai_execution_flag | — | — | — | — | — | ✓ |
| live_trading_flag | — | — | — | ✓ | ✓ | ✓ |
| futures_live_flag | (true 시 모든 모드 BLOCK — 본 게이트는 선물 LIVE 미허용) |
| notification | optional (WARN only) |

### CLI / API 사용법
```bash
# SIMULATION dry-run.
python scripts/pre_market_check.py --mode SIMULATION

# PAPER + 운영자 입력.
python scripts/pre_market_check.py --mode PAPER \
  --broker-ready --kis-is-paper --kis-credentials-present \
  --data-freshness-ok --watchlist 5 --strategies 2 \
  --daily-loss-limit-configured --format json
```
```http
GET /api/governance/pre-market-check?mode=PAPER&strict=true
POST /api/governance/pre-market-check  { ... 입력 ... }
```
CLI exit code: 0=start_allowed=True / 1=False / 2=실행 오류.

### UI 변경
- 신규 UI: `PreMarketCheckCard` — 모바일 헤드라인 ("오늘 자동운용 가능" /
  "주의 필요" / "시작 금지") + start_allowed 명시 + 실패/경고/조치 + 세부
  항목 펼치기 + "다시 점검" / "확인했습니다" 두 버튼만.
- "자동매매 시작" / "Start Bot" / "mode 변경" / "활성화 토글" / "ENABLE_*" /
  "Place Order" / "실거래 활성화" 라벨 버튼 **0개** (테스트로 lock).

### 테스트 결과
- **신규 backend**: 41 PASS (DTO invariant 4 + happy/SIM/PAPER 5 + kill switch +
  daily loss limit 2 + LIVE_MANUAL 4 + LIVE_AI_* 3 + futures flag 1 + manual
  ack non-bypass 2 + strict 2 + warnings 2 + markdown 2 + API 4 + CLI smoke 2 +
  정적 grep 6)
- **신규 frontend**: 10 PASS (READY / DO_NOT_START / WARN 헤드라인 / disclaimer
  영구 / ack 비우회 / 시작 버튼 0개 / BUY-SELL 0건 / Secret 비노출 / 세부
  토글 / 버튼 라벨)
- Ruff 신규 파일: clean

### 안전 invariant (테스트로 lock)
- ✓ broker / OrderExecutor / route_order / paper_trader / 외부 HTTP / AI SDK import 0건
- ✓ `broker.place_order(` / `route_order(` / `OrderExecutor(` / `submit_candidate(` / `AiClient(` 호출 0건
- ✓ `from app.core.config import` / `get_settings(` 호출 0건 (evaluator는 입력 DTO만)
- ✓ `settings.enable_*_trading =` / `os.environ["ENABLE_*"]` mutate 0건
- ✓ DB write 0건
- ✓ `PreMarketCheckResult.is_order_signal=True` / `live_flag_changed=True` / `mode_changed=True` 생성 불가 (ValueError)
- ✓ manual_ack=True 라도 required FAIL 우회 불가 (`start_allowed=False` 유지)
- ✓ futures_live_flag=true 면 모든 모드에서 BLOCK
- ✓ UI에 "자동매매 시작" / "Start Bot" / "Start Trading" / "mode 변경" / "활성화 토글" / "ENABLE_*" / "Place Order" / "실거래 활성화" 라벨 버튼 0개
- ✓ UI / 응답에 BUY/SELL/HOLD / Secret 패턴 0건

### 실거래 / 자동매매 시작 / 자동 모드 변경 금지 invariant — 본 PR 미변경
- ✓ `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `ENABLE_FUTURES_LIVE_TRADING=false`
- ✓ `app/core/config.py` 변경 0건
- ✓ `app/risk/*` / `app/execution/*` 변경 0건
- ✓ `.env` / Secret / API Key / 계좌번호 변경 0건
- ✓ 실제 broker / KIS / Anthropic / OpenAI 호출 0건
- ✓ 절대 원칙 1~6 모두 유지

### 남은 Pre-market backlog
- 자동 collector (api/health + risk/policy + monitoring → PreMarketCheckInput 자동 매핑)
- 시간 윈도우 검증 (장 시작 직전 자동 cron 실행)
- 결과 영구화 (PreMarketCheckLog 테이블, 현재는 ephemeral)
- Notification 연계 (DO_NOT_START 시 자동 알림)
- BotControl 통합 (시작 버튼이 본 게이트 결과를 *직접* 참조하도록)
- Strategy Researcher 연계 (반복 FAIL 패턴을 학습 자료로)

---

## #81 Strategy Registry beginner metadata (현재 6개 전략 정리)

> 사용자 지시에 따라 *현재 코드에 실제 구현된* 6개 매매 전략에 한해, *기존
> 매매 로직 0줄 변경* 으로 초보자용 메타데이터 레이어만 추가. 가짜 전략명 /
> 경쟁 앱 표현 영구 금지.

### 실제 확인된 매매기법 (6종)
| internal id | 클래스 | displayName (한글) | 위험도 |
|---|---|---|---|
| `sma_crossover` | `SmaCrossoverStrategy` | 단기/장기 이동평균 교차 | 보통 |
| `rsi_reversion` | `RsiReversionStrategy` | RSI 과매도/과매수 회복 | 보통 |
| `vwap_strategy` | `VWAPStrategy` | VWAP 평균 회귀 | 보통 |
| `orb_vwap` | `OrbVwapStrategy` | ORB + VWAP 돌파 | 높음 |
| `volume_breakout` | `VolumeBreakoutStrategy` | 거래량 급증 돌파 | 높음 |
| `pullback_rebreak` | `PullbackRebreakStrategy` | 눌림목 재돌파 | 높음 |

### 제거한 예시 전략명 / 가짜 전략명 영구 금지
- 본 PR 시점에 코드에 *원래부터 없던* 가짜 / 외부 앱식 전략명 — 따라서 제거할
  대상이 없음. 단 영구 차단 패턴 (테스트로 lock): `골든브릿지` / `트라이앵글
  전설` / `다이아 전략` / `퀀텀 점프` / `황금알` / `100% 승률` / `guaranteed` /
  `magic strategy` 등.

### 생성 / 수정 파일
- `backend/app/strategies/registry_metadata.py` (신규) — `BeginnerMetadata` /
  `RiskLevel` / `RecommendedMode` enum + `_BEGINNER_METADATA` 6 entry +
  `list_beginner_registry()` / `validate_metadata()` / helpers
- `backend/app/api/routes_live_engine.py` (수정) — `GET /api/strategies/beginner-registry` 추가
- `backend/tests/test_strategy_registry_metadata.py` (신규, **23 PASS**)
- `frontend/src/services/backend/client.js` (수정) — 1 helper 추가
- `frontend/src/components/tabs/StrategyRegistryCard.jsx` (신규)
- `frontend/src/components/tabs/StrategyRegistryCard.test.jsx` (신규, **10 PASS**)
- `docs/strategy_registry.md` (신규)
- `CLAUDE.md` / `README.md` / `docs/final_completion_summary.md` /
  `docs/backlog.md` (수정)

### 각 전략의 현재 연결 상태
모든 전략 공통:
- **백테스트**: ✅ 가용 (`app/backtest/engine.py` 통해)
- **모의투자(Paper)**: ✅ 가용 (`MockBrokerAdapter` + `PaperTrader`)
- **위험관리**: ✅ `route_order` → `RiskManager` (#34) 단일 진입점 경유
- **실전투자(Live)**: 🛑 **모두 비활성** — `KisBrokerAdapter.place_order(is_paper=False)` 가 `NotImplementedError`. 본 메타가 그 상태를 carry.

### 모의투자 / 백테스트 / 위험관리 연결 매트릭스
| 전략 | 백테스트 | 모의 | LIVE_SHADOW | LIVE_MANUAL_APPROVAL | RiskManager |
|---|---|---|---|---|---|
| 6개 모두 | ✓ | ✓ | ✓ | (라우팅 가능, 실주문은 NotImpl) | ✓ 단일 진입점 |

### UI 표시명 변경 내역
| internal id (변경 X) | 기존 UI 표시 | 신규 UI 표시 |
|---|---|---|
| `sma_crossover` | `sma_crossover` (raw) | **단기/장기 이동평균 교차** (sma_crossover) |
| `rsi_reversion` | `rsi_reversion` | **RSI 과매도/과매수 회복** (rsi_reversion) |
| `vwap_strategy` | `vwap_strategy` | **VWAP 평균 회귀** (vwap_strategy) |
| `orb_vwap` | `orb_vwap` | **ORB + VWAP 돌파** (orb_vwap) |
| `volume_breakout` | `volume_breakout` | **거래량 급증 돌파** (volume_breakout) |
| `pullback_rebreak` | `pullback_rebreak` | **눌림목 재돌파** (pullback_rebreak) |

원칙: displayName 표시 + internal id 괄호 *항상 함께* — 운영자가 로그·audit과 매핑 가능.

### 추가된 데이터 모델
없음. *DB schema 변경 0건* — 본 PR은 메타데이터 in-memory 만 (코드 dict).

### 테스트 결과
- **신규 backend**: 23 PASS (6개 ID inventory 4 + content invariant 3 + safety
  invariant 5 + 가짜 전략명 차단 1 + helper 2 + API 4 + 정적 grep 4)
- **신규 frontend**: 10 PASS (6개 렌더 / displayName+internal id 함께 노출 /
  위험도 / live=false / disclaimer / 활성화 버튼 0개 / 펼치기 / 가짜 전략명 0건 /
  Secret 0건 / 가용 칩)
- Regression: 기존 `/api/strategies/registry` endpoint 호환 (테스트 포함)
- Ruff 신규 파일: clean

### 안전 invariant (테스트로 lock)
- ✓ STRATEGY_REGISTRY 와 beginner_metadata 1:1 일치
- ✓ broker / OrderExecutor / route_order / paper_trader / 외부 HTTP / AI SDK import 0건
- ✓ DB write 0건
- ✓ `STRATEGY_REGISTRY[ ... ] = ...` / `.pop(` / `.update(` mutation 0건
- ✓ `settings.enable_*_trading =` mutate 0건
- ✓ `live_trading_available` 모든 전략 false 영구
- ✓ `is_order_signal=False` / `auto_apply_allowed=False` / `is_investment_advice=False` 응답 entry 영구
- ✓ UI 카드 "전략 활성화" / "비활성화" / "Apply Parameters" / "주문 실행" / "ENABLE_*" / "Place Order" / "실거래 활성화" 라벨 버튼 0개
- ✓ UI / 응답 / 메타 텍스트에 가짜 전략명 0건 (`골든브릿지` / `퀀텀 점프` / `100% 승률` / `guaranteed` / `magic strategy` 등)

### 실거래 / 자동 정책 변경 / 매매 로직 변경 금지 invariant — 본 PR 미변경
- ✓ `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `ENABLE_FUTURES_LIVE_TRADING=false`
- ✓ `app/strategies/concrete/*.py` 변경 0건 (6개 전략 로직 그대로)
- ✓ `app/strategies/base.py` / `live_engine.py` / `quality.py` / `scoreboard.py` 변경 0건
- ✓ `app/risk/*` / `app/execution/*` / `app/core/config.py` 변경 0건
- ✓ `.env` / Secret / API Key / 계좌번호 변경 0건
- ✓ 실제 broker / KIS / Anthropic / OpenAI 호출 0건
- ✓ 절대 원칙 1~6 모두 유지

### 남은 Strategy Registry backlog
- 운영자가 직접 UI 에서 beginner metadata 편집 (현재 코드 hard-coded)
- displayName 다국어 (영문 fallback)
- 전략별 backtest 결과 미니 카드 통합
- 운영 노트 영구화 (DB 테이블)
- displayName 변경 이력 audit
- `recommended_mode` 자동 조정 (Strategy Researcher #55 연계)
- Alpha Decay (#77) 결합 — DISABLE_CANDIDATE 전략 옆에 *비활성 후보* 배지

---

## #82 Strategy displayName UI 적용

> #81 의 beginner metadata 를 기존 UI 컴포넌트 6곳에 적용. **internal id 는
> *항상* 함께 노출** — 운영자 / 로그 / audit 매핑 보존. *기존 매매 로직 0줄
> 변경*, *API 변경 0건*.

### 생성 / 수정 파일 (10개)
- `frontend/src/utils/strategyNames.js` (신규) — `formatStrategyName` /
  `strategyDisplayShort` / `useStrategyDisplayNames` hook (module-level 캐시) /
  `fetchStrategyDisplayLookup`
- `frontend/src/utils/strategyNames.test.js` (신규, **12 PASS**)
- `frontend/src/components/tabs/LiveEngine.jsx` (수정) — ScoreboardCard 행 +
  StatusCard 전략 필드
- `frontend/src/components/tabs/AuditLog.jsx` (수정) — OrderAuditRow strategy
  badge + BacktestStrategyMiniTable 셀 + BacktestExtremesSummary best/worst
- `frontend/src/components/tabs/AgentStatsCard.jsx` (수정) — per-strategy 행
- `frontend/src/components/tabs/DisplayNameIntegration.test.jsx` (신규, **8 PASS**)
- `docs/strategy_registry.md` (수정) — UI 적용 현황 표 추가

### 적용된 UI 위치 (6곳)
| 컴포넌트 | 위치 | 결과 |
|---|---|---|
| OrderAuditRow strategy 배지 | AuditLog.jsx | `단기/장기 이동평균 교차 (sma_crossover)` |
| BacktestStrategyMiniTable 셀 | AuditLog.jsx | 동일 |
| BacktestExtremesSummary best/worst | AuditLog.jsx | 동일 |
| ScoreboardCard 누적 성과 행 | LiveEngine.jsx | 동일 |
| StatusCard "전략" 필드 | LiveEngine.jsx | 동일 |
| AgentStatsCard per-strategy 행 | AgentStatsCard.jsx | 동일 |

### 공통 helper
- `formatStrategyName(id, lookup)` → `"displayName (internal_id)"`
- `strategyDisplayShort(id, lookup)` → `"displayName"` only
- `useStrategyDisplayNames()` hook — module-level 캐시 (한 번 fetch → 6개 컴포넌트 공유)
- `fetchStrategyDisplayLookup()` — Promise dedup + 실패 시 in-flight 해제

### 안전 invariant (테스트로 lock)
- ✓ 모든 적용 위치에서 internal id 가 *항상 함께 노출* (data-internal-id attribute + 본문 텍스트)
- ✓ lookup 부재 / 미등록 id / 네트워크 실패 시 internal id 그대로 (graceful fallback)
- ✓ helper 가 lookup 응답을 *변형하지 않음* (캐시 dict 만 저장)
- ✓ UI 에 가짜 / 외부 hype 전략명 (`골든브릿지` / `100% 승률` / `guaranteed` / `magic strategy` 등) 0건 (통합 테스트로 lock)

### 테스트 결과
- **신규 frontend**: 20 PASS (helper 12 + 통합 8)
- **Regression**: 기존 LiveEngine + AuditLog + AgentStatsCard + StrategyRegistryCard 합산 **396 PASS, 0 fail** (기존 366 + 신규 30)

### 안전 / 변경 금지 invariant — 본 PR 미변경
- ✓ `app/strategies/concrete/*.py` (6개 전략 로직) 변경 0건
- ✓ `app/strategies/base.py` / `live_engine.py` / `quality.py` / `scoreboard.py` 변경 0건
- ✓ `app/risk/*` / `app/execution/*` / `app/core/config.py` 변경 0건
- ✓ `.env` / Secret / API Key / 계좌번호 변경 0건
- ✓ Backend 변경 0건 (frontend + 문서만)
- ✓ Backend API 응답 변경 0건 (`/api/strategies/beginner-registry` 만 사용)
- ✓ `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 변경 0건
- ✓ 절대 원칙 1~6 모두 유지

### 남은 displayName UI backlog
- Approvals.jsx AI hero summary line
- ApprovalQueue.jsx proposal strategy chip
- AgentMemoryCard.jsx memory row metadata
- ExecutionRecommenderCard.jsx proposal strategy display
- BotControl.jsx 전략 선택 dropdown (현재는 internal id 만 표시 예상)
- Dashboard.jsx 24h activity card 전략 컬럼 (있다면)
