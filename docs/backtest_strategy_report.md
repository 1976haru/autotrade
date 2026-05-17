# 최소 6개 전략 baseline 백테스트

> ⚠ **본 문서와 산출물은 *투자 조언이 아니라* 자동매매 시스템 운영·검증·개선
> 자료입니다.** 모든 결과는 결정론적 합성 OHLCV (`MockMarketData`) 기반이며
> *실 시장 성과 아님*. 실거래 적용 전 실 데이터 + walk-forward + paper /
> shadow 검증이 별도로 필요합니다.

## 1. 목적

본 절차는 현재 코드베이스에 등록된 6개 단타 전략의 baseline 성과를 *동일한
지표 매트릭스* 로 비교·기록한다. 다음 단계인 “나. 전략별 파라미터 최적화” 가
1차 후보를 정렬할 때 본 산출물을 입력으로 사용한다.

본 절차는 **백테스트 전용** — broker / OrderExecutor / route_order / KIS 실
계좌 / Anthropic / Telegram API 어떤 것도 호출하지 않는다.

## 2. 대상 전략 6종

| ID | 클래스 | required_regime | risk_profile (요약) |
|----|--------|-----------------|---------------------|
| `sma_crossover` | `SmaCrossoverStrategy` | trending | SMA 5/20 교차 |
| `rsi_reversion` | `RsiReversionStrategy` | ranging | RSI 14 / 30·70 |
| `vwap_strategy` | `VWAPStrategy` | trending_up | VWAP 회귀/돌파 |
| `orb_vwap` | `OrbVwapStrategy` | trending_up | 시초 ORB + VWAP 필터 |
| `volume_breakout` | `VolumeBreakoutStrategy` | trending_up | 거래량 돌파 |
| `pullback_rebreak` | `PullbackRebreakStrategy` | trending_up | 눌림목 재돌파 |

본 6개 외 *어떤 전략도 추가하지 않는다* (`docs/system_audit_2026_05.md` 의
invariant).

## 3. 실행 방법

```bash
# 기본 실행 — symbol=005930, 기간 2026-01-01 ~ 2026-06-30,
# 초기 자본 10,000,000 KRW, 1주문 수량 10주, 기본 수수료 / 슬리피지 반영.
python scripts/run_backtest_all_strategies.py

# 옵션 — 다른 심볼 / 기간 / 비용 모델
python scripts/run_backtest_all_strategies.py \
    --symbol 005930 \
    --start 2026-01-01 --end 2027-12-31 \
    --initial-cash 10000000 \
    --quantity 10 \
    --commission-bps 15 \
    --tax-bps 23 \
    --slippage-bps 5 \
    --output-dir reports/backtest

# 부분 실행 — 일부 전략만
python scripts/run_backtest_all_strategies.py \
    --strategies sma_crossover rsi_reversion

# dry-run — 파일 작성 X, stdout 에 run_meta 만
python scripts/run_backtest_all_strategies.py --dry-run
```

## 4. 산출물

`reports/backtest/` 하위에 3개 파일이 생성된다 (디렉토리는 `.gitignore` 로
git 커밋이 차단됨):

- **`strategy_backtest_summary.json`** — 전체 지표 풀세트 + run_meta +
  `ranking` 키.
- **`strategy_backtest_ranking.csv`** — `risk_adjusted_score` 내림차순 +
  필수 12 지표. Excel / pandas 로 즉시 분석 가능.
- **`strategy_backtest_report.md`** — 운영자 검토용 markdown. 순위 표 +
  비용 영향 표 + 안전 / 무결성 / 다음 단계 안내.

## 5. 필수 지표 (12종)

| 지표 | 의미 |
|------|------|
| `total_return` | 비용 반영 후 누적 수익률 (% as float). |
| `annualized_return` | `(1+r)^(1/years) - 1` — 1년 미만 / 손실 100%↑ 면 `null`. |
| `win_rate` | 승률 (`win_count / trade_count`). |
| `trade_count` | 체결 거래 수. |
| `profit_factor` | `Σwin / |Σloss|` — 손실 0 이면 `null`. |
| `expectancy` | `win_rate × avg_win + loss_rate × avg_loss`. |
| `max_drawdown` | 누적 PnL 의 peak-to-trough 낙폭 (KRW). |
| `avg_trade_pnl` | 거래당 평균 PnL (KRW). |
| `loss_streak` | 최대 연속 손실 거래 수. |
| `sharpe_like_score` | 거래 PnL 표준편차 기반 sharpe-ish — 거래 수 부족 시 `null`. |
| `fee_adjusted_return` | raw — 수수료 — 거래세. **슬리피지 제외**. |
| `slippage_adjusted_return` | raw — 수수료 — 거래세 — 슬리피지. **전체 비용 반영**. |

