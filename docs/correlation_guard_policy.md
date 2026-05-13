# Correlation Guard Policy — 체크리스트 #78

> 동일 sector / theme 종목 과다 보유를 방지하는 *pre-trade* guard.
> RiskManager의 *하위 가드*로 동작하며 broker / OrderExecutor / route_order 우회 0건.

---

## 1. 목적

- 같은 sector / theme가 *함께 급락*하면 분산 효과가 사라지고 동시 손실이 발생할 수 있다.
- 신규 BUY 가 **동일 sector / theme 종목 과다 보유**를 만들지 않도록 *사전 검사*한다.
- 본 가드는 *주문을 실행하지 않으며*, RiskManager / PermissionGate / OrderExecutor
  흐름을 *대체*하지 않는다 — *하위 pre-trade guard*로만 동작.

---

## 2. 정책 (`CorrelationGuardPolicy`)

| 항목 | 0/빈값 의미 | 비고 |
|---|---|---|
| `max_symbols_per_sector` | 비활성 | 동일 섹터 종목 수 상한 |
| `max_sector_exposure` (KRW) | 비활성 | 섹터별 절대 노출 상한 |
| `max_sector_exposure_pct` (0~1) | 비활성 | equity 대비 섹터 비율 상한 |
| `max_symbols_per_theme` | 비활성 | 동일 테마 종목 수 상한 |
| `max_theme_exposure` (KRW) | 비활성 | 테마별 절대 노출 상한 |
| `max_theme_exposure_pct` (0~1) | 비활성 | equity 대비 테마 비율 상한 |
| `warn_ratio` (default 0.8) | — | REJECT 임계의 80% 이상이면 WARN |
| `max_pairwise_correlation` | 비활성 (후속 PR) | 종목간 수익률 상관계수 상한 |
| `correlation_lookback_bars` | 비활성 (후속 PR) | 상관계수 산정 lookback |

모든 한도는 *상한*. 0 또는 빈 값은 해당 검사를 비활성화한다 — 운영자가
RiskPolicy 어댑터로 보수적으로 설정한다.

---

## 3. 평가 흐름

```text
1. side check
   - BUY  → 본격 검사
   - SELL / EXIT → SKIP_NON_BUY (가드 우회)
2. 현재 sector / theme 노출 집계 (held_positions)
3. candidate가 추가될 때 projected exposure / symbol count 계산
4. 각 임계와 비교
   - 초과 → REJECT (blocked_reasons에 사유)
   - warn_ratio 이상 → WARN (warnings에 사유)
5. PASS / WARN / REJECT / SKIP_NON_BUY 반환
```

### BUY / SELL 정책 차이 (invariant)

- **BUY** → 한도 초과 시 차단 가능. *동시 진입 제한*이 본 가드의 목적.
- **SELL / EXIT** → *리스크 축소* 목적이므로 본 가드가 *기본 허용*
  (`SKIP_NON_BUY`). 매도까지 제한하면 손절 / 차익실현이 막힐 수 있다.

코드 단 invariant: `CorrelationGuardRule.evaluate()` 첫 줄에서 side 확인 후
SELL/EXIT 우회. 테스트 `test_sell_orders_pass_through`로 lock.

### 같은 심볼 재매수 (추가 매수)

- `held_positions` 에 같은 symbol이 이미 있으면 *종목 수* 카운트는 증가하지
  않는다 (sector 종목 수 제한 통과).
- 단, *노출 (notional)* 은 누적 증가하므로 sector / theme 절대 노출 한도와
  equity 비율 한도는 그대로 적용.

---

## 4. 결과 (`CorrelationGuardResult`)

| Verdict | 의미 |
|---|---|
| `PASS`         | 정책 통과 — RiskManager 다음 가드로 진행 |
| `WARN`         | warn_ratio 이상 — 운영자 surface 권장 |
| `REJECT`       | 한도 초과 — BUY 차단 |
| `SKIP_NON_BUY` | SELL/EXIT — 가드 우회 |

invariant:
- `is_order_signal=False` 항상 — 본 가드는 BUY/SELL/HOLD 신호 생성 X.
- `auto_apply_allowed=False` 항상 — 결과는 *제안*일 뿐.

---

## 5. 데이터 소스

본 가드는 *입력 DTO*만 사용. 호출자가 sector / theme 정보를 채워야 한다.

