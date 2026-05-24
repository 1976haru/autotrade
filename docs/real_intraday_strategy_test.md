# 실제 분봉 데이터 기반 전략 가능성 최종 테스트 (REAL-INTRADAY-TEST-01)

> 사용자 핵심 질문 — **"내 매매기법 + Agent Council 전략이 *실제 데이터* 기준으로 가능성이
> 있는가?"** 에 대해, 실제 분봉을 수집해 끝까지 테스트하고 *사용자용 최종 판단* 을 산출한다.
> **자동 적용 아님 · 실전 승인 아님 · 수익 보장 아님.**

## 1. 한 줄 결론 (2026-05-25 실측)

실제 yfinance 분봉(KOSPI 10종목 · 5분봉 · 60거래일 · 종목당 4,238봉)으로 ORB/Momentum/Gap/
VWAP + Agent Council + RiskOfficer + exit_plan + quality_score gate 결합 전략을 테스트한 결과:

> **현재 실제 분봉 데이터 기준으로, 사용자님의 매매기법 + Agent 전략은
> "연구를 계속할 가치는 있지만 튜닝이 필요합니다."** (user_final_judgement = `WORTH_MORE_RESEARCH`)

## 2. 실측 요약

| 항목 | 값 |
|---|---|
| 데이터 출처 | yfinance intraday (`005930.KS` 등), 5분봉, 60일 — **실제 시장 데이터** |
| 종목 수 / PASS | 10 / 10 (BLOCKED 0) |
| total_bars / total_trades | ~42,380 / **4,719** |
| median win_rate | ~0.48 |
| median profit_factor | ~1.10 |
| median expectancy | 양(+) (종목별 편차 큼) |
| median walk_forward_score | **~0 (낮음 — OOS 안정성 약함, 과최적화 의심)** |
| Agent vs 단일전략 | **AGENT_ADDS_VALUE** (도움 6종목 / 방해 4종목 / 무진입 0) |
| 개발자 verdict | `RESEARCH_ONLY` |
| 사용자 판단 | **`WORTH_MORE_RESEARCH`** |
| Paper 리허설 권고 | 아니오(아직) |

종목 편차: 005930 PF 2.9·WF 100 / 000660 PF 2.08 / 006400 PF 1.87 (양호) vs 035420 PF 0.24 /
035720 PF 0.17 / 005380 expectancy 음(-) (저조). → 전종목 일괄이 아니라 *선별/튜닝* 이 필요.

## 3. 데이터 확보 우선순위 / 실제 데이터 여부

1순위 기존 CSV → 2순위 **yfinance intraday(성공)** → 3순위 KIS 분봉(공식 endpoint 미확인 →
`NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION`, 실제 호출 0건) → 4순위 합성 fixture(실제 가능성 판단
*불가* 표시). 본 테스트는 **실제 시장 분봉(actual_data_used=True)** 사용 — 합성 fixture 결과를
실제 가능성으로 보고하지 않는다.

## 4. 실행 방법

```
# 1) 실제 분봉 수집 (yfinance 5분봉 60일)
python scripts/collect_yfinance_intraday_ohlcv.py \
    --symbols 005930,000660,035420,035720,005380,000270,006400,373220,005490,068270 \
    --period 60d --interval 5m --output-dir data/market/intraday_ohlcv \
    --json reports/strategy_validation/intraday_collect.json
# 2) 전략 최종 테스트 + 사용자 판단
python scripts/run_real_intraday_final_test.py \
    --input-dir data/market/intraday_ohlcv --data-source yfinance_intraday_5m_60d
```
결과: `reports/strategy_validation/real_intraday_final_result.{md,json}` (gitignore).

## 5. 사용자 최종 판정 6단계 + 게이팅

`PROMISING_FOR_PAPER_TEST`(실데이터+거래100+·PF≥1.2·expectancy>0·WF≥40·Agent 도움) /
`WORTH_MORE_RESEARCH`(실데이터+거래30+·일부 양호·WF/Agent 약함) / `TOO_EARLY_TO_JUDGE`(표본
부족·종목<3·거래<30) / `STRATEGY_NEEDS_TUNING`(거래 0·진입 못함) /
`NOT_PROMISING_ON_CURRENT_DATA`(충분 거래+PF<1+expectancy≤0) / `BLOCKED_BY_DATA`(PASS 0·합성뿐).

**게이팅(보수)**: 합성 fixture → 최대 TOO_EARLY, total_trades<30 → 최대 TOO_EARLY, <100 →
최대 WORTH_MORE_RESEARCH, WF 낮음 → PROMISING 금지, Agent 무진입 → PROMISING 금지, Paper 0건 →
실전 검토 불가.

## 6. 왜 WORTH_MORE_RESEARCH인가 (이번 실측)

- ✅ 실제 분봉 + 거래 4,719건(충분) + median expectancy 양(+) + Agent ADDS_VALUE.
- ❌ median PF ~1.10 < 1.2, **median walk_forward_score ~0 (OOS 안정성 약함 → 과최적화 의심)**,
  일부 종목 음의 성과. → PROMISING 조건 미충족, 그러나 방향성은 있어 *연구·튜닝 가치*.

## 7. 지금 바로 할 일 (다음 단계)

1. 성과 음수/저조 종목(035420·035720·005380 등) 제외 후 파라미터 튜닝 — **별도 PR + 재백테스트,
   자동 적용 금지.**
2. 더 긴 기간/더 많은 종목 분봉으로 **walk-forward 안정성** 재확인(현재 가장 약한 고리).
3. Paper 모의 운영 100건 + 28거래일 표본 축적 → 그 후 실전 검토(별도 옵트인 + 운영자 승인).

## 8. 안전

`RealIntradayFinalResult.do_not_auto_apply=True` / `is_live_authorization=False` /
`is_order_signal=False` / `contains_secret=False` / `no_profit_guarantee=True` 불변(dataclass
가드). broker / OrderExecutor / route_order / KIS 주문 API 호출 0건. PROMISING 이어도 *실전이
아니라 Paper 리허설* 단계다.

## UI / API

AISignal 탭 `IntradayStrategyValidationCard` 상단에 **사용자 최종 판단 headline**(한 줄 결론 +
실제 데이터 여부 + Paper 권고 + "실전 승인 아님·수익 보장 아님") 표시. API
`GET /api/system/real-intraday-final-result/latest` (read-only; 파일 없으면 empty fallback,
무거운 backtest 는 CLI 전용).
