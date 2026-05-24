# 전략 가능성 종합 평가 (STRATEGY-VALIDATION-01)

> 최종 KIS 모의 장중 리허설(BUILD-02B) **전**, 사용자 매매기법 + Agent 결합 전략이
> *계속 검증해볼 가치가 있는지* 를 백테스트 / Walk-forward / Stress / Paper 결과를
> **종합** 해 advisory 로 판정하는 자료. **자동 적용 / 실전 전환 승인 / 주문 신호가
> 아니다. 수익을 보장하지 않는다.**

## 1. 목적

단순 수익률이 아니라 승률 · 손익비 · profit factor · MDD · expectancy · 안정성 ·
과최적화 위험 · 체결 실패율 · 차단 사유 · **Agent Council 이 단일 전략보다 나은지**
까지 종합해, 전략을 5단계로 평가한다.

## 2. 왜 백테스트만 보면 안 되는가

- 백테스트는 *과거 한 구간* 에 최적화된 착시(overfit)일 수 있다 → Walk-forward 로 검증.
- 백테스트는 *체결 실패 / 슬리피지 / 거절 / 미체결* 을 과소반영 → Stress + 실제 Paper 체결로 검증.
- sample fixture 백테스트는 **기능 확인용** 일 뿐 수익성 근거가 아니다 → 실데이터/Paper 필요.
- 따라서 **sample fixture 만으로는 STRONG_CANDIDATE 판정이 불가** 하다 (data-sufficiency 게이팅).

## 3~6. 평가 기준 (sub-score)

