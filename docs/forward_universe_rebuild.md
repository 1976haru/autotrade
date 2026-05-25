# KIS-INTRADAY-FORWARD-UNIVERSE-REBUILD-01 — point-in-time universe 선별 검증

## 목적

FORWARD-VALIDATION-01 의 FORWARD_WEAK 원인은 universe(GO+TUNE top10)를 *6개월 전체*
in-sample 으로 골랐기 때문(rolling symbol-split decay +10pp). 본 모듈은 각 rebalance
시점에서 **그 이전 lookback 구간 데이터만** 으로 종목 점수를 계산하고, 다음 test 구간에
*그때 선정된 종목만* 고정 적용한다 — **미래 구간 성과는 점수에 사용하지 않는다(look-ahead 금지).**
**Paper/Backtest only — EXE 빌드 0건, 실전 금지.**

## 구성

- `app/system/forward_universe_selector.py` — lookback-only per-symbol 합성 score(고정 가중치)
  + rebalance 일자(weekly/biweekly/monthly) + 12 selector 변형.
- `app/system/forward_universe_validation.py` — rolling rebalance chain(각 기간 universe 재선정)
  + selector variants / lookback·rebalance grid / Agent·손실방어 결합 + verdict + EXE 권고.
  look-ahead selector(STATIC_IN_SAMPLE_TOP10)는 *참고용*, 최종 후보 제외.
- CLI `scripts/run_forward_universe_validation.py` (→ `forward_universe_*.{json,md}` + `*_latest.json`).
- endpoint `GET /api/system/forward-universe/latest` + UI `ForwardUniverseCard`(AISignal 탭, 4상태,
  새로고침·복사만).

## Score 산식 (검증 전 고정, 재최적화 금지)

`0.30·z(cost_adj_expectancy) + 0.25·z(profit_factor) + 0.20·z(win_rate) + 0.15·z(liquidity)
+ 0.10·z(time_bucket_edge)` − penalties(low_trades / neg_cost_edge / low_win_rate /
signal_noise / agent_veto_repeat). 모든 통계는 lookback 구간만 사용.

## verdict

- `UNIVERSE_FAIL`: forward<0 또는 거래<30 → EXE 보류.
- `UNIVERSE_WEAK`: 양(+)이나 약함 → EXE 보류/관찰용만.
- `UNIVERSE_WATCH`: forward≥2·MDD≤20·거래≥60 → 관찰용 EXE 가능, 자동매매/모의주문 비활성.
- `UNIVERSE_PAPER_CANDIDATE`: forward≥5·MDD≤15·거래≥100·posR≥0.55 → dry-run 중심 KIS 모의 리허설(실전 금지).
- `live_trading_recommendation` 항상 False.

## 실측 결과 (50종목·7개월, point-in-time)

- **최종: UNIVERSE_WATCH** (FORWARD-VALIDATION 의 WEAK 대비 개선).
- **STATIC_BASELINE_ALL(선별 없음): −1.3%(FAIL)** vs **best forward FORWARD_STABLE_UNIVERSE +3.6%/MDD1.4%/101거래/posR0.75(WATCH)** → **point-in-time 종목 선별이 ~+4.9pp 추가**(forward 에서도 의미 있음).
- 참고(편향) STATIC_IN_SAMPLE_TOP10(look-ahead) +10.3% — 후보 제외(미래 전체 hindsight 상한).
- Lookback/rebalance grid: 60d+weekly **+8.3%**, 40d+weekly +5.5% → **lookback 길수록·rebalance 잦을수록** 개선 경향(별도 고정 후 재검증 필요).
- Agent: RISK_VETO_ONLY +3.6%(최고), OFF −1.1%(악화 일관), SIZER/veto+sizer 0.0(작은 universe 과제약), REVIEW −1.1%.
- 손실방어: forward-stable universe 에선 stop 미발동(추가 손실 방어 여지). 반복 선택: 005930·042700·000270·005380.

## 결론

종목 선별을 **forward(point-in-time)** 로 재설계하니 static 대비 양(+)으로 돌아섰다
(UNIVERSE_WATCH, best +3.6%). RISK_VETO 의 가치도 재확인. 다만 locked default 는 아직
PAPER_CANDIDATE 미달 — 60d/weekly 같은 더 긴 lookback 후보를 *고정해* 추가 기간으로
재검증해야 하며, 통과 시에만 *관찰용* EXE 재빌드(자동매매·모의주문 비활성). **실전 금지.**

## 안전 불변값

`ForwardUniverseReport` 는 `is_live_authorization`/`live_trading_recommendation`/`broker_order_sent`/
`order_created`/`exe_build_executed`=False · `do_not_auto_apply`/`no_profit_guarantee`=True ·
`auto_apply_allowed`=False 불변(dataclass 가드). 모듈/스크립트 broker/OrderExecutor/route_order/
KIS 주문 API/httpx/requests import·호출 0건, tauri/cargo build 0건(정적 grep), look-ahead
universe 선택 후보 제외, 안전 flag 변경 0건, 수익 보장/실전 전환 문구 0건.