추가로 `risk_adjusted_score` (= `expectancy / max_drawdown`, fallback ranking
key) 와 비용 분해 (`fees`, `taxes`, `slippage_cost`) 도 carry.

## 6. 수수료 / 슬리피지 기본값

| 항목 | bps | % |
|------|-----|---|
| `commission_bps` | 15 | 0.15% (BUY + SELL 양쪽) |
| `tax_bps` | 23 | 0.23% (SELL 측만, 한국 거래세 가정) |
| `slippage_bps` | 5 | 0.05% (호가 갭 / 체결 지연 보수 가정) |

기본값은 *보수적 추정* 이며 운영자의 실 증권사 / 계좌에 따라 달라진다. CLI
인자로 override 가능. **본 값을 실 비용 보장으로 사용하지 말 것** — 실거래
진입 전 별도 reconciliation 필요.

## 7. 체결 모델

본 runner 는 `next_open` + `execution_delay_bars=1` 을 사용한다:

- 신호 봉 → 다음 봉 *open* 에 체결.
- 마지막 봉에서 신호가 나오면 체결 불가 (다음 봉이 없음).
- 미청산 포지션은 마지막 봉 *close* 에 강제 청산.

`same_close` 는 promotion 평가 금지 (`BacktestConfig` docstring 참조).

## 8. 데이터 소스

본 PR 시점에는 `MockMarketData` 의 *결정론적 합성 OHLCV* 만 사용한다. 같은
`(symbol, start, end)` 입력은 항상 같은 결과를 낸다 — 회귀 테스트 / 비교가
재현 가능.

후속 단계에서 실 데이터 (`yfinance` / `KIS` adapter) 로 재실행할 때는
*반드시* 본 README 의 "데이터 소스" 항목을 갱신한다.

**중요**: 일부 전략 (예: `volume_breakout` / `orb_vwap` / `pullback_rebreak` /
`vwap_strategy`) 은 `MockMarketData` 의 매끄러운 합성 봉에서 진입 조건이
거의 트리거되지 않아 baseline 거래 수가 0 으로 보고될 수 있다. 이는 *데이터
한계* 이며 코드 결함이 아니다 — 실 데이터로 재실행하면 거래가 발생한다.

## 9. 절대 원칙 (CLAUDE.md 매핑)

| 원칙 | 강제 방식 |
|------|-----------|
| 실거래 활성화 금지 | `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 변경 0건 (runner 정적 grep 가드 + pytest invariant). |
| `KIS_IS_PAPER=true` 유지 | 동일 — runner 가 환경변수 mutate 0건. |
| broker / OrderExecutor / route_order 호출 0건 | `test_script_does_not_call_real_order_functions` AST 검사 + import grep. |
| Secret 노출 0건 | `test_script_does_not_print_secrets` (산출 파일 secret-shape 패턴 검사). |
| `.env` 수정 금지 | `test_script_does_not_write_env_files` 정적 grep. |
| AI API / HTTP 호출 금지 | `import anthropic` / `openai` / `httpx` / `requests` 0건 (정적 grep). |

## 10. 테스트

```bash
# baseline runner 테스트 (18 case)
python -m pytest backend/tests/test_backtest_all_strategies.py -q

# secret 누출 / hygiene
python scripts/security_scan.py
python -m pytest backend/tests/test_repository_hygiene.py -q
```

## 11. 다음 단계 (참고 — 본 PR 의 범위 밖)

- 나. **전략별 파라미터 최적화**: 본 ranking 상위 후보부터 grid / random search.
- **walk-forward**: 단일 기간 결과의 over-fit 여부 검증 (#25 walk_forward_runner).
- **Monte Carlo**: 거래 순열 sampling 으로 신뢰 구간 추정 (#26 monte_carlo).
- **실 데이터 backtest**: `MarketDataAdapter` 의 yfinance / KIS adapter 로 재실행.
- **promotion gate** 재진입: backtest 결과를 promotion_policy.md 기준으로 평가.

본 baseline 은 *비교 가능한 출발점* — 단일 절대 성과로 해석하지 않는다.
