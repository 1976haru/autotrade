# Backlog

본 세션 (147~156) 동안 발견됐지만 NICE-to-have 또는 별도 옵트인이 필요한
항목. 우선순위 순.

## High — 운영 안전성 직결

### ~~1. Daily PnL의 KST 일자 경계~~ ✅ 166에서 해결
- 166 진행: `today_kst()` 헬퍼 + `compute_today_realized_pnl(tz=KST)` 기본값. KST 자정(=15:00 UTC, 장 종료 후) 리셋 → 운영자 직관 일치. `tz=timezone.utc` 명시로 backwards-compat. backend +5 테스트 (KST 자정 boundary / KST vs UTC 분기 / backwards-compat).

### ~~2. Position vs broker reconciliation~~ ✅ 212에서 해결
- 212 진행: `app/reconciliation/position_checker.py` 신설 — `aggregate_audit_positions(db)`가 `OrderAuditLog.executed=True + filled_quantity > 0` 행을 walk해 symbol별 net BUY-SELL 포지션을 계산하고, `compare_positions(broker, audit)`가 `broker.get_positions()` 결과와 비교해 `quantity_mismatch`/`broker_only`/`audit_only` 분류로 mismatch 산출. `/api/reconciliation/status` (read-only) endpoint + StrategyRisk 탭 `ReconciliationStatusCard` (DRIFT/IN SYNC 배지 + mismatch 행). archived audit row도 포함 — 보유 포지션은 archive 여부와 무관. backend +21 / frontend +13 테스트. 본 기능은 LIVE 활성화 직전에 운영자가 broker 외부 주문 / 체결 누락 / 동기화 문제를 즉시 감지하기 위한 안전 메커니즘.

### ~~3. Approval queue TTL / expiry~~ ✅ 167에서 해결
- 167 진행: `RiskPolicy.approval_ttl_seconds` (기본 0=비활성, env `APPROVAL_TTL_SECONDS`). `PermissionGate.list_pending(ttl_seconds=N)` lazy expire + `expire_stale_approvals()` 명시 sweep. `STATUS_EXPIRED` 추가 — terminal 상태로 approve/reject/cancel 차단. backend +8 테스트.

### ~~4. OrderAudit 보존 정책~~ ✅ 168에서 해결
- 168 진행: 별도 테이블 대신 `OrderAuditLog.archived` boolean flag 추가 (alembic 0012). 컬럼 drift 위험 없고 atomic. `app/audit/archive.py::mark_orders_older_than_archived(db, *, days, dry_run=False)` 함수로 N일보다 오래된 row를 archived=True로 마크. `/api/audit/orders` 기본은 archived=False만 반환 (hot), `?include_archived=true`로 cold 포함. 운영자가 cron / 명시 호출 결정. backend +9 테스트.

### ~~5. 선물 별도 audit 테이블~~ ✅ 169에서 해결
- 169 진행: `FuturesOrderAuditLog` 테이블 + alembic 0013. `MockFuturesBroker(db=...)` 주입 시 매 broker 호출 후 audit row 추가 — open / close / 강제청산 / insufficient_cash / limit_not_crossed 모두. `forced_liquidation` boolean으로 강제청산 식별. `audit_mode` 인자로 LIVE_FUTURES_SHADOW 등 다른 모드도 carry. backend +10 테스트.

## Medium — 기능 확장

### 6. KIS futures broker adapter
- 별도 옵트인 PR. 본 세션에서는 명시적으로 안 함. `docs/live_activation_blockers.md` 3절 참조.

### 7. LiveAiAgent (실 LLM)
- 현재 `VirtualAiAgent`는 결정적 stub. Anthropic API를 호출해 신호를 만드는 `LiveAiAgent` 별도 PR 필요. 사용량 한도 + retry + 비용 추적도 함께.

### ~~8. Strategy contract validation~~ ✅ 170에서 해결
- 170 진행: `build_strategy()` 기본 enforce_contract=True. base.py default(빈 entry/exit/invalidation, "any" regime, 빈 risk_profile)면 `StrategyContractError`. 백테스트 / 검증 흐름은 `enforce_contract=False` 명시로 우회. `validate_strategy_contract(cls)` 헬퍼 별도 호출 가능. backend +8 테스트.

