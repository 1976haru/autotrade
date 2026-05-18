# Step 4-Loop-09 — Auto Loop consumes Agent recommendations

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 흐름은 *advisory* — Paper 가상 체결 + AgentDecisionLog 기록만, 실 broker
> 호출 0건.

## 1. 목적

`AutoPaperLoop` 가 RUNNING 상태일 때 *매 tick* 마다 다음 단계가 1회 이상
실행되도록 한다:

```
Agent recommendation (provider)
   → PaperStartExplanation (4-05 통합)
   → PaperDecisionBridge (4-07)  →  Risk veto (4-09) + Position sizing (4-08)
   → Paper ledger 기록 (2-09)
   → AgentDecisionLog 기록 (4-10)
   → AutoPaperStatus 에 cycle 요약 carry → UI 표시
```

운영자가 매 tick 에 Agent 추천을 *어떻게* 생성하는지는 caller 가 주입한
`recommendation_provider` 가 결정한다 — 본 모듈은 LLM / Anthropic / OpenAI 를
호출하지 않으며, 결정론적 stub 또는 사전 분석 결과만 받아 PaperDecision 으로
변환한다.

## 2. 구현 위치

| 파일 | 의미 |
|---|---|
| `backend/app/auto_paper/agent_consumer.py` | `consume_agent_recommendations()` + `ConsumerResult` + `null_recommendation_provider` + `build_deterministic_explanation` (테스트/dev 전용 stub) |
| `backend/app/auto_paper/loop.py` | `AutoPaperLoop(agent_consumer_runner=...)` + `set_agent_consumer_runner()` + 5개 last_consumer 상태 필드 |
| `backend/tests/test_auto_paper_loop_consumes_agent.py` | 25 backend tests |
| `frontend/src/components/tabs/AutoPaperLoopCard.jsx` | 최근 tick / 최근 판단 / ledger / AgentDecisionLog 카운트 + Paper 전용 배지 |
| `frontend/src/components/tabs/AutoPaperLoopCard.test.jsx::#4-Loop-09 consumer strip` | 5 frontend tests |
| `docs/auto_loop_agent_consumption.md` | 본 정책 |

## 3. tick() 흐름

```python
class AutoPaperLoop:
    def __init__(self, ..., agent_consumer_runner=None):
        self._agent_consumer_runner = agent_consumer_runner
        # last cycle state for status() / UI:
        self._last_consumer_consumed = False
        self._last_consumer_decision_count = 0
        self._last_consumer_action = None
        self._last_consumer_ledger_events = 0
        self._last_consumer_decision_log_count = 0

    def tick(self):
        # 1. RUNNING 가드 — 다른 상태에서는 LoopNotRunningError.
        # 2. cycle_count += 1, last_tick_at 갱신.
        # 3. paper_tick_handler 호출 (#2-06).
        # 4. agent_consumer_runner(loop_state, now) 호출 →
        #    ConsumerResult 의 카운터를 last_consumer_* 에 carry.
        # 5. _snapshot_unlocked() 반환 → 상태 + consumer 요약.
```

**runner 실패 정책**: consumer 가 raise 해도 `tick()` 은 raise 하지 않는다 —
cycle 은 정상 증가, `last_error` 에 사유 카운트, last_consumer_* 는 직전 값
보존. 운영 흐름이 단발 실패로 중단되지 않도록 의도된 방어.

## 4. ConsumerResult 응답 schema

```python
@dataclass(frozen=True)
class ConsumerResult:
    cycle_at:           str
    schema_version:     str
    consumed:           bool                       # provider 가 explanation 반환했는지
    explanation_verdict: str | None
    decision_count:     int
    ledger_events:      int                        # 2-09 ring ledger 신규 row
    ledger_blocked:     int
    decision_log_count: int                        # 4-10 AgentDecisionLog 신규 row
    by_action:          dict[str, int]             # {BUY: 1, HOLD: 2, ...}
    block_reasons:      list[str]
    summary:            str
    metadata:           dict[str, Any]
    is_order_signal:       bool = False    # 영구 lock
    auto_apply_allowed:    bool = False
    is_live_authorization: bool = False
```

## 5. provider contract — 운영자 책임

| 항목 | 정책 |
|---|---|
| 입력 | `(now: datetime) → PaperStartExplanation \| None` |
| 반환 None | "데이터 부족" — 본 cycle 0 decision (안전 fallback) |
| 반환 PaperStartExplanation | bridge → ledger + AgentDecisionLog 실행 |
| LLM / Anthropic / OpenAI 호출 | **불가** — 본 모듈은 SDK import 0건. provider 가 별도 PR 에서 옵트인으로 wrapper 추가 시 별도 정책 적용 |
| 외부 HTTP | **불가** — provider 가 read-only DB / 결정론적 분석만 사용 |
| 부수효과 | 없어야 함 — explanation 객체만 생성, 추가 broker 호출 0건 |

