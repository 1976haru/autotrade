# Agent / Risk Gate 스트레스 테스트 (#48 / 6-03)

실전과 유사한 **악조건**(슬리피지 / 부분체결 / 거절 / 미체결 / stale price / 급락 /
급등 / 데이터락 / 포트폴리오 drift / 일일 손실한도 / kill switch)을 fixture·합성
입력으로 재현하고, **기존 안전 가드(Agent Council, Risk Gate, 손실 한도, kill
switch, 주문품질 분류, 포지션 정합성)가 정상 동작하는지** 검증해
`stress_test_report` 를 생성한다.

> ⚠️ **본 작업은 스트레스 *검증* 작업이다.** 실제 주문을 보내지 않으며, KIS 실전/
> 모의 주문 API 를 호출하지 않고, 실전 기능을 켜지 않는다. **스트레스 결과만으로
> 실전 전환(live promotion)을 허가하지 않으며, 과거/모의 결과는 미래 수익을 보장하지
> 않는다.**

관련 코드:
- `backend/app/stress_test/agent_stress_test.py` — 시나리오 / 검증 / 리포트
- `scripts/run_agent_stress_test.py` — CLI (JSON / Markdown)
- 테스트: `backend/tests/test_agent_stress_test.py`,
  `backend/tests/test_run_agent_stress_test_script.py`

## 1. 스트레스 테스트 목적

악조건에서 *진짜 가드가 보호 동작을 하는지* 검증한다. 본 모듈은 가드를 재구현하지
않고 **기존 함수를 그대로 호출** 한다 — 따라서 검증 결과가 실제 장중 동작과 일치한다.
재사용하는 가드:

- `RiskManager.check_order / evaluate_order` — stale price hard-reject, 시장 regime
  `BLOCK_NEW_BUY`, `emergency_stop`(kill switch), 일일 손실한도 → REJECTED.
- `app.risk.loss_limits.DailyLossLimitRule` — 일일 손실한도 `block_buy`.
- `app.kis_paper.order_quality.build_order_quality_log` — 슬리피지 bps / 부분체결 /
  거절 / 미체결 분류.
- `app.reconciliation.position_checker.compare_positions` — 포트폴리오 drift.
- `app.agents.agent_council.run_agent_council` — 악조건 입력 시 BUY 미발생 확인.

## 2. 테스트하는 악조건 (12 시나리오)

| # | scenario | 기대 동작(가드) | 판정 |
|---|---|---|---|
| 1 | `SLIPPAGE_HIGH` | 높은 슬리피지가 bps 로 측정·기록(주문품질 악화 플래그) | WARN |
| 2 | `PARTIAL_FILL` | filled<requested → partial_fill=true, remaining 기록, FILLED 처리 금지 | WARN |
| 3 | `ORDER_REJECTED` | broker_order_sent=false / status=REJECTED (submitted 처리 금지) | PASS |
| 4 | `UNFILLED_TIMEOUT` | UNFILLED 기록 + fill polling 필요 (FILLED 처리 금지) | WARN |
| 5 | `PRICE_STALE` | 오래된 시세 → 신규 BUY REJECTED (RiskManager step 1.5) | PASS |
| 6 | `MARKET_CRASH` | 급락 → risk gate 신규 BUY 차단 + Council final_action≠BUY | PASS |
| 7 | `GAP_DOWN_OPEN` | 급락 출발 → 신규 BUY 차단 (보유는 손절 후보) | PASS |
| 8 | `GAP_UP_SPIKE` | 급등 과열 → risk_flag 증가 / 추격매수 방지 | WARN |
| 9 | `DATA_LOCK` | 시세 없음/멈춤 → 자동 판단 skip(HOLD), 주문 0건 | PASS |
| 10 | `PORTFOLIO_DRIFT` | 프로그램 vs broker/Paper 포지션 불일치 감지 → 차단/WARN | WARN |
| 11 | `DAILY_LOSS_LIMIT` | 일일 손실한도 초과 → 신규 BUY 차단 + kill switch 권고 | PASS |
| 12 | `REPEATED_REJECTION` | kill switch(emergency_stop) → 모든 주문 REJECTED + 반복 거절 위험 경고 | PASS |

판정 의미:
- **PASS** — 가드가 기대대로 보호 동작(차단/HOLD/정확 분류).
- **WARN** — 악조건이 advisory 로 플래그됨(hard block 불필요, 기록·경고).
- **FAIL** — 가드가 보호하지 못함(예: stale price 에서 BUY 허용). 즉시 조치 대상.

`overall_verdict` = FAIL 있으면 FAIL, 아니면 WARN 있으면 WARN, 아니면 PASS.
`--strict` 면 WARN 도 FAIL 로 격상.

## 3. slippage 설명

요청가 대비 체결가가 불리한 정도를 bps 로 측정(`_compute_slippage_bps`). BUY 는
체결가>요청가, SELL 은 부호 정규화해 *불리한 방향이 양수*. 임계
`SLIPPAGE_WARN_BPS`(기본 50bps) 이상이면 주문품질 악화로 WARN.

## 4. partial fill 설명

