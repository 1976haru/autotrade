# 3-02 — 실제 데이터 백테스트 runner

> 본 문서는 *연구 / 검증* 파이프라인 정의입니다. **투자 조언이 아닙니다.**
> 기본 모드는 `SIMULATION` / `PAPER` 이며, `ENABLE_LIVE_TRADING` /
> `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 는 default false 입니다.

## 1. 목적

`MockMarketData` 의 결정론적 합성 OHLCV 만으로는 *실 시장 성과* 를 판단할 수
없다. 본 3-02 단계는 *실제 / 준실제* OHLCV 데이터로 ``STRATEGY_REGISTRY`` 의
6 전략 baseline 성과 / 위험 지표를 한 번에 측정하고, 4단계 verdict 로 분류
하는 *실행 전용* 스크립트를 정의한다.

## 2. MockMarketData vs 실제 데이터

| 항목 | MockMarketData (`app.market.mock`) | 실제 데이터 (`load_real_ohlcv`) |
|---|---|---|
| 가격 패턴 | 결정론적 합성 (재현성 ↑) | 실 OHLCV — 갭 / 거래정지 / 거래량 편차 보존 |
| 거래량 | 합성 상수 / 단순 sin | 실 거래량 — liquidity 검증 가능 |
| 갭 / 거래정지 | 없음 (이상 케이스 미포함) | 발생 — INSUFFICIENT_DATA 로 분기 |
| 슬리피지 / 수수료 | 백테스트 시 plug-in 비용 모델 | 동일 (수수료 / 슬리피지 / 세금 모두 적용) |
| 사용 범위 | **시스템 동작 확인용** | **전략 수익성 판단** (3-02 ~ 3-07) |

본 3-02 결과는 *MockMarketData 가 아닌* 실제 데이터 기반이지만 여전히 *과거
데이터 통계* — 미래 성과 보장 아님.

## 3. 데이터 소스 우선순위

`app.backtest.real_data.loader.load_real_ohlcv`:

1. **로컬 CSV** — repo 표준 위치 우선 검색.
   - `data/ohlcv/{symbol}.csv`
   - `backend/tests/fixtures/real_data/{symbol}.csv`
2. **yfinance fallback** — `--enable-yfinance` 옵트인 시에만 시도.
   - read-only 과거 데이터 fetch 만. 주문 / 계좌 조회 0건.
   - 네트워크 / rate-limit / 파싱 실패 모두 *graceful* (예외 raise 0건).
3. **데이터 없음** — `LoadStatus.DISABLED` / `NO_DATA` / `FETCH_FAILED`.
   *mock 으로 silent swap 0건* — 해당 symbol skip + 사유 carry.

## 4. 1차 대상 종목 10종 (대표성 / 거래대금 위주)

| code | 한글명 | yfinance ticker | 시장 |
|---|---|---|---|
| 005930 | 삼성전자 | 005930.KS | KOSPI |
| 000660 | SK하이닉스 | 000660.KS | KOSPI |
| 035420 | NAVER | 035420.KS | KOSPI |
| 035720 | 카카오 | 035720.KS | KOSPI |
| 005380 | 현대차 | 005380.KS | KOSPI |
| 051910 | LG화학 | 051910.KS | KOSPI |
| 068270 | 셀트리온 | 068270.KS | KOSPI |
| 373220 | LG에너지솔루션 | 373220.KS | KOSPI |
| 105560 | KB금융 | 105560.KS | KOSPI |
| 055550 | 신한지주 | 055550.KS | KOSPI |

코드에서는 항상 6자리 ``symbol`` 로 처리하고, yfinance fetch 시점에만
``yahoo_ticker(symbol)`` 로 ``005930.KS`` 형식 변환.

**중요**: 본 10종은 *1차 검증용 샘플* 이다. **최종 운용은 전체 종목이 아니라
유동성 / 거래대금 / 시가총액 / 거래정지 / 관리종목 / 신규상장 필터를 통과한
종목** 만 사용한다. 본 카탈로그 확장은 후속 PR + 운영자 옵트인.

## 5. 6 전략

`STRATEGY_REGISTRY`: `sma_crossover` · `rsi_reversion` · `vwap_strategy` ·
`orb_vwap` · `volume_breakout` · `pullback_rebreak`. 본 PR 에서는 default
파라미터로만 실행. 파라미터 grid search 는 3-03 (별도 PR).

## 6. 4단계 verdict

`app.backtest.real_data.verdicts.BacktestVerdict`:

| Verdict | 조건 | 의미 |
|---|---|---|
| `INSUFFICIENT_DATA` | `trade_count < 10` | 거래 표본 부족 — 통계 의미 X |
| `HIGH_DRAWDOWN`     | `max_drawdown > 15%` | 위험 한도 초과 — 손실 방어 우선 |
| `LOW_QUALITY`       | `profit_factor < 1.10` | 품질 미달 |
| `BACKTEST_PASS`     | 위 3 가지 모두 통과 | 백테스트 기준 통과 |

우선순위: `INSUFFICIENT_DATA` > `HIGH_DRAWDOWN` > `LOW_QUALITY` > `BACKTEST_PASS`.

**`BACKTEST_PASS` 는 paper 후보 / 실거래 자격이 *아니다*.** 본 PR (3-02) 는
*분석 라벨* 까지만 — paper 후보 export 는 3-07 별도 PR + 운영자 검토.

## 7. CLI

```bash
# 1) repo CSV 만 (CI / 자동 테스트 안전).
python scripts/run_backtest_real_data.py

