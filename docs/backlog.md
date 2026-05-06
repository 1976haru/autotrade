# Backlog

본 세션 (147~156) 동안 발견됐지만 NICE-to-have 또는 별도 옵트인이 필요한
항목. 우선순위 순.

## High — 운영 안전성 직결

### 1. Daily PnL의 KST 일자 경계
- **현재**: `app/risk/daily_pnl.py::today_utc()` UTC date 기반. 한국 시장과 9시간 차이.
- **제약**: KOSPI는 09:00–15:30 KST. UTC 기준 00:00–06:30. UTC 자정에 PnL이 reset되면 한국 장중에 카운터가 갑자기 0으로 — 운영 의미 손상.
- **변경 안**: `today_kst()` 헬퍼 추가, `compute_today_realized_pnl(today=today_kst())` 호출. 호출자(route_order, PermissionGate.approve)에서 인자 변경.
- **테스트**: 한국 시간 자정 / UTC 자정 / 시장 종료 후 토글 모두 시나리오 추가.

### 2. Position vs broker reconciliation
- **현재**: 가상 환경은 단일 진실(`MockBrokerAdapter` 또는 `VirtualOrder`). 실거래 KIS LIVE 활성화 시 broker가 인식한 포지션 vs 백엔드 내부 상태 불일치 가능.
- **변경 안**: 주기적 `broker.get_positions()` vs `compute_open_positions(db)` 비교 → 불일치 시 경고 로그 + Dashboard 배너.
- **차단 조건**: KIS LIVE place_order 활성화 직전에 필요 (LIVE 옵트인 PR과 함께).

### 3. Approval queue TTL / expiry
- **현재**: `PendingApproval.created_at`에서 시간이 오래 지나도 자동 만료 X. UI는 stale 배지(111)만 표시.
- **변경 안**: Settings에 `APPROVAL_TTL_MINUTES` (default 30분). 백그라운드 sweeper 또는 lazy expiration on read. 만료된 approval은 STATUS=EXPIRED + audit row.
- **무리도**: 작음 — 기존 PermissionGate에 `expire_stale()` 메서드 추가만으로 충분.

### 4. OrderAudit 보존 정책
- **현재**: 무한 누적. 1년 운영 시 수십만 row 가능.
- **변경 안**: `archive_audit_older_than(days)` cron + 별도 `order_audit_log_archive` 테이블. dashboard는 hot table만 본다.

### 5. 선물 별도 audit 테이블
- **현재**: 선물 거래는 `MockFuturesBroker.orders` dict에만 영구화. DB audit row 없음.
- **변경 안**: `futures_order_audit_log` 신규 테이블 + 마이그레이션 0012. `MockFuturesBroker.place_order` 후 audit row 작성.

## Medium — 기능 확장

### 6. KIS futures broker adapter
- 별도 옵트인 PR. 본 세션에서는 명시적으로 안 함. `docs/live_activation_blockers.md` 3절 참조.

### 7. LiveAiAgent (실 LLM)
- 현재 `VirtualAiAgent`는 결정적 stub. Anthropic API를 호출해 신호를 만드는 `LiveAiAgent` 별도 PR 필요. 사용량 한도 + retry + 비용 추적도 함께.

### 8. Strategy contract validation
- `Strategy.entry/exit/invalidation`이 빈 문자열이면 register 거부 — 현재는 surface만 함. `concrete/__init__.py::STRATEGY_REGISTRY` 등록 시 검증.

### 9. 봉 데이터 stale detection (broker가 아닌 데이터 피드)
- 143은 broker quote timestamp 기반. yfinance / KIS bar cache의 `MarketBar.fetched_at`이 오래됐을 때도 별도 가드.

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