기본 provider 는 `null_recommendation_provider` — 항상 None 반환. 운영자가
명시 provider 를 주입하지 않으면 *자동 BUY/SELL 생성 경로가 0건* 인 안전한 기본.

## 6. AutoPaperStatus 확장 필드

| 필드 | 의미 | UI 표시 |
|---|---|---|
| `last_consumed` | 직전 cycle 에서 provider 가 explanation 반환했는지 | (boolean carry) |
| `last_decision_count` | 직전 cycle 에서 생성된 PaperDecision 수 | "판단 수: N" |
| `last_decision_action` | 직전 cycle 의 가장 흔한 action (BUY/HOLD/EXIT 등) | 컬러 라벨 (button 아님) |
| `last_ledger_events` | 직전 cycle 에서 ledger 에 INSERT 된 row 수 | "ledger 기록: N" |
| `last_decision_log_count` | 직전 cycle 에서 AgentDecisionLog 에 INSERT 된 row 수 | "AgentDecisionLog 기록: N" |

## 7. 절대 invariant (테스트로 lock)

| 항목 | 강제 위치 |
|---|---|
| `ConsumerResult.is_order_signal=False` | `__post_init__` ValueError |
| `ConsumerResult.auto_apply_allowed=False` | 위 |
| `ConsumerResult.is_live_authorization=False` | 위 |
| RUNNING 이 아닌 상태에서 tick() → consumer NOT 호출 | `test_non_running_state_blocks_tick` (4 parametrized) |
| RUNNING + provider → PaperDecision + ledger + AgentDecisionLog 동시 | `test_running_tick_invokes_consumer_and_persists` |
| risk_flag stale_data → BUY downgraded to HOLD + log carries veto | `test_consumer_with_risk_veto_records_hold` |
| provider error → tick() 정상 종료, last_error captured | `test_consumer_exception_does_not_break_loop` |
| runtime injection 가능 | `test_set_agent_consumer_runner_replaces_runner` |
| 다중 tick → N rows 누적 | `test_multiple_ticks_accumulate_log_rows` |
| broker / OrderExecutor / route_order import 0건 | `TestStaticGuards` |
| Anthropic / OpenAI / httpx / requests import 0건 | 위 |
| 본 모듈 자체에 `db.add` / `session.add` / `session.commit` 0건 (bridge 가 단일 진입점) | `test_no_db_write_outside_bridge` |
| `settings.enable_*` mutation 0건 | `test_no_settings_mutation` |
| `null_recommendation_provider` 항상 None | `test_null_provider_returns_none` |
| Frontend: BUY/SELL/EXIT 은 `<strong>` 라벨, `<button>` 0개 | `consumer strip` block |
| Frontend: "Paper 전용 · 실제 주문 아님" 영구 배지 | 위 |
| Frontend: "Place Order" / "지금 매수" / "ENABLE_LIVE_TRADING" 텍스트 0건 | 위 |

## 8. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건 (정적 AST 가드)
- ✅ KIS / Anthropic / OpenAI / 외부 HTTP / `httpx` / `requests` import 0건
- ✅ 본 모듈은 DB write 0건 — bridge → 4-10 모듈이 단일 INSERT 진입점
- ✅ 안전 flag default 변경 0건 (`ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
  `ENABLE_FUTURES_LIVE_TRADING` / `KIS_IS_PAPER` 그대로)
- ✅ secret 필드 0건 — sanitizer fail-closed
- ✅ `mode="PAPER"` carry (4-10 모듈이 강제)
- ✅ Frontend "지금 매수" / "Place Order" / "ENABLE_*" 라벨 button 0개

## 9. 후속 PR 권고

- **운영자 provider 구현** — 운영자 PC 의 결정론적 분석 + DB 조회 기반
  `PaperStartExplanation` 생성기 (별도 PR + 명시 옵트인).
- **scheduler 통합** — `AutoPaperLoop` 의 백그라운드 task 가 `tick_interval_sec`
  마다 자동 `tick()` 호출 (현재는 API `POST /api/auto-paper/tick` 수동 호출).
- **chain_id 검색 UI** — 한 cycle 의 모든 row 를 한 view 에 노출.
- **AgentDecisionLog 정리 정책** — append-only 유지, 별도 archive flag 검토.
- **LIVE 흐름** — 본 PR 은 PAPER 전용. LIVE 자동 실행은 별도 게이트 (#45).