# 2) yfinance 옵트인 (네트워크 실패 graceful).
python scripts/run_backtest_real_data.py --enable-yfinance

# 3) 특정 symbol / strategy 만.
python scripts/run_backtest_real_data.py --symbol 005930 \
    --strategies sma_crossover rsi_reversion

# 4) dry-run — stdout 요약만, 파일 작성 X.
python scripts/run_backtest_real_data.py --dry-run

# 5) 비용 / 자본 / 임계값 override.
python scripts/run_backtest_real_data.py \
    --start 2025-01-01 --end 2026-05-01 \
    --initial-cash 10000000 --quantity 10 \
    --commission-bps 15 --tax-bps 23 --slippage-bps 5 \
    --min-trade-count 10 --min-profit-factor 1.10 --max-drawdown-pct 0.15
```

## 8. 산출물 (`reports/backtest_real/`, gitignore)

- `real_data_backtest_summary.json` — per_symbol × per_strategy 전체 결과.
- `real_data_backtest_ranking.csv`  — BACKTEST_PASS run 정렬.
- `real_data_backtest_report.md`    — 운영자 검토용 markdown 요약.

JSON 최상위 invariant (테스트로 lock):

- `is_order_signal:        false`
- `auto_apply_allowed:     false`
- `is_live_authorization:  false`

## 9. KIS read-only 시세 API (후속 옵션)

KIS API 는 *주문* 과 *시세 조회* 가 분리되어 있다. 본 3-02 시점에는:

- **KIS 주문 API 사용 금지** — `KisBrokerAdapter.place_order(is_paper=False)`
  는 영구 `NotImplementedError` (다층 안전 가드).
- **KIS read-only 시세 API** — 본 PR 미구현. 후속 옵트인 (`backend/app/market/
  kis_market_data.py` 같은 read-only adapter 추가) — 별도 PR + 운영자 명시
  승인.

본 PR 의 외부 데이터 소스는 **yfinance read-only fetch 만**. KIS 시세 API
어댑터는 *문서화만* (후속 PR 에서 추가).

## 10. 3-02 완료 기준

| 항목 | 본 PR 상태 |
|---|---|
| MockMarketData 아닌 실 데이터로 6 전략 한 번에 실행 가능 | ✓ `scripts/run_backtest_real_data.py` |
| CSV 우선 + yfinance fallback + 데이터 없음 graceful | ✓ `load_real_ohlcv` |
| 4단계 verdict 분류 | ✓ `classify_backtest_metrics` |
| JSON / CSV / Markdown 산출 | ✓ `_write_outputs` |
| broker / OrderExecutor / route_order / KIS 주문 0건 | ✓ 정적 grep 가드 + 테스트 lock |
| 안전 flag default 변경 0건 | ✓ `.env.example` / config default 미변경 |
| 테스트 — loader / pipeline / verdict / 정적 가드 | ✓ `test_real_market_data_loader.py` + `test_real_data_backtest.py` |

## 11. 3-03 (파라미터 최적화) 진입 조건

본 3-02 PR 머지 후 별도 PR 에서:

1. `BACKTEST_PASS` 또는 경계선 (`LOW_QUALITY` 근접) 전략 / 종목 조합 식별.
2. 전략별 *제한된* 파라미터 grid 정의 (search space 폭주 차단).
3. 동일한 4단계 verdict 분류기로 grid 결과 분류.
4. 모든 단계에서 broker / OrderExecutor / route_order 호출 0건 유지.

## 12. CLAUDE.md 절대 원칙

- broker / OrderExecutor / route_order import 0건 (정적 grep + 테스트 lock).
- KIS 주문 API 호출 0건. yfinance 는 read-only 시세 fetch 만.
- 실제 매수 / 매도 / Place Order 0건. 본 스크립트는 *분석 read-only*.
- 안전 flag default 변경 0건 — `KIS_IS_PAPER=true` / `ENABLE_LIVE_TRADING=false`
  / `ENABLE_AI_EXECUTION=false` / `ENABLE_FUTURES_LIVE_TRADING=false`.
- secret / API key / 계좌번호 / `.env` 노출 0건.
- `BACKTEST_PASS` 라벨은 *분석 라벨* — paper 운용 / 실거래 활성화 의미 X.