| score | 근거 |
|---|---|
| **backtest_score** | council(또는 best single) 의 win_rate / profit_factor / expectancy / max_consecutive_losses |
| **walk_forward_score** | overall_stability_score(×100) − overfit_suspected(−30) − collapse_segments(−10/개) |
| **stress_resilience_score** | 12 시나리오 PASS/WARN/**FAIL** (FAIL = 가드 보호 실패, 강한 감점) |
| **paper_execution_score** | Paper 성과(win_rate/expectancy) + 체결 품질(order_failure_rate/rejected_rate). 표본 부족 시 None(평가불가) |
| **agent_value_score** | council_better_than_best_single + WF OOS council_better_fraction |
| **risk_control_score** | max_consecutive_losses + stress FAIL + rejected_rate |
| **data_sufficiency_score** | backtest bar_count + 실데이터 여부 + WF split + Paper 표본 |

권장 기준값: win_rate ≥ 50% · payoff ≥ 1.0 · profit_factor ≥ 1.3 · expectancy > 0 ·
MDD ≤ 10% · max_consecutive_losses ≤ 5 · walk_forward stability ≥ 0.65 · overfit_suspected=false.

## 7. Agent 결합 효과 평가

핵심 질문: **"Agent Council 이 단일 전략(ORB/Momentum/Gap/VWAP)보다 실제로 나은가?"**

`agent_value_verdict`:
- `AGENT_ADDS_VALUE` — council 이 수익/리스크 동시 개선 (best single 대비 우위 + 높은 점수)
- `AGENT_RISK_REDUCTION_VALUE` — 수익은 낮아도 리스크(MDD/HOLD) 개선
- `AGENT_UNDERPERFORMS` — 단일 전략보다 못함
- `AGENT_VALUE_INSUFFICIENT_SAMPLE` — 표본 부족

## 8. 사용자 매매기법 적합성

`method_fit` 에 favorable_market_conditions / dangerous_market_conditions(백테스트
by_market_regime · by_time_phase 의 win_rate 버킷에서 유도) + recommended_tuning_candidates
(feedback 태그 OVER_ENTRY → 진입 강화, LATE_EXIT → 청산 조정 등)를 표시한다.
**모든 튜닝 후보는 `do_not_auto_apply=True` — 자동 적용되지 않으며 운영자 검토 + 별도 PR +
재백테스트가 필요하다.**

## 9. overall_strategy_potential_score

가용한 sub-score 의 가중 평균(0~100). 평가 불가(None) sub-score 는 평균에서 제외하고
data_sufficiency_score 에 반영한다.

## 10. 5단계 판정

| verdict | 의미 |
|---|---|
| **STRONG_CANDIDATE** | 백테스트 양호 + WF 안정 + Stress FAIL 0 + Agent 우위 + **실데이터 + Paper Gate 표본(100건/28일)**. sample fixture/Paper 부족이면 **불가** |
| **CAUTIOUS_CANDIDATE** | 일부 지표 양호하나 표본/실데이터 부족 — 소액 모의 운영 지속 권장 |
| **RESEARCH_ONLY** | 구조는 의미 있으나 성과 불안정 / 과최적화 위험 — 리서치·튜닝 필요 |
| **NOT_READY** | expectancy ≤ 0 / profit_factor < 1.0 / Stress FAIL 1건 등 핵심 기준 미달 |
| **BLOCKED** | Stress FAIL ≥ 2(가드 보호 실패) / secret 노출 / live_authorization 주장 등 치명·안전 문제 |

## 11. sample fixture 결과의 한계

`backend/tests/fixtures/backtest/sample_ohlcv.csv` 는 **합성 데이터** 다. 본 평가에서
sample fixture 결과는 `sample_fixture_only=True` 로 표시되고 수익성 판정에 낮은 가중치만
가지며, **STRONG_CANDIDATE 로 올라갈 수 없다**. 실/준실제 OHLCV 로 재실행해야 수익성을
논할 수 있다.

## 12. Paper 100건 / 28거래일 기준

`paper_sample_class`:
- `PAPER_NO_TRADES_YET` (0건) / `PAPER_SAMPLE_TOO_SMALL` (1~29건) → 수익성 판단 불가
- `PAPER_EARLY_SIGNAL` (30~99건) → 방향성만 관찰
- `PAPER_GATE_EVALUABLE` (100건 + 28거래일 이상) → 실전 검토 표본 충족 (그래도 STRONG 은
  다른 조건도 모두 충족해야 하며, 실전 전환은 별도 게이트 + 운영자 승인 필요)

## 13. 추천 다음 단계

1. 실/준실제 OHLCV 데이터로 백테스트 + Walk-forward 재실행
2. Paper 모의 운영 지속 (목표 100건 + 28거래일)
3. KIS 모의 장중 리허설(BUILD-02B)로 체결 품질 관찰 (실거래 아님)
4. 튜닝 후보는 운영자 검토 + 별도 PR + 재백테스트 (자동 적용 금지)

## 14~16. 자동 적용 아님 / 실전 승인 아님 / 수익 보장 아님

- `do_not_auto_apply=True` / `auto_apply_allowed=False` — threshold 추천 자동 적용 0건.
- `is_live_authorization=False` / `is_order_signal=False` — 결과가 좋아도 실전 전환 / 주문 0건.
- `contains_secret=False` — 자격정보(계좌/secret) 미표시.
- 결과가 좋아 보여도 live promotion / 자동 설정 변경 0건. 실전 전환은 별도 옵트인 PR +
  사용자 명시 승인 + Paper Gate(100건/28일) 통과가 필요하다.

## 사용

```
# sample fixture 로 backtest/WF/stress in-process 실행 후 종합 (실데이터 아님)
python scripts/run_strategy_potential_report.py --run-sample \
    --markdown reports/strategy_validation/strategy_potential.md

# 사전 생성된 리포트 JSON 주입 (실데이터 백테스트/WF/stress/paper)
python scripts/run_strategy_potential_report.py \
    --backtest-json bt.json --walk-forward-json wf.json --stress-json st.json \
    --paper-json paper.json --order-quality-json oq.json --has-real-data
```
exit: 0 (평가 완료) / 1 (BLOCKED) / 2 (실행 오류).

UI: AISignal 탭 `StrategyPotentialReportCard` — verdict + 7 sub-score + 강점/약점/리스크/
다음 단계 + "자동 적용 안 됨 · 실전 승인 아님 · 수익 보장 아님" 표시. 새로고침/복사 버튼만,
매수/매도/실전/승인 버튼 0개. API: `GET /api/system/strategy-potential` (read-only).