### ~~9. 봉 데이터 stale detection~~ ✅ 171에서 해결
- 171 진행: `app/market/staleness.py` 신설. `latest_bar_fetched_at(db, symbol, interval)` / `is_bar_cache_stale(db, *, symbol, interval, max_age_seconds, now)` / `stale_symbols(db, interval, max_age_seconds)` 3개 헬퍼. 운영자 / route_order가 호출 결정. SQLite naive datetime은 UTC 가정. backend +11 테스트.

### ~~10. Position close → SELL order auto-route~~ ✅ 172에서 해결
- 172 진행: `app/virtual/auto_close.py::auto_close_position(pos, evaluation, *, mode, broker, risk, db, client_order_id)` — should_close=True PositionSummary를 SELL OrderRequest로 변환 후 `route_order(requested_by_ai=False)`. CloseEvaluation.reason → trade_reason carry (stop_loss/take_profit/time_exit/auto_close). 가드 우회 0 — emergency_stop / LIVE_MANUAL 큐 / RiskManager 모두 적용. backend +10 테스트.

## Low — UI / 분석

### 11. Strategy Scoreboard FE 확장
- 147에서 expectancy / PF / hold time / consec loss / approval rate 5개 metric을 추가했지만 frontend `<ScoreboardCard>`는 7컬럼 한정. 운영자가 클릭해서 상세 펼치기 등 추가.

### 12. Dashboard 통합 — 가상 거래 위젯
- VirtualOrder lifecycle 통계 (NEW / ACCEPTED / PARTIALLY_FILLED / FILLED / EXPIRED / REJECTED / CANCELLED 분포) 카드.

### ~~13. Backtest 데이터 출처 검증~~ ✅ 173에서 해결
- 173 진행: scoreboard `per_strategy[i].runs_by_data_source` (`{"market": 3, "bars": 1, ...}`) 분포 surface. 운영자가 LIVE 승격 결정 시 'market' 비율을 즉시 인지. 'bars'는 운영자 임의 데이터 가능성. backend +4 테스트.

### 14. 한국어 docs → 영어 번역 (선택)
- 모든 docs가 한글 + 일부 영어. 국제 협업 시 영어 보충.

### 15. Frontend i18n
- 현재 한국어 hard-coded. label 분리 + locale switching.

### ~~16. Frontend lint — 사전 존재 에러 8건~~ ✅ 157에서 해결
- 156 머지 후 별도 PR `feature/157-ci-recovery`에서 8 errors 모두 해결. `useRef(Date.now())` → null + useEffect lazy init / 의도된 setState-in-effect는 disable comment + 사유 / time-bucket 필터의 Date.now() snapshot은 `eslint-disable-next-line react-hooks/purity`. 833 테스트 회귀 0.
- 결과: `npm run lint` → 0 errors / 55 warnings.

### 23. Promotion Gate 후속 (#27 PR 이후)
- Cooldown rule (최근 FAIL 전략은 N일 재승격 금지) — 운영 데이터 누적 후.
- Promotion result DB 영구화 (감사 추적).
- Strategy Scoreboard에 promotion gate 결과 통합.
- AI 추천 정확도 자동 산출 (agent_decision_log 분석).
- 임계값(MIN_PROFIT_FACTOR 등) 운영자 UI 조정.
- Paper/Shadow 일수 자동 산출 (audit_log 기반).
- frontend가 backtest run에서 자동으로 PromotionInput 생성.
- 자세한 정책: `docs/strategy_promotion_gate.md`.

### 22. Monte Carlo 후속 (#26 PR 이후)
- Regime별 Monte Carlo (시장 체제 분리 후 재추출).
- Strategy 간 correlation 기반 Monte Carlo.
- Portfolio-level Monte Carlo (여러 전략 합산).
- Intraday path simulation (분/시간 단위 변동) — tick 데이터 통합 후.
- Orderbook-aware slippage simulation — 호가 데이터 통합 후.
- Monte Carlo 결과 영구화 (DB 테이블).
- Equity sample paths chart (frontend).
- Strategy Scoreboard에 MC 통합.
- 자세한 정책: `docs/monte_carlo_policy.md`.

