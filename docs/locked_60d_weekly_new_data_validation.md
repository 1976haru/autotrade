# KIS-INTRADAY-60D-WEEKLY-NEW-DATA — 고정 룰 추가 기간 새 데이터 재검증

## 목적

60D-WEEKLY-FIXED-REVALIDATION 의 고정 룰(`FORWARD_UNIVERSE_60D_WEEKLY_LOCKED_V1`)이
**기존 6개월 밖 새 데이터** 에서도 유지되는지 확인. 룰은 **절대 변경하지 않으며**(rule hash
검증), universe 선택은 직전 lookback 만(look-ahead 금지). **Paper/Backtest only — EXE 빌드 0건,
실전 금지.**

## 구성

- `scripts/collect_forward_extra_ohlcv.py` — 기존 read-only collector 재사용, 추가 기간을 *별도
  경로* `data/market/intraday_5m_forward_extra/` 에 resume 수집(progress/failed/quality json).
  기존 6개월 CSV 보존, KIS 주문 API 0건.
- `app/system/locked_60d_weekly_new_data_validation.py` — rule hash lock 검증
  (`EXPECTED_LOCKED_RULE_HASH`, 불일치→BLOCKED) + 새 거래일 수 계산 + (충분 시) new-data-only /
  extended walk-forward / slippage stress / Agent 비교 / breadth / decay + verdict.
- CLI `scripts/run_locked_60d_weekly_new_data_validation.py` + endpoint
  `GET /api/system/locked-60d-weekly-new-data/latest` + UI `Locked60dWeeklyNewDataCard`(AISignal 탭,
  데이터없음/수집중/분석중/완료/실패, 새로고침·복사만).

## verdict

NEW_DATA_BLOCKED(룰 hash 불일치) / **NEW_DATA_INSUFFICIENT(새 거래일<20)** / FAIL / WEAK /
WATCH / PAPER_CANDIDATE / RESEARCH_CONFIRMED. `live_trading_recommendation`·`real_order_allowed`
항상 False, `dry_run_required` 항상 True.

## 실측 결과 (2026-06)

- **추가 forward 데이터 미존재**: KIS 시세 최신일 = **2026-05-22**(미래 일자 조회 시에도 동일),
  기존 6개월(2025-11-25~2026-05-22)이 이미 포함. 추가 수집(8종목, end=2026-05-26)은 전부 기존과
  overlap → **새 거래일 0**.
- **최종 verdict: NEW_DATA_INSUFFICIENT.** rule_hash_match=True(룰 불변 확인), new_trading_days=0.
- **EXE 재빌드: 보류** — 추가 forward 데이터로 고정 룰을 확정할 수 없음. 60D-WEEKLY holdout 의
  RESEARCH_PROMISING 은 *기존 6개월 내부* 결과이며, 새 forward 기간 확인은 *미달*.

## 결론 / 권고

고정 룰은 변경되지 않았고(hash 일치) 새 데이터로 적용할 준비가 됐으나, **시장 데이터가 아직 더
진행되지 않아(KIS 최신 05-22) 추가 forward 검증을 수행할 수 없다.** 따라서 EXE 재빌드는 **보류**
하고, 장이 더 진행돼 **새 거래일이 ≥20 쌓이면 동일 고정 룰(hash 일치)로 자동 재검증**한다. 그때
PAPER_CANDIDATE 이상이면 dry-run(자동주문 OFF, `KIS_PAPER_AUTO_ORDER_DRY_RUN=true`) EXE 재빌드를
검토한다. **실전매매는 어느 경우에도 금지, 실제 모의주문도 별도 승인 게이트 필요.**

## 안전 불변값

`NewDataReport` 는 `is_live_authorization`/`live_trading_recommendation`/`real_order_allowed`/
`broker_order_sent`/`order_created`/`exe_build_executed`=False · `dry_run_required`/
`do_not_auto_apply`/`no_profit_guarantee`=True · `auto_apply_allowed`=False 불변(dataclass 가드).
모듈/스크립트 broker/OrderExecutor/route_order/KIS 주문 API/httpx/requests import·호출 0건
(수집기는 기존 read-only 시세 collector 재사용), tauri/cargo build 0건, rule hash 검증으로 검증 중
파라미터 변경 0건, look-ahead 0건, 기존 6개월 CSV 보존, 안전 flag 변경 0건, 수익 보장 문구 0건.
