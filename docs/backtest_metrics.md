# Backtest Metrics (체크리스트 #24)

## 1. 목적

단타 전략을 **승률만 보고 승인하면 안 된다**. 같은 승률이라도 손실 거래의 평균 크기, 연속 손실 길이, 특정 시간대의 손익 집중도가 다르면 실전 운용 가능성이 완전히 달라진다. 본 PR은 성과지표 계산을 `app/backtest/metrics.py` 독립 모듈로 분리하고, 기대값 / Profit Factor / MDD / Sharpe / 연속손실 / 시간대별 손익을 백테스트 결과 JSON에 포함시킨다.

`BacktestEngine` 자체는 변경하지 않으며 (#23 PR에서 이미 체결 모델 + 비용 반영 완료), `BacktestResult` properties가 metrics.py로 위임하도록 리팩토링한다 — 단일 진실, metric drift 위험 0.

## 2. 지표 정의

| 지표 | 의미 | 빈 입력 | 계산 불가 |
|---|---|---|---|
| `total_pnl` | 모든 거래 pnl 합 | 0 | — |
| `win_count` | `pnl > 0` 거래 수 | 0 | — |
| `loss_count` | `pnl <= 0` 거래 수 (legacy 의미: flat 포함) | 0 | — |
| `flat_count` | `pnl == 0` 거래 수 (#24 신규) | 0 | — |
| `win_rate` | win / 전체 거래 수 | 0.0 | — |
| `avg_win` | 이익 거래 평균 | 0.0 | — |
| `avg_loss` | 손실 거래 평균 (음수, flat 제외) | 0.0 | — |
| `expectancy` | `wr × avg_win + lr × avg_loss` | 0.0 | — |
| `profit_factor` | `gross profit / |gross loss|` | None | 손실 0건 → None |
| `max_drawdown` | 누적 PnL peak-to-trough 절대값 | 0 | — |
| `sharpe_ratio` | per-trade `mean / stdev` (연환산 X) | None | 거래 < 2 / stdev = 0 → None |
| `max_consecutive_wins` | 연속 이익 거래 최대 길이 | 0 | flat이 streak를 끊음 |
| `max_consecutive_losses` | 연속 손실 거래 최대 길이 | 0 | flat이 streak를 끊음 |
| `hourly_pnl` | exit_ts hour(UTC) 기준 손익 합. 미존재는 -1 키 | `{}` | — |
| `equity_curve` | `[{timestamp, equity}, ...]` 누적 손익 점들 | `[{None, initial_cash}]` | — |

`avg_loss` 정의 변동 — 기존 `BacktestResult.avg_loss`는 pnl≤0 평균(flat 포함)으로 lockstep 호환. 신규 `metrics.avg_loss`는 strict (pnl<0만). `expectancy` 계산은 strict 손실만 사용.

## 3. 기대값 계산

```
expectancy = win_rate × avg_win + loss_rate × avg_loss
           = (W/N) × avg_win + (L/N) × avg_loss
```

- `avg_loss`는 음수 유지 — 손실 비율이 클수록 expectancy를 끌어내린다.
- flat은 win/loss 어느 쪽에도 들지 않아 0 기여.
- 거래 0건 → 0.0.

해석:
- `expectancy > 0`: 평균적으로 이익. 단, 연속손실/MDD가 운용 가능 범위인지 별도 평가.
- `expectancy ≤ 0`: 평균적으로 손실 — 승격 평가 사용 금지.

## 4. Profit Factor 해석

| 값 | 해석 |
|---|---|
| `< 1.0` | gross 손실이 gross 이익을 초과 — **위험** |
| `1.0 ~ 1.2` | 손익균형 근처 — 비용/슬리피지 약간만 늘어도 적자 — **부족** |
| `1.2 ~ 1.5` | 최소 후보 — 다른 지표(MDD, expectancy, 연속손실) 함께 평가 필요 |
| `≥ 1.5` | 양호. 단, Walk-forward / out-of-sample 검증 필수 |
| `None` | 손실이 0건 — 표본이 작거나 승률 100% — 과신 금지, 표본 크기 확인 |

## 5. MDD 해석

- 누적 PnL 곡선의 peak에서 trough까지의 최대 낙폭(절대값).
- **단타 운영에서 MDD는 운영 자본 결정과 직결** — 일반적으로 `MDD × 2~3 배`의 자본 여유가 필요.
- 거래 표본이 작을수록 MDD 추정이 부정확. 100거래 미만이면 신뢰도 낮음.

## 6. Sharpe 해석

- 본 구현은 **per-trade 단순 Sharpe** (`mean(returns) / stdev(returns)`).
- **연환산 Sharpe가 아니다** — 봉 간격을 엔진이 모르므로 시간 단위 자체가 모호.
- 절대값보다 같은 데이터 구간의 다른 전략과 *비교*하는 데 유용.
- 거래 < 2 / 모든 return 동일 (stdev=0) → None.
- NaN / inf는 None으로 sanitize되어 JSON 응답이 항상 안전.

## 7. 연속손실 (max_consecutive_losses)

단타에서 가장 위험한 운용 신호. 5연속 손실이면 운영자 심리가 흔들리고 사이즈가 의도치 않게 변할 수 있음.

| 정책 | 권장 |
|---|---|
| **자동 중단** | `max_consecutive_losses ≥ 5`이면 운영자에게 alert. `auto_stop_consecutive_rejections` (RiskPolicy #182)와 결합 검토 |
| **사이즈 축소** | 3 연속 이후 quantity 축소 (별도 옵트인 PR) |
| **fold별 평가** | 25번 Walk-forward에서 fold별 max_consecutive_losses 모니터링 |

본 PR 단계에서는 **지표만 보고**한다. 자동 중단/사이즈 축소는 별도 PR.

## 8. 시간대별 손익 (hourly_pnl)

| key | 의미 |
|---|---|
| `0~23` | UTC hour. KST = UTC + 9, 정규장 09:00–15:30 KST = UTC 0:00–6:30 |
| `-1` | exit_ts 미존재 또는 파싱 실패 — 운영자가 인지하도록 별도 키 |

운영 활용:
- 특정 시간대에 손실이 집중되면 strategy의 시간대 필터링이 필요할 수 있음.
- 예: KST 14:30 (UTC 5:30) = 장 마감 30분 전. 변동성이 평소와 다를 수 있음.
- 표본이 작으면 (특정 시간대 거래 5건 미만) 통계적 의미 없음 — 운영자가 표본 크기 확인 필수.

향후 (Backlog) — 시간대 분포 mini chart, 정규장 시간대만 필터링 옵션.

## 9. 전략 승인 정책

`docs/promotion_policy.md`와 lockstep:

| 단계 | 요구 metric |
|---|---|
| `SIMULATION` → `PAPER` | `expectancy > 0`, `profit_factor ≥ 1.2`, `거래 ≥ 100`, `max_consecutive_losses ≤ 5` |
| `PAPER` → `LIVE_SHADOW` | 위 + 4주 PAPER 운영 데이터 + 시간대별 손익 분포 검토 |
| `LIVE_SHADOW` → `LIVE_MANUAL_APPROVAL` | 위 + walk-forward fold별 일관성 (#25 PR에서) |
| LIVE_AI_* | 본 시점 비활성 |

**금지 기준**:
- **승률만으로 승인 금지**. 80% 승률이라도 1번의 큰 손실로 expectancy < 0이면 거부.
- **특정 구간의 한 번 대박으로 승인 금지** — 표본 분포 확인 필수.
- **profit_factor가 None인 결과로 승인 금지** — 손실 표본이 없으면 통계적 의미 약함.
- **MDD가 운영 자본 대비 크면 거부** — 일반 기준: `MDD ≤ initial_cash × 0.15`.

## 10. API 응답 보강

`POST /api/backtest/run`, `POST /api/backtest/compare`, `GET /api/backtest/runs/{id}` 모두 응답에 신규 필드 추가 (거래 0건 시 안전 default):

```json
{
  // ... (기존 필드 그대로)
  "expectancy":            25.0,
  "flat_count":            1,
  "max_consecutive_wins":  3,
  "max_consecutive_losses": 2,
  "hourly_pnl":            { "9": 100, "15": -80 }
}
```

기존 호출자 호환성 — 모든 신규 필드는 default 값 (`0` / `0.0` / `{}`). frontend가 옛 응답을 파싱하더라도 신규 필드 부재 시 graceful.

## 11. 안전 invariant (본 PR이 지키는 것)

- broker / RiskManager / PermissionGate / OrderExecutor / `route_order` 변경 0건.
- `BacktestEngine` 코드 변경 0건 — types.py의 properties만 metrics.py로 위임.
- 기존 BacktestResult property 의미 유지 (특히 `loss_count`, `avg_loss`는 legacy 의미).
- 외부 네트워크 호출 0건.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 변경 0건.
- API Key / Secret / 계좌번호 변경 0건. frontend 시크릿 노출 0건.
- 응답 신규 필드는 모두 optional default — 옛 클라이언트 호환.
- NaN / inf는 None으로 sanitize되어 JSON `null` (응답 안전).

## 12. 후속 작업 (Backlog)

| 항목 | 트리거 |
|---|---|
| Walk-forward fold별 metric 분포 | 25번 PR |
| 연속손실 자동 중단 / 사이즈 축소 | LIVE 활성화 PR |
| 시간대별 손익 chart UI | 운영자 요청 누적 후 |
| 연환산 Sharpe (봉 간격 인자화) | 시간 단위 표준화 PR |
| Calmar / Sortino / Omega ratio | 운영 데이터 누적 후 |
| Strategy Scoreboard에 신규 metric 통합 | scoreboard 확장 PR |
| `data_quality` (#21)의 EXCLUDE 제외 + min_quality_score 옵션 | 별도 옵트인 PR |
| Equity curve를 frontend에 chart로 표시 | UI 요청 시 |
| BacktestRun DB에 신규 metric 별도 컬럼 | 운영자 분석 누적 후 (현재는 trades_json에서 재계산) |

## 관련 문서

- [`backtest_policy.md`](backtest_policy.md) — 체결 모델 + 비용 정책 (#23)
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 + metric 요건
- [`data_quality_report.md`](data_quality_report.md) — 백테스트 데이터 품질 (POOR/EXCLUDE 제외)
- [`risk_policy.md`](risk_policy.md), [`risk_guards_matrix.md`](risk_guards_matrix.md) — 가드 체인 (자동 중단 #182)
