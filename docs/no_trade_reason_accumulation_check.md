# 거래 없을 때 사유 누적 (3-09)

자동매매 tick/cycle 에서 **실제 주문이 발생하지 않았을 때도** "왜 거래가
없었는지" 를 명확히 기록·집계한다. 이를 통해 사용자는 *"시스템이 멈춘
것인지, 조건이 없어서 쉰 것인지"* 를 구분할 수 있다.

> 본 문서는 운영 확인용이며 주문 신호가 아니다. 거래 없음은 *오류가 아닐 수
> 있다*. 실거래 활성화 절차가 아니다.

## 표준 no-trade reason_code (한국어)

| reason_code | 한국어 |
|---|---|
| NO_SIGNAL | 조건에 맞는 매수/매도 신호가 없어 거래하지 않음 |
| NO_STRATEGY_SIGNAL | 전략 신호가 없어 거래하지 않음 |
| NO_MARKET_DATA | 시장 데이터가 없어 거래하지 않음 |
| MARKET_CLOSED | 장 시간이 아니어서 거래하지 않음 |
| PRICE_STALE | 현재가가 오래되어 거래하지 않음 |
| INVALID_PRICE | 현재가가 비정상이라 거래하지 않음 |
| BLOCKED_BY_RISK_MANAGER | 리스크 조건으로 거래 차단 |
| BLOCKED_BY_PERMISSION_GATE | 주문 권한 조건으로 거래 차단 |
| BLOCKED_BY_KIS_READINESS | KIS 모의투자 준비 상태가 충족되지 않아 거래하지 않음 |
| PAPER_EXECUTION_DISABLED | Paper 가상 실행이 비활성화되어 거래하지 않음 |
| EXIT_PLAN_MISSING | 손절/익절 계획이 없어 매수 차단 |
| EXIT_PLAN_INVALID | 손절/익절 계획이 유효하지 않아 매수 차단 |
| RISK_FLAGS_EXCEEDED | 위험 플래그 초과로 HOLD |
| DUPLICATE_POSITION_BUY_BLOCKED 등 buy block | (P-17 / 3-08 차단 사유 carry) |

raw code (HOLD / NO_OP / RISK_FLAGS_EXCEED / KIS_NOT_READY / PAPER_GUARD_* /
KIS_PAPER_* 등) 는 위 canonical code 로 정규화되어 동일 한국어로 표시된다.

## 집계 모델 (`app/auto_paper/no_trade_reasons.py`)

`summarize_no_trade_reasons(events, *, limit)` → `NoTradeSummary`:

- `order_count`: PAPER_FILLED 체결 event 수.
- `no_trade_count`: 거래 없음 cycle 수 (체결 외 전부 — HOLD / NO_OP /
  PAPER_REJECTED / PENDING / CANCELLED).
- `cycle_count = order_count + no_trade_count` — **주문이 없어도 증가**.
- `by_reason` / `by_no_trade_reason`: reason_code 별 count.
- `last_no_trade` / `last_no_trade_reason` / `last_no_trade_symbol`: 가장 최근.

각 no-trade record 는 `broker_order_no=None`, `broker_order_sent=False` 를
carry — **거래가 없는데 성공 주문처럼 표시하지 않는다**.

## 절대 invariant (코드 단 + 테스트로 lock)

- `NoTradeSummary.is_order_signal=False` / `auto_apply_allowed=False` /
  `is_live_authorization=False` / `contains_secret=False` 불변 (dataclass
  `__post_init__` ValueError 가드).
- 거래 없음 record 에 `broker_order_no` 0건, `broker_order_sent=true` 0건,
  `KIS_PAPER_SUBMITTED` 미발생.
- 본 모듈은 broker / OrderExecutor / route_order / anthropic / openai / httpx /
  requests import 0건, DB write 0건 (정적 grep + AST 가드).
- secret / API key / 계좌번호 carry 0건 — detail 은 안전 키 allowlist 만.

## API (read-only)

`GET /api/auto-paper/no-trade-reasons/today?limit=N&all_events=false`

응답 예:
```json
{
  "cycle_count": 120,
  "order_count": 2,
  "no_trade_count": 118,
  "total_no_trade": 118,
  "by_reason": { "NO_SIGNAL": 100, "MARKET_CLOSED": 15, "BLOCKED_BY_RISK_MANAGER": 3 },
  "by_no_trade_reason": { "...": 0 },
  "recent": [ ... ],
  "last_no_trade": { "reason_code": "NO_SIGNAL", "symbol": "005930", "broker_order_sent": false, "broker_order_no": null },
  "last_no_trade_reason": "NO_SIGNAL",
  "last_no_trade_symbol": "005930",
  "contains_secret": false,
  "is_live_authorization": false
}
```
broker 호출 0건, audit row 0건, DB write 0건.

## UI 표시

- **AutoPaperLoopCard** (`auto-paper-no-trade-summary`): cycle_count /
  no_trade_count / order_count + 최근 거래 없음 사유(한국어 + 코드) + reason 별
  count 요약 + "거래 없음은 오류가 아닐 수 있습니다 — 사유를 확인하세요" +
  "broker_order_sent=false · is_live_authorization=false" disclaimer. 헤더에
  `no-trade-count` pill.
- **PaperDecisionLogCard / DecisionEpisodeCard**: HOLD / BLOCKED / REJECTED row
  의 reason_code + 사유 표시 (기존 P-17 / P-21 흐름), broker_order_no 미발생.

UI 에 실전/매수/매도/Place Order/자동 재주문 라벨 버튼 0개, secret/계좌 표시
0건 (frontend 테스트로 lock).

## EXE 확인 순서

1. 장중 자동 tick 실행 (AutoPaperLoop RUNNING).
2. 조건이 없을 때 — 헤더 `거래없음 N` 증가 + "최근 거래 없음: 조건에 맞는
   매수/매도 신호가 없어 거래하지 않음 (NO_SIGNAL)" 표시.
3. 시장 데이터 없을 때 — NO_MARKET_DATA 사유 표시 확인.
4. 장 시간 외 — MARKET_CLOSED 사유 표시 확인.
5. Risk Gate 차단 시 — BLOCKED_BY_RISK_MANAGER / BLOCKED_BY_PERMISSION_GATE 등.
6. `cycle_count` 가 주문 없어도 증가 + `no_trade_count` 증가 확인.
7. reason 별 count 요약에서 NO_SIGNAL 등 분포 확인.
8. Decision Episode / 로그 탭에서 HOLD/BLOCKED row 의 reason_code 확인.
9. **KIS 모의투자 화면** — 거래 없음 cycle 은 주문 0건.
10. **실전 계좌** — 주문 0건.

## 코드 단 보장 (테스트로 lock)

- `backend/tests/test_no_trade_reason_accumulation.py` — HOLD/NO_OP/REJECTED 기록,
  13개 reason_code 저장, buy block carry, cycle/order/no_trade 카운트, by_reason
  집계, last 추적, broker_order_no 없음 / broker_order_sent=false /
  KIS_PAPER_SUBMITTED 미발생, invariant, 정적 import/DB-write 가드, API endpoint.
- `frontend/src/utils/noTradeReasons.test.js` — 필수 한국어 문구 + alias + 안전 처리
  + 금지 문구 0건.
- `frontend/src/components/tabs/AutoPaperLoopCard.test.jsx` — no_trade_count 표시,
  요약/최근 사유/reason 별 count/ disclaimer, 금지 버튼 0개, secret 미표시.