`filled_quantity < requested_quantity` → `partial_fill=true`,
`unfilled_quantity = requested - filled`. **부분체결을 완전체결(FILLED)로 처리하지
않는지** 검증 — 포트폴리오에 잘못 반영되면 FAIL.

## 5. order rejection 설명

거절은 `broker_order_sent=false` / `order_status=REJECTED` 로 기록되고 체결로 오인되지
않아야 한다. 반복 거절은 위험 경고/차단(`auto_stop_consecutive_rejections`) 권고.

## 6. stale price 설명

`RiskManager.policy.stale_price_max_age_seconds`(기본 60초)를 초과한 시세에서는
신규 BUY 가 hard-reject(`REJECTED`)된다. stale 에서 BUY 가 생성되면 FAIL.

## 7. crash / gap 설명

- **MARKET_CRASH** — `market_regime=TREND_DOWN` + `regime_decision=BLOCK_NEW_BUY` →
  RiskManager 가 신규 BUY 차단, Agent Council 도 `final_action≠BUY`.
- **GAP_DOWN_OPEN** — 전일 대비 급락 출발 → 신규 BUY 차단, 보유 포지션은 손절 후보.
- **GAP_UP_SPIKE** — 급등 후 고변동성 → council risk_flag(high_volatility) 증가,
  confidence/quality 게이트로 추격매수 과열 방지.
- **DATA_LOCK** — 시세 데이터 없음 → council HOLD(주문 0건).

## 8. portfolio drift 설명

`compare_positions(broker, audit)` 로 프로그램(audit) 포지션과 broker/Paper 포지션의
수량 불일치를 감지. 불일치가 있으면 신규 주문을 차단/WARN 해야 한다. drift 미감지 시
FAIL.

## 9. daily loss / kill switch 설명

- **DAILY_LOSS_LIMIT** — `DailyLossLimitRule` block_buy + `RiskManager.daily_realized_pnl`
  초과 시 REJECTED. 추가로 운영자에게 `kill_switch_should_trigger=true` *권고*
  (자동 토글 아님 — kill switch 는 운영자 수동 승인, #37).
- **kill switch** — `RiskManager.emergency_stop=True` 시 모든 주문 REJECTED 를 검증
  (`kill_switch_triggered=true`).

> 기존 시스템에 kill switch 가 *이미 있으므로*(#37 3-Level), 본 테스트는 advisory
> 추정이 아니라 *실제 가드*를 검증한다. 단, 손실한도→kill switch 자동연동은
> 미배선이라 그 부분만 `kill_switch_should_trigger`(권고)로 산출한다.

## 10. 실행 명령어

```bash
# 전체 시나리오
python scripts/run_agent_stress_test.py \
    --output reports/stress/latest.json --markdown reports/stress/latest.md

# 단일 시나리오
python scripts/run_agent_stress_test.py --scenario MARKET_CRASH
python scripts/run_agent_stress_test.py --scenario PRICE_STALE --strict
```

산출물 (`--output` 미지정 시 `reports/stress/` 에 timestamp 파일):
- `stress_test_YYYYMMDD_HHMMSS.json`
- `stress_test_YYYYMMDD_HHMMSS.md`

> `reports/` 는 `.gitignore` 등록 — 생성 리포트는 커밋하지 않는다.

exit code: `0` 모든 시나리오 PASS/WARN / `1` FAIL 있음 / `2` 입력·설정 오류.

## 11. 한계

- fixture·합성 입력 기반 — 실제 시장의 모든 미시구조(호가 공백, 부분체결 연쇄)를
  완전히 재현하지 않는다.
- 본 테스트는 *가드의 응답*을 검증하며, 자금 곡선/수익을 시뮬레이션하지 않는다.
- 손실한도→kill switch 자동연동은 권고(`kill_switch_should_trigger`)로만 표시
  (실제 토글은 운영자 수동).

## 12. 실전 전환 승인 아님 · 수익 보장 아님

- `StressTestReport.is_order_signal=False` / `is_live_authorization=False` /
  `auto_apply_allowed=False` / `broker_order_sent=False` / `contains_secret=False`
  불변(dataclass 가드).
- 리포트/스크립트에 "수익 보장" / "실전 전환 승인" 문구 0건(테스트로 lock).
- 실전 전환은 별도 Paper Gate(#44/#72) → Live Capital Review(#41) → Manual
  Approval(#42) → Canary(#43) 게이트 + 운영자 명시 옵트인이 필요하다.

---

## 안전 가드 (정적 grep + dataclass 불변)

- `agent_stress_test.py` / `run_agent_stress_test.py`: KIS / mock broker 어댑터 /
  단일 주문 라우터 / OrderExecutor / paper_trader 모듈 import 0건, broker 주문/취소/
  route 호출 0건, broker 인스턴스 생성 0건, anthropic/openai/httpx/requests import 0건.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 변경 0건, `.env` 작성/갱신 0건.
- secret / API key / 계좌번호 출력 0건. RiskManager / loss_limits / order_quality /
  reconciliation / agent_council 의 *평가* 함수만 read-only 로 호출.