### 21. Backtest Metrics 후속 (#24 PR 이후)
- Walk-forward fold별 metric 분포 — 25번 PR.
- 연속손실 자동 중단 / 사이즈 축소 — LIVE 활성화 PR.
- 시간대별 손익 chart UI — 운영자 요청 누적 후.
- 연환산 Sharpe (봉 간격 인자화).
- Calmar / Sortino / Omega ratio.
- Strategy Scoreboard에 신규 metric 통합.
- `BacktestRequest.min_quality_score` (#21 data_quality와 연동).
- BacktestRun DB에 신규 metric separate 컬럼 (현재는 trades_json에서 재계산).
- 자세한 정책: `docs/backtest_metrics.md`.

### 20. Backtest 비용 모델 후속 (#23 PR 이후)
- 부분 체결 모델 — 운영 데이터에서 필요성 확정 후.
- Volume-aware 슬리피지 (호가 깊이 추정) — 호가 데이터 통합 후.
- 시장 충격 (market impact) 모델 — 대량 주문 단계.
- `BacktestRequest.min_quality_score` — data_quality(#21)와 연동, 24번 Metrics PR.
- Walk-forward fold별 비용 reporting.
- Paper 결과 vs 백테스트 결과 자동 비교 (실측 slippage 검증).
- Frontend BacktestConfig 입력 UI (현재는 raw JSON).
- `BacktestRun` DB에 비용 메타 separate 컬럼.
- 자세한 정책: `docs/backtest_policy.md`.

### 19. Theme/News/Trend 후속 (#22 PR 이후)
- 실 Google Trends API alpha 통합 — alpha 권한 + 운영자 옵트인 PR.
- News API 통합 — rate limit / 약관 검토 후 별도 PR.
- DART 공시 OpenAPI 통합 — 가입 후 별도 PR.
- Manual provider UI / CSV import — 운영자 수동 입력.
- `ThemeSignal` 중복 dedup 정책.
- Strategy/Agent에 `theme_context` read-only 주입 — LIVE strategy 활성화 PR.
- Frontend 테마 분포 카드.
- 테마-단타 성과 상관 분석 리포트.
- 자세한 정책: `docs/theme_signal_policy.md`.

### 18. Data Quality 후속 (#21 PR 이후)
- KRX 휴장일 캘린더 통합 — 정확한 expected_count.
- `BacktestRequest`에 `min_quality_score` / `min_grade` 옵션 + 결과에 품질 메타 carry — 23번 PR.
- Walk-forward runner의 fold별 데이터 품질 평가 + EXCLUDE fold 자동 제외.
- `data_quality_report` DB 테이블 (배치 실행 결과 영구화).
- Frontend 데이터 품질 카드 (Dashboard 또는 Backtest 탭).
- Tick / orderbook 품질 검사 — tick 테이블 도입(Phase 2) 후.
- 자세한 정책: `docs/data_quality_report.md`.

### 17. Data Freshness 후속 (#20 PR 이후)
- KIS / Kiwoom WebSocket 실 feed 상태 통합 (`DataFeedState` provider) — Phase 2 KIS WebSocket adapter PR.
- Redis 기반 feed health 공유 — 다중 worker / 다중 host 운영 시 동기화.
- frontend freshness status card (Dashboard 또는 StrategyRisk 탭) — UI 요청 시.
- `OrderAuditLog`에 `rejected_by_freshness` reason carry — LIVE 활성화 PR에서.
- `route_order` pre-check에 `should_block_buy_*` helper wire — LIVE 활성화 옵트인 PR.
- SELL 전용 freshness helper — BUY와 다른 정책. 운영 데이터 누적 후.
- 자세한 정책: `docs/data_freshness_policy.md`.

## Won't Do (현 세션에서 제외)

- 실거래 KIS API 통합 — 사용자 명시 옵트인 영역.
- LIVE_AI_EXECUTION을 실 broker와 연결 — 동일.
- 선물 라이브 evaluate 로직 활성화 — 동일.
- 외부 모니터링 (Datadog / Sentry) 통합 — MVP 범위 외.

## 관련 문서

- [`docs/final_checklist_report.md`](final_checklist_report.md)
- [`docs/live_activation_blockers.md`](live_activation_blockers.md)
- [`docs/virtual_trading_architecture.md`](virtual_trading_architecture.md)
