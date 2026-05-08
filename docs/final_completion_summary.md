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
