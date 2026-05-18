# Step 4-07 — AI Paper 매수/매도 판단 연결

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 bridge 는 *advisory* — Paper 가상 체결만, 실 broker 호출 0건.

## 1. 목적

4-05 `PaperStartExplanation` (4-01~4-04 통합 결과) + 현재 가상 포지션 +
`loop_state` 를 입력으로 받아, 2-10 `PaperDecision` (BUY/SELL/HOLD/EXIT/NO_OP)
으로 변환하고 2-09 Paper ledger 에 *advisory* event 로 기록.

**실 broker / OrderExecutor / route_order 호출 0건** — `PaperDecision.is_order_signal=False`
양 끝 lock.

## 2. 구현 위치

| 파일 | 의미 |
|---|---|
| `backend/app/agents/paper_decision_bridge.py` | bridge + dataclass + main builder |
| `backend/app/api/routes_paper_decision_bridge.py` | `POST /api/agents/paper-decision-bridge` |
| `backend/tests/test_paper_decision_bridge.py` | 30 tests across 8 classes |
| `docs/paper_decision_bridge.md` | 본 정책 |

## 3. Gating 매트릭스 (사용자 spec)

| 조건 | 결과 |
|---|---|
| `loop_state == EMERGENCY_STOP` | **모든 action 차단 + ledger 손대지 않음** |
| `loop_state != RUNNING` | trade action 차단 (HOLD/NO_OP audit 만 ledger 기록) |
| `explanation.verdict == DO_NOT_START` | trade action 차단 (blocking_reasons carry) |
| `explanation.verdict in {HOLD, INSUFFICIENT_DATA}` | HOLD audit 만 (BUY/SELL/EXIT 차단) |
| `explanation.verdict in {READY_TO_REVIEW, REVIEW_WITH_WARNING}` + RUNNING | trade 가능 |

**위험 한도 차단** (4-05 시점에 이미 처리되어 본 bridge 가 자동 상속):
- OVERFIT_RISK / STRESS_FAILED / REJECTED_BY_RISK → 4-05 가 `excluded_explanations` 로 분류 → bridge 가 BUY 생성 *불가능* (`bucket=excluded` → `direction=NO_OP`)
- LOW_LIQUIDITY / UNKNOWN regime → 4-05 가 `verdict=DO_NOT_START` → bridge 가 trade 차단

## 4. 변환 규칙 (bucket × position 매트릭스)

| 4-05 bucket | current position | allow_trade | 결과 direction → PaperDecision action |
|---|---|---|---|
| `recommended` | 0 | True (RUNNING+verdict OK) | **BUY** → PAPER_FILLED, virtual_delta=+size |
| `recommended` | 0 | False | HOLD (audit only) |
| `recommended` | >0 | any | HOLD (중복 매수 차단) |
| `watchlist` + `exit_condition=True` | >0 | True | **EXIT** → PAPER_FILLED, virtual_delta=-pos (전량 청산) |
| `watchlist` + `exit_condition=True` | >0 | False | HOLD (audit only) |
| `watchlist` (그 외) | any | any | HOLD |
| `excluded` | any | any | NO_OP (audit only, rationale carry) |

**SELL** action 은 본 bridge 시점에 *직접* 생성되지 않음 — `watchlist + exit_condition`
은 EXIT (전량) 로 처리. 부분 매도 (SELL) 는 후속 PR 에서 별도 흐름 (예:
risk-trim or take-profit ladder) 으로 추가 권고.

## 5. Ledger 연결

각 PaperDecision 은 `record_paper_event()` (2-09) 를 통해 in-memory ring ledger
에 *advisory* event 로 기록:
- `loop_state="RUNNING"` + trade action → ledger.record() 성공
- `loop_state != "RUNNING"` + trade action → `LedgerStateError` raise → `events_blocked` 카운트
- HOLD / NO_OP → state 무관 ledger 기록 가능 (판단 로그)

ledger 기록은 `record=False` 인자로 *변환만* 가능 (test / dry-run).

## 6. API

`POST /api/agents/paper-decision-bridge`:

**요청** (모두 optional):
```jsonc
{
  "market_state":        { "trend_direction": "UP", ... },
  "pre_market":          { "start_allowed": true, "verdict": "READY", ... },
  "positions":           [{ "strategy": "sma_crossover", "symbol": "005930",
                             "quantity": 10, "exit_condition": false }],
  "virtual_trade_size":  1,
  "auto_fill":           true,
  "demote_to_watchlist": false
}
```

**응답** (`BridgeReport.to_dict()`):
```jsonc
{
  "loop_state":          "RUNNING",
  "explanation_verdict": "READY_TO_REVIEW",
  "decisions":           [{ "action": "BUY", "strategy": "...", ... }],
  "decision_count":      1,
  "events_recorded":     1,
  "events_blocked":      0,
  "block_reasons":       [],
  "summary":             "verdict=READY_TO_REVIEW | loop_state=RUNNING | decisions=1 ...",
  "is_order_signal":     false,
  "auto_apply_allowed":  false,
  "is_live_authorization": false,
  ...
}
```

**broker 호출 0건** — endpoint 가 호출하는 모든 흐름은 read+ledger-append only.

## 7. 절대 invariant (테스트로 lock)

| 항목 | 강제 위치 |
|---|---|
| `BridgeReport.is_order_signal=False` | `__post_init__` ValueError |
| `BridgeReport.auto_apply_allowed=False` | 위 |
| `BridgeReport.is_live_authorization=False` | 위 |
| `PaperDecision.is_order_signal=False` | 2-10 dataclass 가드 (상속) |
| broker / OrderExecutor / route_order import 0건 | `TestNoForbiddenImports` |
| 외부 HTTP / AI SDK import 0건 | 정적 grep |
| `OVERFIT_RISK` 전략은 BUY 변환 *불가능* | `test_overfit_risk_blocks_buy` |
| `STRESS_FAILED` BUY 차단 | `test_stress_failed_blocks_buy` |
| `LOW_LIQUIDITY` 장세 BUY 차단 | `test_low_liquidity_blocks_buy` |
| `UNKNOWN` 장세 BUY 차단 | `test_unknown_regime_blocks_buy` |
| pre-market BLOCK 모든 trade 차단 | `test_pre_market_block_blocks_all_trades` |
| EMERGENCY_STOP 모든 action 차단 + ledger 0건 | `test_emergency_stop_blocks_everything` |
| non-RUNNING state 모든 trade 차단 | `test_non_running_state_blocks_trade_actions` (parametrized × 4) |
| schema 에 API key / Secret / 계좌번호 필드 0건 | `test_bridge_report_has_no_secret_fields` |

## 8. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건
- ✅ KIS 주문 API / Anthropic / OpenAI / 외부 HTTP import 0건
- ✅ 실제 매수 / 매도 / Place Order 0건 — *advisory bridge* 전용
- ✅ 안전 flag default 변경 0건
- ✅ AI Agent broker/executor 직접 호출 0건
- ✅ AI 추천은 PaperDecision 으로만 변환 (4-06 guard 와 호환)

## 9. 후속 PR 권고

- 부분 매도 (SELL) 흐름 — risk-trim or take-profit ladder
- 실 시장 데이터 기반 exit_condition 자동 계산 (현재는 caller 가 명시 전달)
- ledger 영구화 (DB / JSONL) — 현재는 in-memory ring
- frontend 카드 통합 — 본 PR 은 backend bridge + API 만. AutoPaperLoopCard
  (2-09/2-10 ledger UI) 가 이미 최근 decision 표시
