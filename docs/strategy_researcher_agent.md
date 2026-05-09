# Strategy Researcher Agent (#55)

본 문서는 [`StrategyResearcherAgent`](../backend/app/agents/strategy_researcher.py)의 정책 contract를 정의한다. `BacktestRun` + 메트릭(#24) + walk-forward(#25) + Monte Carlo(#26) + data quality(#21) + strategy promotion gate(#27)를 read-only로 분석해 *전략 개선 후보*를 markdown 리포트로 *제안*하는 advisory Agent.

**본 Agent는 어떤 제안도 *자동으로 코드 / 파라미터에 반영하지 않는다*.** 모든 제안은 운영자 검토 → 별도 PR → 별도 백테스트 → walk-forward → paper / shadow → live 절차가 필요하다. 또한 **주문 신호를 만들지 않으며**, BUY / SELL / HOLD 같은 결정 값을 반환하지 않는다.

## 1. 목적

백테스트 결과의 *통계적 약점*을 운영자가 빠르게 파악하고, 어떤 *후속 검증*이 필요한지 명시적으로 안내한다. 단일 Agent가 "분석 + 적용"을 하지 못하도록 본 Agent는 *순수 분석 + advisory* 계층에 머문다 — 운영자 / 다른 Agent / RiskManager가 본 리포트를 보고 *수동* 결정.

## 2. 입력 데이터

본 Agent는 입력 metric을 *직접 계산하지 않으며* — caller(예: API endpoint)가 미리 계산한 `StrategyResearcherInput`을 전달한다. 이는 strategy_researcher 모듈이 strategies / external client에 의존하지 않게 하기 위함.

### `BacktestSummary` (필수)
- `BacktestRun` row + 메트릭 모듈(#24) `summarize_metrics()` 결과를 carry.
- 핵심 필드: `run_id`, `strategy`, `params`, `initial_cash`, `trade_count`, `win_rate`, `profit_factor`, `expectancy`, `max_drawdown`, `max_consecutive_losses`, `sharpe_ratio`, `hourly_pnl`, `data_symbol`, `data_interval`, `data_start`, `data_end`.

### `WalkForwardSummary` (선택, #25)
- `recommendation` (PASS/CAUTION/FAIL), `fold_count`, `positive_fold_ratio`, `single_best_fold_share`, `overfit_risk_score`, `holdout_pnl`, `warnings`.

### `MonteCarloSummary` (선택, #26)
- `risk_of_ruin` (0-1), `p05/p50/p95_total_pnl`, `worst_5pct_avg_mdd`, `longest_losing_streak`, `promotion_risk_flag`, `stability_grade`, `warnings`.

### `DataQualitySummary[]` (선택, #21)
- 종목별 data quality 결과 — `score` (0-100), `grade` (GOOD/WARNING/POOR/EXCLUDE/EMPTY), `missing_rate`, `coverage_score`, `notes`.

### `PromotionGateSummary` (선택, #27)
- `current_stage`, `target_stage`, `decision` (PASS/CAUTION/FAIL/BLOCKED), `failed_criteria`, `cautions`, `required_actions`.

### DB read-only helpers
```python
load_backtest_run(db, run_id) -> BacktestRun | None
load_recent_backtest_runs(db, *, strategy=None, limit=20) -> list[BacktestRun]
```
- 둘 다 read-only SELECT
- INSERT / UPDATE / DELETE 0건 (정적 grep 가드)
- caller가 본 함수로 row를 가져온 뒤 `summarize_metrics()`로 metric을 계산해 `BacktestSummary`에 채워 넣는다.

## 3. 출력 데이터 (`StrategyResearchReport`)

```python
@dataclass(frozen=True)
class StrategyResearchReport:
    audit_level:          ResearchSeverity        # HEALTHY/CAUTION/WARNING/CRITICAL
    findings:             tuple[StrategyFinding, ...]
    suggestions:          tuple[StrategySuggestion, ...]
    required_next_tests:  tuple[str, ...]         # 운영자 *수동* 실행 필수
    markdown_report:      str                      # 자연어 리포트
    summary_lines:        tuple[str, ...]
    strategy:             str
    run_id:               int
    auto_apply_allowed:   bool                     # *항상 False* (가드)
    is_order_signal:      bool                     # *항상 False* (가드)
    created_at:           datetime
```

`__post_init__`에서 `auto_apply_allowed=True` 또는 `is_order_signal=True`로 만들면 즉시 ValueError. 이는 *어떤 PR도 invariant를 우회하지 못하도록* 강제하는 기본 가드.

### `ResearchSeverity` (4단계, BUY/SELL/HOLD 0개)
| 값 | 의미 |
|---|---|
| `HEALTHY` | 모든 임계 통과 — 정기 재검증 권고 |
| `CAUTION` | 일부 임계 근접 — 모니터링 강화 |
| `WARNING` | 임계 위반 — 추가 검증 + 파라미터 재검토 권고 |
| `CRITICAL` | 다수 위반 / 자동승격 차단 — 운영자 수동 결정 필수 |

### `FindingCode` (19개)
LOW_TRADE_COUNT / LOW_PROFIT_FACTOR / NEGATIVE_EXPECTANCY / LOW_WIN_RATE / HIGH_MAX_DRAWDOWN / HIGH_CONSECUTIVE_LOSSES / HOURLY_PNL_IMBALANCE / WALK_FORWARD_FAIL / WALK_FORWARD_CAUTION / LOW_POSITIVE_FOLD_RATIO / SINGLE_FOLD_DOMINANCE / OVERFIT_RISK_HIGH / MONTE_CARLO_RUIN_HIGH / MONTE_CARLO_FAT_TAIL / DATA_QUALITY_POOR / DATA_QUALITY_WARNING / PROMOTION_BLOCKED / PROMOTION_FAILED / INSUFFICIENT_HOLDOUT.

### `SuggestionCategory` (10개, advisory only)
| 값 | 의미 |
|---|---|
| `PARAMETER_TUNE` | 파라미터 후보 (예: SMA window 늘리기) |
| `RISK_TIGHTEN` | 손절 / 포지션 한도 강화 |
| `TIMEFRAME_FILTER` | 손익 편향 시간대 회피 |
| `DATA_QUALITY` | 데이터 quality 개선 후 재실행 |
| `OVERFIT_GUARD` | walk-forward / holdout 강화 |
| `SHRINK_SIZE` | quantity / notional 축소 |
| `ADD_FILTER` | 신규 진입 조건 추가 (theme / regime) |
| `RE_RUN_TEST` | 단순 재실행 (데이터 갱신 후 등) |
| `PROMOTION_BLOCK` | 현재 stage 승격 보류 |
| `SHADOW_VALIDATE` | 변경 전 shadow 운용 권고 |

### `markdown_report` 구조
1. 자동 반영 안 됨 / PR 검토 필요 disclaimer
2. 분석 대상 (strategy, run_id, period, params)
3. 핵심 metric 테이블 (Verdict 컬럼)
4. Findings 목록 (severity별)
5. 개선 제안 목록 (rationale + proposed_change + required_validation 체크박스)
6. Required Next Tests (운영자 *수동* 실행)
7. 한계 (반드시 검증 필요)

## 4. 안전 원칙 (절대 invariant)

| 원칙 | 가드 |
|---|---|
| **자동 반영 0건** | `auto_apply_allowed=False` 불변 (`__post_init__` ValueError) |
| 주문 신호 아님 | `is_order_signal=False` 불변 |
| BUY/SELL/HOLD 반환 금지 | `ResearchSeverity` / `FindingCode` / `SuggestionCategory` enum에 해당 값 0개 |
| approval queue 직접 등록 금지 | `submit_candidate(` / `route_order(` 호출 0건 (정적 grep 가드) |
| broker / OrderExecutor / route_order 호출 금지 | 정적 grep 가드 |
| **strategy 코드 / 파라미터 mutation 금지** | `app.strategies.*` import 0건, `.save_params(` / `.apply_params(` / `.update_params(` / `policy.max_*= ` 0건 (정적 grep 가드) |
| emergency_stop 토글 금지 | `set_emergency_stop(` / `emergency_stop = True` 호출 0건 |
| DB INSERT/UPDATE/DELETE 금지 | 정적 grep 가드 (read-only SELECT only) |
| 외부 HTTP / AI 호출 금지 | httpx / requests / urllib3 / anthropic / openai import 0건 |
| 제안 텍스트 advisory 톤 강제 | "자동으로 적용" / "지금 변경" / "코드를 수정하라" 금지 — 정적 grep 가드 |

## 5. PR / 승인 절차

본 Agent의 어떤 제안도 자동으로 적용되지 않는다. 운영자가 적용을 검토하려면:

1. 본 리포트의 `suggestions[]`를 검토하고 적용할 후보 1건을 선택.
2. 별도 *git branch*를 만들고 strategy 코드 / 파라미터를 *수동* 변경.
3. 새 BacktestRun을 *수동* 실행 (`POST /api/backtest/run`).
4. walk-forward 재검증 (#25, `POST /api/backtest/walk-forward`).
5. Monte Carlo 재측정 (#26, ROR < 5% 권고).
6. data quality 재확인 (#21).
7. PR 생성 → 운영자 리뷰 → 머지.
8. paper / shadow 운용으로 실시간 검증.
9. promotion gate(#27) 통과 후에만 LIVE 활성화.

각 단계는 코드 단에서 강제됨 (StrategyPromotionGate가 final backstop).

## 6. 제안 기준 (임계값 표)

| Metric | HEALTHY 임계 | CAUTION 임계 | 출처 |
|---|---|---|---|
| 트레이드 수 | ≥ 100 | ≥ 30 | promotion_gate |
| Profit Factor | ≥ 1.20 | ≥ 1.00 | promotion_gate |
| Expectancy | > 0 | > 0 | metrics |
| Win Rate | (정보용) | ≥ 35% | 본 Agent |
| Max Drawdown / initial | ≤ 15% | ≤ 25% | promotion_gate |
| 연속 손실 최대 | ≤ 5 | ≤ 8 | promotion_gate |
| 시간대 dominance | < 50% | < 50% | 본 Agent |
| Walk-forward recommendation | PASS | CAUTION | walk_forward_runner |
| Positive fold ratio | ≥ 0.60 | (정보용) | walk_forward_runner |
| Single fold share | ≤ 0.70 | (정보용) | walk_forward_runner |
| Overfit risk | ≤ 0.50 | (정보용) | walk_forward_runner |
| Risk of Ruin | < 5% | < 10% | monte_carlo |
| Data quality score | ≥ 75 | ≥ 60 | data_quality |
| Promotion gate decision | PASS | CAUTION | strategy_promotion |

본 임계값은 promotion_gate(#27)와 일치 — 운영자가 임계를 *낮춰서* 통과시키는 것이 아니라 *전략을 개선*해 임계를 *통과해야* 한다.

## 7. 한계

본 Agent의 출력을 단독 결정 근거로 쓰면 안 되는 이유:

| 위험 | 영향 |
|---|---|
| 표본 외 일반화 | 백테스트는 *과거 데이터* — 미래에 다르게 작동 가능 |
| 슬리피지 / 부분체결 | 백테스트 모델은 실 시장 미세 구조를 *완전히 반영하지 못함* |
| Regime change | 추세 / 변동성 / 거래대금 / 거시 환경 변화 시 전략 무력화 |
| Data leakage | walk-forward로 일부 완화하나 *완전한 보장 X* |
| AI / 규칙 기반 제안 오류 | 도메인 지식 / 인과관계 검증 필수 |
| 유의성 vs 효과 | 통계적으로 유의해도 효과 크기가 작으면 운영 가치 X |

본 Agent는 위 위험을 *완화하지 않는다* — 단지 운영자에게 *원본 데이터의 통계 요약 + 개선 후보*를 보여줄 뿐이다. 매매 결정 / 전략 변경은 운영자 + 별도 PR + 별도 검증 흐름에서 이루어져야 한다.

## 8. Agent 관계

| Agent | 본 StrategyResearcher 사용 패턴 |
|---|---|
| **MarketObserverAgent** (#52) | regime이 STRESS일 때 본 리포트의 추가 필터 제안을 운영자가 가중 검토 |
| **NewsTrendAgent** (#53) | 후보 종목과 본 리포트의 universe 비교 — *수동* |
| **RiskAuditorAgent** (#54) | 장중 위험 + 본 리포트의 historical 위험 = 종합 안전 그림 |
| **ChiefTradingAgent** | 본 리포트는 *전략 개선* 전용 — chief는 이를 운영자 dashboard에 노출 |
| **ExecutionRecommender** (#51) | 본 리포트와 무관 — chief / executor는 본 Agent의 입력으로 사용하지 않음 |

## 9. API surface

| Endpoint | 메서드 | 의미 |
|---|---|---|
| `/api/agents/strategy-researcher/recent` | GET | 최근 BacktestRun 목록 + audit_level 미리보기 |
| `/api/agents/strategy-researcher/report/{run_id}` | GET | 단일 BacktestRun을 분석해 markdown advisory report 반환 |
| `/api/agents/strategy-researcher/mock` | POST | deterministic mock — 운영자가 외부 metric을 직접 주입 (테스트 / Demo) |

세 endpoint 모두 broker 호출 0건, audit row 0건, DB write 0건.

## 10. UI

[`frontend/src/components/tabs/StrategyResearcherCard.jsx`](../frontend/src/components/tabs/StrategyResearcherCard.jsx) — Backtest / Agent 탭에 마운트.

**필수 표시**:
- "자동 반영 안 됨 · PR 검토 필요" 배지
- 운영자 요약 3줄
- audit_level 색상 (HEALTHY/CAUTION/WARNING/CRITICAL)
- 핵심 findings (severity별 색상)
- 개선 제안 (rationale + proposed_change + required_validation 체크박스)
- Required Next Tests 박스
- markdown 미리보기 토글
- "본 리포트는 *주문 신호가 아니며 자동으로 코드 / 파라미터에 반영되지 않습니다*" disclaimer

**금지된 UI 요소** (테스트로 lock):
- BUY / SELL / HOLD 버튼
- 매수 / 매도 CTA
- "자동 반영" / "자동 적용" / "파라미터 저장" / "코드 수정" / "지금 적용" 버튼
- `Apply parameter` / `Apply change` / `Apply config` 영문 버튼

**허용된 후속 행동**:
- "Backtest 다시 실행" 버튼 (별도 backtest run 시작 — 새 backtest_run 생성하지만, 기존 코드/파라미터를 *변경하지 않음*)
- "새로고침" (re-fetch only)
- "markdown 미리보기" 토글

## 11. 변경 시 동기화

- 새 `FindingCode` / `SuggestionCategory` 추가 → 본 문서 §3 + `_check_*` 함수 + 테스트
- 임계값 조정 → 본 문서 §6 + 테스트 boundary 갱신 + promotion_gate(#27)와의 일관성 확인
- 새 입력 metric (예: live trading PnL) → `StrategyResearcherInput` + Pydantic schema + 본 문서 §2
- **자동 반영 invariant 변경 금지** — `auto_apply_allowed=True`는 절대 허용 X. 자동 적용이 필요하면 *별도 모듈*로 분리하고 별도 옵트인 PR.

## 관련 문서

- [`agent_architecture.md`](agent_architecture.md) — 6개 표준 Agent 역할 contract (#51)
- [`agent_design.md`](agent_design.md) — Agent 분리 정책
- [`market_observer_agent.md`](market_observer_agent.md) — 시장 환경 snapshot (#52)
- [`news_trend_agent.md`](news_trend_agent.md) — News/Trend Agent (#53)
- [`risk_auditor_agent.md`](risk_auditor_agent.md) — 장중 리스크 감독 (#54)
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 + 환경 플래그
- `app/agents/strategy_researcher.py` — 본 Agent 구현
- `app/backtest/metrics.py` — 메트릭 모듈 (#24)
- `app/backtest/walk_forward_runner.py` — walk-forward (#25)
- `app/backtest/monte_carlo.py` — Monte Carlo (#26)
- `app/governance/strategy_promotion.py` — promotion gate (#27)
- `app/market/data_quality.py` — data quality (#21)
- `CLAUDE.md` — 절대 원칙 1번 (AI 직접 호출 금지) + 본 Agent의 자동 반영 금지 정책
