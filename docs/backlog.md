# Backlog

본 세션 (147~156) 동안 발견됐지만 NICE-to-have 또는 별도 옵트인이 필요한
항목. 우선순위 순.

## High — 운영 안전성 직결

### ~~1. Daily PnL의 KST 일자 경계~~ ✅ 166에서 해결
- 166 진행: `today_kst()` 헬퍼 + `compute_today_realized_pnl(tz=KST)` 기본값. KST 자정(=15:00 UTC, 장 종료 후) 리셋 → 운영자 직관 일치. `tz=timezone.utc` 명시로 backwards-compat. backend +5 테스트 (KST 자정 boundary / KST vs UTC 분기 / backwards-compat).

### 2. Position vs broker reconciliation
- **현재**: 가상 환경은 단일 진실(`MockBrokerAdapter` 또는 `VirtualOrder`). 실거래 KIS LIVE 활성화 시 broker가 인식한 포지션 vs 백엔드 내부 상태 불일치 가능.
- **변경 안**: 주기적 `broker.get_positions()` vs `compute_open_positions(db)` 비교 → 불일치 시 경고 로그 + Dashboard 배너.
- **차단 조건**: KIS LIVE place_order 활성화 직전에 필요 (LIVE 옵트인 PR과 함께).

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

### 10. Position close → SELL order auto-route
- `compute_open_positions` + `evaluate_close`가 `should_close=True`를 반환했을 때 자동으로 SELL 주문을 만드는 흐름. 현재는 read-only 분석만.

## Low — UI / 분석

### 11. Strategy Scoreboard FE 확장
- 147에서 expectancy / PF / hold time / consec loss / approval rate 5개 metric을 추가했지만 frontend `<ScoreboardCard>`는 7컬럼 한정. 운영자가 클릭해서 상세 펼치기 등 추가.

### 12. Dashboard 통합 — 가상 거래 위젯
- VirtualOrder lifecycle 통계 (NEW / ACCEPTED / PARTIALLY_FILLED / FILLED / EXPIRED / REJECTED / CANCELLED 분포) 카드.

### 13. Backtest 데이터 출처 검증
- `BacktestRun.data_source`만 기록. 합성 데이터로 돌린 backtest를 LIVE 단계 승격에 쓰지 못하도록 promotion_policy 가드.

### 14. 한국어 docs → 영어 번역 (선택)
- 모든 docs가 한글 + 일부 영어. 국제 협업 시 영어 보충.

### 15. Frontend i18n
- 현재 한국어 hard-coded. label 분리 + locale switching.

### ~~16. Frontend lint — 사전 존재 에러 8건~~ ✅ 157에서 해결
- 156 머지 후 별도 PR `feature/157-ci-recovery`에서 8 errors 모두 해결. `useRef(Date.now())` → null + useEffect lazy init / 의도된 setState-in-effect는 disable comment + 사유 / time-bucket 필터의 Date.now() snapshot은 `eslint-disable-next-line react-hooks/purity`. 833 테스트 회귀 0.
- 결과: `npm run lint` → 0 errors / 55 warnings.

## Won't Do (현 세션에서 제외)

- 실거래 KIS API 통합 — 사용자 명시 옵트인 영역.
- LIVE_AI_EXECUTION을 실 broker와 연결 — 동일.
- 선물 라이브 evaluate 로직 활성화 — 동일.
- 외부 모니터링 (Datadog / Sentry) 통합 — MVP 범위 외.

## 관련 문서

- [`docs/final_checklist_report.md`](final_checklist_report.md)
- [`docs/live_activation_blockers.md`](live_activation_blockers.md)
- [`docs/virtual_trading_architecture.md`](virtual_trading_architecture.md)
