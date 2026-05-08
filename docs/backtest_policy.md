# Backtest Policy (체크리스트 #23)

## 1. 목적

실제보다 과장된 수익률을 만들지 않는 백테스트 — **신호 봉 종가 단순 체결을 기본에서 제거**하고, 보수적 체결 모델 + 수수료 + 거래세 + 슬리피지를 반영한다. 본 PR은 backtest engine / 체결 모델 / 비용 모델 / API 응답 / 문서 / 테스트만 변경하며, 주문 / 리스크 / PermissionGate / OrderExecutor 분기는 건드리지 않는다 (CLAUDE.md 절대 원칙).

## 2. 체결가 정책 (`BacktestConfig.execution_model`)

| 모델 | 의미 | 기본 체결 시점 | 위험 |
|---|---|---|---|
| `same_close` | 신호가 발생한 봉의 close에 즉시 체결 | 같은 봉 (`allow_same_bar_execution=True` 강제) | **과대평가 위험 高** — 신호 시점에 정확히 close에 체결되는 건 비현실 |
| `next_open` | 신호 봉의 다음 봉 open에 체결 | i+1 봉 | 표준. 권장 기본값 |
| `next_close` | 신호 봉의 다음 봉 close에 체결 | i+1 봉 | 보수적이지만 '하루 보유' 색채 |
| `conservative` | BUY=`max(open, close)`, SELL=`min(open, close)` | i+1 봉 | 가장 보수적. 과적합 검증용 |

마지막 봉에서 신호가 나서 execution bar가 없으면 **체결 안 함**. 잔여 포지션은 `exit_on_last_bar=True`(기본) 시 마지막 봉 close에 강제 청산 (signal_price와 raw price 모두 close).

`execution_delay_bars`: 기본 1. 0으로 설정 + `allow_same_bar_execution=True`이면 same_close와 동치.

## 3. 비용 모델

bps = basis points (1bps = 0.01%).

| 항목 | 적용 | 식 |
|---|---|---|
| **slippage** | BUY는 + 방향, SELL은 − 방향 | `BUY: price × (1 + bps/10000)` / `SELL: price × (1 − bps/10000)` |
| **commission** | BUY/SELL 양쪽 notional | `notional × bps/10000` |
| **tax** | SELL notional만 (한국 거래세 가정) | `sell_notional × bps/10000` |

`net_pnl = gross_pnl − fees − taxes − slippage_cost`. `gross_pnl`은 signal 가격 기준 (slippage 미반영).

본 PR 기본값은 모두 0 — config 미제공 시 legacy(same_close, 비용 0) 동작 유지. **운영자가 명시적으로 설정해야 비용이 반영**된다.

## 4. 기본 권장값

| 운용 단계 | 권장 config |
|---|---|
| MVP / 단순 비교 | (config 미제공) — legacy 동작 |
| 전략 1차 검증 | `next_open` + delay 1 + slippage 5 + commission 5 + tax 23 |
| 전략 승격 평가 | `next_open` 또는 `conservative` + slippage ≥ 5bps + commission ≥ 5bps + tax 23bps |
| 운영 전 최종 점검 | `conservative` + slippage 10bps |

`tax_bps=23` (0.23%)은 한국 주식 거래세 기준. 이는 추정치이며 ETF / 종목 / 시점에 따라 달라질 수 있어 정확한 값은 운영자 책임.

## 5. `same_close`의 위험

- 단일 봉 안에서 신호와 체결이 같은 봉의 close 한 점에 일어난다는 가정은 비현실적.
- 결과적으로 strategy의 진짜 알파가 아닌 *신호 발생 직후 close까지의 가격 움직임*을 수익으로 흡수한다 (look-ahead bias의 일종).
- **승격 평가에서는 same_close 단독 결과 사용 금지.** 비교 / 디버깅 목적으로만.

## 6. Trade / Result 신규 필드

`Trade`에 옵션 필드 추가 (config 미제공 시 None/0):
- `entry_signal_price`, `exit_signal_price` — 신호 시점 reference 가격
- `fees`, `taxes`, `slippage_cost` — 거래당 비용 분해

`BacktestResult` 새 properties (config 미제공 시 모두 0 또는 gross == net):
- `gross_pnl`, `net_pnl`, `total_fees`, `total_taxes`, `total_slippage`

기존 `pnl` 필드는 **항상 net_pnl과 동일** — 호환성 유지.

## 7. API

`POST /api/backtest/run`, `POST /api/backtest/compare` 모두 optional `config` 블록을 받는다:

