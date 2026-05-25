# KIS-INTRADAY-60D-WEEKLY-FIXED-REVALIDATION-01 — 60d/weekly 고정 룰 holdout 재검증

## 목적

FORWARD-UNIVERSE-REBUILD 의 grid 에서 60d/weekly 가 +8.3% 로 유망했으나 *grid search 결과*라
사후최적화 의심이 있었다. 본 모듈은 그 변형을 **하나의 고정 룰(V1)** 로 잠그고(검증 중 변경
금지), holdout 으로 forward 유지 여부를 검증한다. **모든 universe 선택은 직전 lookback 만
사용(look-ahead 금지). Paper/Backtest only — EXE 빌드 0건, 실전 금지.**

## 고정 룰 FORWARD_UNIVERSE_60D_WEEKLY_LOCKED_V1

selector=FORWARD_STABLE_UNIVERSE · lookback=60거래일 · rebalance=weekly · universe size=10(primary;
15/20 보조) · agent=RISK_VETO_ONLY · selection=composite · allowed=GAP/ORB/VWAP · max_positions=5 ·
capital=10,000,000 · costs 1.5/18/5 bps · loss_defense=daily_loss_stop −1.5% · exit=stop/target+EOD
(trailing/max-hold/forced-close 금지). `rule_locked_before_validation=True`, `no_look_ahead=True`.

## 검증 세트 (모두 point-in-time lookback)

- original 6M replay (재현성) · last-20D holdout · **last-40D holdout(주 판정)** · worst-month holdout
  · even/odd symbol split · slippage stress(5/7/10bps) · 40d-monthly & static 비교 · RISK_VETO vs OFF
  · defense none vs daily · 보조 size 15/20. holdout 은 `count_window` 로 해당 기간 rebalance 만
  계상하되 lookback 은 실제 과거만 사용.

## verdict

LOCKED_RULE_FAIL(<0/PF<1.05/MDD>20) / WEAK / WATCH(≥3·PF≥1.10·MDD≤15) /
PAPER_CANDIDATE(≥5·PF≥1.15·MDD≤15·거래충분·worst-month 방어·slippage OK) /
RESEARCH_PROMISING(≥8·PF≥1.20·MDD≤12·안정·decay OK). `live_trading_recommendation` 항상 False.

## 실측 결과 (50종목·7개월, point-in-time)

- **최종: LOCKED_RULE_RESEARCH_PROMISING.**
- original 6M replay **+8.34%/PF1.47** (grid +8.3% 재현 ✓) · **last-40D holdout +8.77%/PF1.81/MDD1.38%/63거래** ·
  last-20D +6.27%/PF2.27 · **worst-month(3월) +1.0%(방어 성공, baseline −23.2%)**.
- slippage stress: 5bps +8.3% / 7bps +7.0% / 10bps +6.0%(비용 robust ✓).
- 비교: static +1.9% · 40d/monthly +3.6% · **60d/weekly +8.3%**(명확 우위). RISK_VETO +8.3% vs OFF −3.0%(재확인).
- 보조 size: 15 +10.0% / 20 +7.8%(일관). decay −0.43pp(holdout≥full).
- **caveat(중요)**: ① 표본 작음 — last-40D holdout **63거래**(<100, LOW_CONFIDENCE), 6개월 데이터 자체가 작음.
  ② **symbol-split breadth 의존** — disjoint 종목 절반에선 +1.5%/+2.3%(even-half PF<1)로 급감. +8.3%는 50종목
  중 best10 선택(breadth)에 의존. ③ worst-month PF 0.64(순 +1%는 얇음).

## 결론 / 권고

고정 룰이 holdout(last-20D/40D 양(+), worst-month 방어, 비용 robust, RISK_VETO 재확인)에서
**유지됨** → 처음으로 *모의 리허설 후보* 단계 도달. EXE 권고: **재빌드 후 dry-run KIS 모의 리허설
가능(실전 금지, 추가기간 권장)**. 단 표본/breadth 한계로 **추가 기간 데이터 수집 후 동일 룰
재검증**이 선행되어야 하며, **실전매매는 어느 경우에도 금지**.

## 안전 불변값

`Locked60dReport` 는 `is_live_authorization`/`live_trading_recommendation`/`broker_order_sent`/
`order_created`/`exe_build_executed`=False · `do_not_auto_apply`/`no_profit_guarantee`/
`rule_locked_before_validation`/`no_look_ahead`=True · `auto_apply_allowed`=False 불변(dataclass 가드).
모듈/스크립트 broker/OrderExecutor/route_order/KIS 주문 API/httpx/requests import·호출 0건,
tauri/cargo build 0건(정적 grep), 검증 중 룰 변경 0건, 안전 flag 변경 0건, 수익 보장 문구 0건.
