# 실제/준실제 데이터 기반 전략 가능성 검증 (REAL-DATA-STRATEGY-01)

> 사용자의 핵심 질문 — **"실제 데이터 기준으로 내 매매기법 + Agent 결합 전략이 가능성 있는가?"**
> 에 답하기 위해, sample fixture 가 *아니라* 실제/준실제 OHLCV 로 전략을 검증한다.
> **자동 적용 / 실전 전환 승인 / 주문 신호가 아니다. 수익을 보장하지 않는다.**

## 1. 목적

실제/준실제 OHLCV(CSV / yfinance) 로 4전략(ORB·Momentum·Gap·VWAP) + Agent Council 을
백테스트 / Walk-forward / Stress 로 검증하고, STRATEGY-VALIDATION-01 종합 평가기를 재사용해
5단계(STRONG / CAUTIOUS / RESEARCH_ONLY / NOT_READY / BLOCKED) 로 판정한다.

## 2. KIS 현재가 API 와 historical 검증의 차이

현재 KIS Client 는 **현재가(get_price) / 잔고(inquire_balance) / 당일체결(inquire_daily_ccld)**
만 제공하며 **과거 일봉/분봉(historical candle) API 는 미구현** 이다
(`kis_historical_supported() == False`). 백테스트는 과거 OHLCV 가 필요하므로:

- **현재가만으로는 백테스트가 불가능** 하다 (시계열 누적이 아니라 단일 스냅샷).
- 따라서 데이터 소스는 **CSV(권장) / yfinance(옵션)** 를 사용한다.
- KIS 주문 API(place_order)는 본 작업에서 **절대 호출하지 않는다** — 검증 전용.

## 3. CSV 데이터 준비 방법

```
timestamp,open,high,low,close,volume          # 필수
# 선택: vwap,market_regime,time_phase,symbol
2025-01-02T09:00:00,70000,70800,69800,70600,8500000
```
- 단일 종목: `--input-csv path.csv`
- 다종목: `--input-dir dir/` (파일명 `{symbol}.csv`)
- 심볼 + yfinance(준실제, 네트워크 필요): `--symbols 005930,000660 --allow-yfinance`

데이터 무결성: `low ≤ open,close ≤ high` 를 위반하면 품질 FAIL → BLOCKED. (예: 저장소
fixture `005930.csv` 는 일부 행에서 `open > high` 인 잘못된 OHLC 가 있어 FAIL 로 검출된다.)

## 4~5. 권장 데이터 기간 / 종목 수 / 평가 기준

| 항목 | 권장 |
|---|---|
| 최소 거래 수 | 100 이상 |
| 최소 거래일 | 28일 이상 |
| 종목 수 | 5개 이상 |
| 시장 상태 | 상승/하락/횡보 등 다양 포함 |
| 비용 | 수수료/슬리피지 반영(백테스트 엔진) |

성과: win_rate ≥ 50% · payoff ≥ 1.0 · profit_factor ≥ 1.3 · expectancy > 0 ·
MDD ≤ 10% · max_consecutive_losses ≤ 5.
Walk-forward: stability ≥ 0.65 · overfit_suspected=false · collapse 0/경미.
Agent: best single 대비 우위 또는 리스크 축소. AGENT_UNDERPERFORMS 면 CAUTIOUS 이상 금지.
Stress: FAIL 0 (MARKET_CRASH BUY 금지 / PRICE_STALE 주문 금지 / REJECTED 폭주 금지 /
PORTFOLIO_DRIFT critical 0).

## 6~11. 결과 해석

- **Backtest**: council(또는 best single) 승률/PF/expectancy/MDD/연속손실.
- **Walk-forward**: train→validation/test 유지율, overfit 의심, 성과 붕괴 구간.
- **Stress**: 12 악조건에서 기존 안전 가드가 보호하는지 (FAIL = 보호 실패).
- **Agent vs 단일전략**: `agent_value_verdict` (ADDS_VALUE / RISK_REDUCTION_VALUE /
  UNDERPERFORMS / INSUFFICIENT_SAMPLE) — "Agent 가 단일 전략보다 나은가".
- **시장국면/시간대**: `favorable_conditions` / `dangerous_conditions` (백테스트 regime/phase
  win_rate 버킷에서 유도).
- **사용자 매매기법 강점/약점**: `strategy_strengths` / `strategy_weaknesses` /
  `tuning_candidates`(feedback 태그 기반, **자동 적용 금지**).

## 12. sample fixture 한계

`backend/tests/fixtures/backtest/sample_ohlcv.csv` 는 합성 데이터 →
`sample_fixture_only=True` → **최대 RESEARCH_ONLY** 로 cap. quasi-real fixture
(`demo_quasi_real.csv`)는 무결한 일봉이지만 **사용자 실제 데이터가 아니며**, Paper 표본이
없으므로 역시 STRONG 에 도달하지 못한다.

## 13. Paper 100건/28일 기준

실데이터 백테스트가 좋아도 **Paper 모의 100건 + 28거래일** 표본이 없으면 실전 검토 불가
(`paper_sample_class=PAPER_NO_TRADES_YET`). STRONG 은 실데이터 + Paper Gate + WF 안정 +
Stress FAIL 0 + Agent 우위를 *모두* 충족해야 한다.

## verdict cap (실데이터 특화 보수 제한)

- 데이터 품질 FAIL → **BLOCKED**
- sample fixture only → 최대 **RESEARCH_ONLY**
- 실데이터지만 거래 수 < 100 → 최대 **CAUTIOUS_CANDIDATE**
- `--strict` 시 품질 WARN → 최대 RESEARCH_ONLY

## 14~16. 자동 적용 아님 / 실전 승인 아님 / 수익 보장 아님

`RealDataStrategyReport.do_not_auto_apply=True` / `auto_apply_allowed=False` /
`is_live_authorization=False` / `is_order_signal=False` / `contains_secret=False` /
`kis_historical_available=False` 불변(dataclass `__post_init__` 가드). 결과가 좋아 보여도
자동 설정 변경 / live promotion / threshold 자동 적용 0건. 실전 전환은 별도 옵트인 PR +
사용자 명시 승인 + Paper Gate 통과가 필요하다.

## 사용

```
# 단일 CSV
python scripts/run_real_data_strategy_validation.py --input-csv data/market/real_ohlcv/005930.csv \
    --markdown reports/strategy_validation/real_data_strategy.md --write-latest
# 다종목 디렉토리
python scripts/run_real_data_strategy_validation.py --input-dir data/market/real_ohlcv/ --symbols 005930,000660
# 심볼 + yfinance(준실제)
python scripts/run_real_data_strategy_validation.py --symbols 005930 --allow-yfinance --start 2025-01-01 --end 2025-06-30
```
exit: 0 (평가 완료) / 1 (BLOCKED) / 2 (실행 오류).

UI: AISignal 탭 `RealDataStrategyValidationCard` — data_source / real_data_used /
sample fixture 경고 / 표본 / 4 score / verdict / 강한·위험 국면 / Agent 도움·방해 / 다음 단계.
새로고침·복사 버튼만, 매수/매도/실전/자동적용/승인 버튼 0개. API:
`GET /api/system/real-data-strategy-validation/latest` (read-only; latest 리포트 또는
quasi-real 데모 즉석 계산).

데이터 저장 위치(모두 gitignore): `data/market/real_ohlcv/` · `data/backtest/` ·
`reports/strategy_validation/`.