```json
{
  "strategy": "sma_crossover", "params": {...},
  "initial_cash": 10000000, "quantity": 10,
  "bars": [...],
  "config": {
    "execution_model": "next_open",
    "execution_delay_bars": 1,
    "slippage_bps": 5,
    "commission_bps": 5,
    "tax_bps": 23,
    "exit_on_last_bar": true
  }
}
```

응답에 `gross_pnl / net_pnl / total_fees / total_taxes / total_slippage / config` 필드가 추가됨. **config 미제공 시 모두 0**, 기존 호출자 호환성 유지.

`config.execution_model`이 알 수 없는 값이면 400 Bad Request (`BacktestConfig.__post_init__`에서 검증).

## 8. 실전 승격 기준

`docs/promotion_policy.md`와 lockstep — 비용 미반영 백테스트 결과는 **승격 근거로 사용 금지**:

| 단계 | 데이터 / 비용 요건 |
|---|---|
| `SIMULATION` → `PAPER` | 비용 반영 백테스트 + 데이터 품질 GOOD/WARNING (#21) |
| `PAPER` → `LIVE_SHADOW` | 비용 반영 + min 4주 PAPER 운영 데이터 + same_close 단독 결과 미사용 |
| `LIVE_SHADOW` → `LIVE_MANUAL_APPROVAL` | conservative 또는 next_open + slippage ≥ 5bps 결과만 |
| `LIVE_*` 이후 | 본 시점 비활성 |

추가 invariant — **승률만으로 승인 금지**. 특정 구간 한 번의 대박으로 승인 금지 (walk-forward fold별 평가 필요 — #20+ 항목).

## 9. 한계 (현재 단계)

- **부분 체결 미지원** — 한 신호당 quantity 전량이 체결되거나 거부된다.
- **호가 (orderbook depth) 미반영** — 슬리피지는 단순 bps 비율로 추정.
- **시장 충격 (market impact) 미모델링** — 대량 주문이 호가창을 흔드는 효과는 별도 PR에서 검토.
- **체결 지연** — `execution_delay_bars`로 봉 단위 지연만 표현. 같은 봉 안의 ms 단위 지연은 본 봉 단위 모델로 표현 불가.
- **세율은 운영자 책임** — `tax_bps=23`은 일반 주식 가정. ETF/ELW/배당주는 다름.
- **실제 호가/체결 지연 검증** — Paper / LIVE_SHADOW 단계에서 추가 검증 필요.

## 10. 후속 작업 (Backlog)

| 항목 | 트리거 |
|---|---|
| 부분 체결 모델 | 운영 데이터로 필요성 확정 후 |
| Volume-aware 슬리피지 (호가 깊이 추정) | 호가 데이터 통합 (Phase 2) |
| 시장 충격 모델 | 대량 주문 운영 단계 |
| `BacktestRequest.min_quality_score` (data quality #21와 연동) | 24번 Metrics PR |
| Walk-forward runner의 fold별 비용 reporting | 24번 |
| Paper 결과와 백테스트 결과의 자동 비교 (slippage 검증) | LIVE 활성화 PR |
| Frontend BacktestConfig UI (현재는 raw JSON 입력 필요) | UI 요청 시 |
| BacktestRun DB에 비용 메타 separate 컬럼 (현재는 trades_json에만) | 운영자 분석 요구 누적 시 |

## 11. 안전 invariant (본 PR이 지키는 것)

- `BacktestEngine` legacy 경로 (`config=None`)는 기존 동작 그대로 — 회귀 0건.
- broker / RiskManager / PermissionGate / OrderExecutor / `route_order` 변경 0건.
- `app/backtest/engine.py` / `types.py` / `routes_backtest.py` 외 코드 변경 0건.
- 외부 네트워크 호출 0건.
- `ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_FUTURES_LIVE_TRADING` 변경 0건.
- API Key / Secret / 계좌번호 변경 0건. frontend 시크릿 노출 0건.
- `/api/backtest/run` `/compare` 기존 호출(config 미제공)은 응답 shape 호환 — 새 필드는 0 default.
- `BacktestRun.trades_json`의 새 키(entry_signal_price 등)는 backwards-compat: 옛 row를 읽을 때 `.get(... , None/0)`로 처리.

## 관련 문서

- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 + 비용 반영 요구
- [`data_quality_report.md`](data_quality_report.md) — 백테스트 데이터 품질 (POOR/EXCLUDE 제외 정책)
- [`market_data_collector.md`](market_data_collector.md) — bar cache + 누락률
- [`risk_policy.md`](risk_policy.md) — RiskManager 가드 (비용 모델과 직접 무관)
- [`broker_selection.md`](broker_selection.md) — adapter 별 실측 슬리피지 (Phase 2)
