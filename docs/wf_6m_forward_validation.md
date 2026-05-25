# KIS-INTRADAY-FORWARD-VALIDATION-01 — 고정 룰 forward/out-of-sample 검증

## 목적

DECOMPOSITION-01 의 개선 조합(PAPER_REHEARSAL_CANDIDATE, +9%)은 *동일 6개월 내부*에서
선택돼 in-sample 편향 가능성이 있다. 본 모듈은 룰을 **검증 전에 고정**하고
(`rule_locked_before_validation=True`), universe/ranking 을 *train 구간/종목에서만* 만들어
*test 구간/종목*에 적용해 forward 생존을 검증한다. **Paper/Backtest only — EXE 빌드 0건.**

## 구성

- `app/system/wf_6m_forward_validation.py` — 고정 룰 후보 A/B/C/D + 4 split + Agent forward
  재검증 + decay/overfit + forward verdict + EXE 재빌드 권고.
- `wf_6m_sim_v2` 에 `by_symbol`(train universe 산출) + `apply_position_sizing`(veto+sizer 결합) 추가.
- CLI `scripts/run_wf_6m_forward_validation.py` (→ `forward_validation_*.{json,md}` + `*_latest.json`, gitignore).
- endpoint `GET /api/system/forward-validation/latest` + UI `ForwardValidationCard`(AISignal 탭,
  4상태, 새로고침·복사만 — 주문/실전/적용/자동매매 시작 버튼 0개).

## 고정 룰 후보 (검증 전 정의)

- A: GO+TUNE top10 + RISK_VETO_ONLY + composite + daily_loss_stop 1.5%.
- B: GO+TUNE top20 + POSITION_SIZER_ONLY + equity_dd_stop 10%.
- C: EXCLUDE 제거 + RISK_VETO_ONLY + daily_loss_stop 1.5%.
- D: Agent OFF + 순수 전략(GAP+ORB+VWAP) — Agent 없이 생존 재확인.

universe/grade/ranking/strategy_pf/bucket_edge 는 **train 구간/종목에서만** 산출(test 미참조).

## Split

- **Monthly forward**: 누적 train(≥2개월) → 다음 1개월 test, 룰 고정 적용.
- **Anchored forward**: 첫 2개월에서 universe/룰 *한 번* 생성 → 이후 월 test.
- **Rolling symbol split**: 50종목을 train/test 종목군으로 분리, train 종목 ranking 을 test 종목에 적용(종목 선택 편향 측정).
- **Worst-month holdout**: 최악월(3월) 완전 holdout, 나머지로 만든 룰을 3월에 적용.

## forward verdict

- `FORWARD_FAIL`: forward return<0 또는 PF<1.05 → EXE 재빌드 보류.
- `FORWARD_WEAK`: 양(+)이나 약함 → EXE 재빌드 가능하나 자동매매/모의주문 비활성, 관찰용 UI만.
- `FORWARD_WATCH`: forward≥2%·PF≥1.10·MDD≤20·거래≥60 → 모의 리허설 준비 가능, 자동주문 dry-run 우선.
- `PAPER_REHEARSAL_CONFIRMED`: forward≥5%·PF≥1.15·MDD≤15·거래≥100·positive_ratio≥0.5·worst-month 방어·anchored 양(+) → KIS 모의매매 리허설 가능(실전 금지).
- 어떤 경우에도 `live_trading_recommendation=False`.

## 실측 결과 (50종목·7개월, train-only universe)

- **최종: FORWARD_WEAK.** in-sample +9% 가 forward 에서 살아남지 못함.
- 후보별 forward: **A +2.3%/PF1.08/MDD4.9%/188거래(WEAK)**, C −1.3%/PF1.16(FAIL, 근접),
  B −0.2%(FAIL), **D(Agent OFF) −21.6%(FAIL)**.
- **Rolling symbol split decay +10pp**(A·C) — universe/ranking 이 train *종목*에 과적합(미지 종목에서 ~10pp 손실). → in-sample 편향이 핵심.
- Worst-month holdout(3월): A −10.1%, C −5.6% — baseline −23.2% 대비 **방어 성공**(룰의 손실방어는 OOS 에서도 유효), 단 여전히 음(−).
- Agent forward: RISK_VETO +2.3%(최고·최안정), OFF −2.9%, SIZER +0.2%, **veto+sizer −2.6%(과도 결합)**, REVIEW −2.9%. → Agent 는 RISK_VETO 단독이 가장 robust, OFF 는 OOS 에서도 최악.

## 결론

in-sample 개선(+9%)은 주로 **종목 선택 in-sample 편향**(symbol-split decay +10pp)에서 왔으며,
forward 에서는 **+2.3%/PF1.08(WEAK)** 로 약화. Paper 리허설 확대는 **아직 불가**. 손실방어 룰과
RISK_VETO 는 OOS 에서도 의미가 있으나, universe 선택을 forward 로 재설계해야 한다.

## 안전 불변값

`ForwardReport` 는 `is_live_authorization`/`live_trading_recommendation`/`broker_order_sent`/
`order_created`/`exe_build_executed`=False · `do_not_auto_apply`/`no_profit_guarantee`=True ·
`auto_apply_allowed`=False 불변(dataclass 가드). 모듈/스크립트 broker/OrderExecutor/route_order/
KIS 주문 API/httpx/requests import·호출 0건, tauri/cargo build 0건(정적 grep), 안전 flag 변경 0건,
수익 보장/실전 전환 문구 0건, 기존 final result 보존.