### sector
- `WatchlistItem.sector` (#18 watchlist)
- 호출자가 symbol → sector 매핑 carry.

### theme
- `ThemeSignal.related_symbols` 역인덱스
- 호출자가 symbol → themes[] 매핑 carry.

본 모듈은 **DB SELECT 도 직접 수행하지 않는다** — 순수 evaluator. DB 조회는
호출자(상위 가드 / API endpoint)가 담당.

---

## 6. 상관계수 확장 (후속 PR)

`compute_return_correlation(series_a, series_b, min_bars=20)` helper 제공.

- Pearson 상관계수, 표본 < `min_bars` 면 `None` 반환 (데이터 부족 시 skip).
- `returns_from_closes(closes)` — 종가 → 단순 수익률 변환 (0 / 음수 close skip).

본 PR에서는 `max_pairwise_correlation` 정책 적용은 *비활성*. MarketBar 통합
(#19) 위에서 후속 PR로 활성화.

### 데이터 부족 처리

상관계수가 None (표본 부족)이면 본 검사는 *skip* — sector/theme 검사 결과만으로
PASS / WARN / REJECT 결정. *없는 데이터를 보수적*으로 차단하지 않는다
(SELL을 막지 않는 정신과 동일 — risk 축소 측은 friction이 적어야 함).

---

## 7. Agent 와의 관계

- **RiskOfficer / RiskAuditor Agent(#54)** — 본 결과를 read-only로 carry해
  운영자 surface 가능.
- **ExecutionRecommender Agent(#56)** — proposal 사전 검사용으로 사용 가능.
  proposal 생성 시점에 `CorrelationGuardRule.evaluate()` 호출 → REJECT 면
  제안 자체를 *생성하지 않는다*.
- **StrategySelectionAgent (선택)** — 같은 sector / theme 집중을 피하도록
  종목 후보 다양성 확보.

본 가드는 어떤 agent도 *자동으로 호출하지 않는다* — agent 측 호출 권한.

---

## 8. UI

`frontend/src/components/tabs/CorrelationGuardCard.jsx`:

- 표시: verdict 배지 + 예상 sector 종목 수 / 노출 / 현재 sector & theme 노출 테이블 + 차단/주의 사유.
- 위험 문구 *항상* 노출: "본 카드는 *사전 검사 preview*이며, 실제 주문은 여전히
  RiskManager + PermissionGate + OrderExecutor 를 통과해야 합니다. SELL/EXIT
  은 *리스크 축소* 목적이므로 본 가드가 차단하지 않습니다 (SKIP_NON_BUY)."
- **주문 실행 / 정책 적용 / ENABLE_* 라벨 버튼 0개** (테스트로 lock).
- BUY/SELL/HOLD/긴급정지 토글 문구 0건.
- "Correlation 사전 검사" 버튼 한 개만.

---

## 9. 절대 원칙 — 본 모듈 강제

`tests/test_correlation_guard.py`의 정적 grep 가드:

1. broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` /
   `app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` import 0건.
2. `broker.place_order(` / `route_order(` / `OrderExecutor(` /
   `submit_candidate(` / `AiClient(` 호출 0건.
3. DB write (INSERT/UPDATE/DELETE/.add/.commit/.flush) 0건.
4. `settings.enable_*_trading =` mutate 0건.
5. `from app.core.config import` / `get_settings(` 호출 0건.
6. `CorrelationGuardResult.is_order_signal=True` 생성 불가 (ValueError).
7. `CorrelationGuardResult.auto_apply_allowed=True` 생성 불가.
8. UI 카드의 주문 실행 / 정책 적용 / ENABLE_* 라벨 버튼 0개.
9. UI / 응답에 BUY/SELL/HOLD / Secret 패턴 0건.
10. SELL/EXIT → `SKIP_NON_BUY` invariant (테스트 `test_sell_orders_pass_through`로 lock).

---

## 10. 한계 (운영자 인지 필요)

- **태그 품질 의존** — `WatchlistItem.sector` / `ThemeSignal.related_symbols`
  품질에 결과가 좌우. 잘못된 분류는 가드가 잘못된 신호를 줄 수 있다.
- **실시간 correlation 부재** — 현 PR은 sector / theme 기준. 실제 가격 움직임
  상관계수는 후속 PR.
- **상관관계는 시기에 따라 변함** — bull / bear / range 시장에서 같은 종목간 상관계수
  편차 큼. regime-aware correlation은 후속 과제.
- **소형주 / 신규 상장** — sector / theme 분류가 비어있을 가능성 — 가드 우회.
- **portfolio-level risk 미반영** — 본 가드는 *섹터/테마 집중*만 본다. 베타 /
  VaR 같은 portfolio metric은 별도 모듈.

---

## 11. 후속 backlog

- **sector master** — 일관된 sector 분류 (KOSPI200 GICS / FICS 등)
- **theme exposure dashboard** — 전 종목 sector / theme 노출 시각화
- **correlation matrix** — pairwise 상관계수 매트릭스 + heatmap
- **portfolio risk heatmap** — 포트폴리오 단위 risk 시각화
- **regime-aware correlation** — bull / bear / range 시장 별도 lookback
- **자동 collector** — symbol → sector / themes 매핑 자동화
- **RiskManager 통합** — RiskManager.evaluate_order 에서 본 가드 자동 호출
  (현재는 호출자가 별도로 호출)

---

## 12. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙
- [`docs/risk_manager_contract.md`](risk_manager_contract.md) — #34 RiskManager 단일 진입점
- [`docs/position_limit_policy.md`](position_limit_policy.md) — #35 PositionLimitRule (본 가드와 별개 layer)
- [`docs/order_guard_policy.md`](order_guard_policy.md) — #38 OrderGuard (본 가드와 별개 layer — 중복/쿨다운 전용)
- [`docs/order_executor_contract.md`](order_executor_contract.md) — #40 OrderExecutor 단일 진입점
- [`docs/watchlist_policy.md`](watchlist_policy.md) — #18 WatchlistItem.sector
- [`docs/theme_signal_policy.md`](theme_signal_policy.md) — #22 ThemeSignal.related_symbols
- [`docs/market_regime_filter.md`](market_regime_filter.md) — regime 분류 (후속 PR에서 regime-aware correlation에 연결)
- [`docs/alpha_decay_monitor.md`](alpha_decay_monitor.md) — #77 (전략 단위)
