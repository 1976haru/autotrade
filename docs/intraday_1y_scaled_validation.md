# KIS-INTRADAY-1Y-SCALED-VALIDATION-01 — 1년 데이터 10/25/50 확장 검증

## 목적

LOCKED-60D-WEEKLY 의 RESEARCH_PROMISING 은 6개월 내부 holdout 결과였고, 추가 forward 데이터가
없어(NEW_DATA_INSUFFICIENT) 확정 불가였다. 본 작업은 새 데이터를 기다리는 대신 **과거 1년**
5분봉으로 고정 룰을 변경 없이 10→25→50 종목으로 확장 검증해 **종목 폭 의존성**·분기/반기
안정성·RISK_VETO 유효성을 본다. **Paper/Backtest only — EXE 빌드 0건, 실전 금지.**

## 데이터

KIS 분봉은 ~1년 전(2025-05-26)까지 제공 확인. 기존 `kis_6m`(2025-11-25~2026-05-22) 보존 + 별도
수집한 older 6개월(`data/market/intraday_5m_1y_old`, 2025-05-27~2025-11-24)을 **병합**(중복 제거)
→ 약 1년(≥240 거래일). 수집은 기존 read-only collector(resume/progress/failed/원자적 저장) 재사용,
**KIS 주문 API 0건**.

## 구성

- `scripts/collect_kis_intraday_ohlcv.py` 재사용(older 6개월 수집, progress/failed json).
- `app/system/intraday_1y_scaled_validation.py` — 1년 병합 + rule hash lock 검증 + 10/25/50 단계
  locked-rule 백테스트 + 월/분기/반기/worst-month/symbol-split/Agent/cost/defense + scale verdict.
- CLI `scripts/run_intraday_1y_scaled_validation.py` + endpoint
  `GET /api/system/intraday-1y-scaled-validation/latest` + UI `Intraday1YScaledValidationCard`
  (AISignal 탭, 데이터없음/수집중/분석중/완료/실패, 새로고침·복사만).

## 고정 룰 / verdict

룰 = `FORWARD_UNIVERSE_60D_WEEKLY_LOCKED_V1` (변경 없음, hash 검증, look-ahead 금지).
단계 verdict: SCALE_BLOCKED(hash 불일치)/SCALE_FAIL(데이터부족·<0·PF<1.05·MDD>20)/WEAK/
WATCH(≥3·PF≥1.10·MDD≤15)/PAPER_CANDIDATE(≥5·PF≥1.15·MDD≤15·거래≥150·posR≥0.5·slip OK·RISK_VETO 우위)/
RESEARCH_CONFIRMED(≥8·PF≥1.20·MDD≤12·…). **전체 verdict는 단계 최소(보수)** — 50종목이 통과해도
10/25 중 FAIL 시 WATCH 로 cap. `live_trading_recommendation`·`real_order_allowed`=False,
`dry_run_required`=True 항상.

## 결과 / EXE 판단

- SCALE_FAIL/WEAK → EXE 재빌드 보류.
- SCALE_WATCH → 관찰용 EXE 재빌드 가능(자동매매/모의주문 비활성).
- SCALE_PAPER_CANDIDATE 이상(50종목 1년 통과) → dry-run(자동주문 OFF, `KIS_PAPER_AUTO_ORDER_DRY_RUN=true`)
  EXE 재빌드 검토. **실전 금지, 실제 모의주문도 별도 승인 게이트.**
- 10만 좋고 25/50 약화 → breadth 의존 확인 → WATCH 이하.

(실측 수치는 1년 수집 완료 후 `intraday_1y_scaled_validation_latest.json` 에 기록.)

## 안전 불변값

`Scaled1YReport` 는 `is_live_authorization`/`live_trading_recommendation`/`real_order_allowed`/
`broker_order_sent`/`order_created`/`exe_build_executed`=False · `dry_run_required`/
`do_not_auto_apply`/`no_profit_guarantee`=True · `auto_apply_allowed`=False 불변(dataclass 가드).
모듈/스크립트 broker/OrderExecutor/route_order/KIS 주문 API/httpx/requests import·호출 0건,
tauri/cargo build 0건, rule hash 로 검증 중 파라미터 변경 0건, look-ahead 0건, 기존 6개월 CSV 보존,
안전 flag 변경 0건, 수익 보장 문구 0건.
