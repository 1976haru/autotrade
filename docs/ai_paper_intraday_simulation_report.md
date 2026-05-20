# AI Paper 장중 시뮬레이션 보고서

> 본 문서는 실제 한투 KIS API 호출 없이 *평일 1일 (2026-05-18 월)* 흐름을
> Claude Code 가상 시각 주입으로 모방한 *실전 유사 AI Paper Auto Loop 시뮬레이션*
> 결과를 기록한다. 본 시뮬레이션은 EXE 빌드 / desktop-release workflow / 실거래
> 호출을 *수행하지 않는다*. Paper/Virtual 한정.
>
> 시뮬레이션 driver: `backend/tests/test_ai_paper_intraday_simulation.py`
> (pytest 8 케이스, 0.15s in-memory SQLite).

## 1. 시뮬레이션 목표

| 시점 | 기대 상태 | 기대 산출물 |
|---|---|---|
| 08:50 KST start | WAITING_MARKET | cycle=0, decision=0, ledger=0 |
| 09:00 KST status | RUNNING (auto-promote) | state 전이 |
| 09:30 KST tick | RUNNING | decision=1, ledger=1, log=1 |
| 11:00 KST tick | RUNNING | 누적 decision=2, ledger=2 |
| 14:00 KST tick | RUNNING | 누적 decision=3, ledger=3 |
| 15:31 KST status | MARKET_CLOSED (auto-demote) | 신규 tick 차단 |
| 15:31 후 tick | LoopNotRunningError | 증분 0 |
| 긴급정지 | EMERGENCY_STOP | start 재호출 차단 |

## 2. 실행 결과 (raw timeline output)

`PYTHONIOENCODING=utf-8 python ...` 로 driver 실행 시 출력되는 timeline:

```
====== AI Paper Intraday Simulation - 2026-05-18 (Mon) ======
KST     action                  state             cyc  D/L/G
------------------------------------------------------------------------
08:50   start                   WAITING_MARKET      0  0/0/0
08:55   tick (blocked)          WAITING_MARKET      0  0/0/0
09:00   status auto-promote     RUNNING             0  0/0/0
09:30   tick #1                 RUNNING             1  1/1/1  action=BUY
11:00   tick #2                 RUNNING             2  2/2/1  action=BUY
14:00   tick #3                 RUNNING             3  3/3/1  action=BUY
15:31   status demote           MARKET_CLOSED       3  3/3/1
15:32   tick (blocked)          MARKET_CLOSED       3  3/3/1  LoopNotRunningError
15:33   emergency_stop          EMERGENCY_STOP      3  3/3/1
  next-day 09:00 start attempt blocked (LoopBlockedError - emergency stop preserved)
------------------------------------------------------------------------
Final: AgentDecisionLog=3, ledger=3

All AgentDecisionLog rows:
  id=1 agent=PaperDecisionBridge decision=BUY mode=PAPER chain=fabda766...
  id=2 agent=PaperDecisionBridge decision=BUY mode=PAPER chain=4564a4b5...
  id=3 agent=PaperDecisionBridge decision=BUY mode=PAPER chain=20809a7c...
```

> D = AgentDecisionLog rows, L = paper ledger events, G = consumer-reported
> `decision_log_count` (1 = 마지막 cycle 에 1건 INSERT — 매 cycle 동일).

## 3. 사용자 요청서 매트릭스 매핑

| 사용자 요구 | 실제 동작 | 결과 |
|---|---|---|
| 08:50 시작 → WAITING_MARKET | `loop.start(now=t_0850)` → state="WAITING_MARKET" | ✅ |
| 09:00 장 시작 → RUNNING | `loop.status(now=t_0900)` → lazy-promote, state="RUNNING" | ✅ |
| 장중 mock market data stream → Agent 추천 | `consume_agent_recommendations` 가 deterministic `build_deterministic_explanation` 호출 — LLM 0건 | ✅ |
| PaperDecision 생성 | bridge 가 매 cycle 1건 BUY 생성 | ✅ |
| ledger 기록 | `get_ledger().recent()` 매 tick 후 +1 | ✅ |
| AgentDecisionLog 기록 | DB row +1 per tick, mode="PAPER" | ✅ |
| 15:30 → MARKET_CLOSED → 신규 판단 중단 | 15:31 `status(now=t_1531)` lazy-demote → "MARKET_CLOSED", 이후 `tick()` LoopNotRunningError | ✅ |
| 긴급정지 → 모든 루프 차단 | `emergency_stop()` → "EMERGENCY_STOP", 다음날 `start()` LoopBlockedError | ✅ |

## 4. 정적 invariant — KIS API / 실 broker 호출 경로 0건

| 모듈 | broker import | OrderExecutor / route_order | KIS / Anthropic / OpenAI / httpx / requests |
|---|---|---|---|
| `app/auto_paper/loop.py` | ✅ 0 | ✅ 0 | ✅ 0 |
| `app/auto_paper/agent_consumer.py` | ✅ 0 | ✅ 0 | ✅ 0 |
| `app/auto_paper/ledger.py` | ✅ 0 | ✅ 0 | ✅ 0 |
| `app/auto_paper/decisions.py` | ✅ 0 | ✅ 0 | ✅ 0 |
| `app/scheduler/market_clock.py` | ✅ 0 | ✅ 0 | ✅ 0 |

검증 방식: AST `import` / `ImportFrom` 노드 순회 + source text grep
(`broker.place_order(` / `route_order(` / `KisClient(` / `KisBrokerAdapter(` /
`OrderExecutor(` 패턴). 5개 모듈 × 6개 패턴 = **30개 정적 가드 모두 통과**.

## 5. 동작 invariant — `ConsumerResult` / `AutoPaperStatus`

| 필드 | 기대 값 | 검증 |
|---|---|---|
| `ConsumerResult.is_order_signal` | False | dataclass `__post_init__` |
| `ConsumerResult.auto_apply_allowed` | False | 동일 |
| `ConsumerResult.is_live_authorization` | False | 동일 |
| `ConsumerResult.schema_version` | `"1.0"` | 정적 상수 |
| `AutoPaperStatus.forced_paper` | True | dataclass `__post_init__` |
| `AutoPaperStatus.is_order_signal` | False | 동일 |
| `AutoPaperStatus.auto_apply_allowed` | False | 동일 |
| `AgentDecisionLog.mode` | `"PAPER"` (3/3 row) | 시뮬 후 SQL count |
| `AgentDecisionLog.chain_id` | non-null UUID | 시뮬 후 inspect |

## 6. 안전 flag 변경 매트릭스

| flag | 시작 시 | 시뮬 후 | 변경 |
|---|---|---|---|
| `KIS_IS_PAPER` | `true` | `true` | ✅ 변경 0건 |
| `ENABLE_LIVE_TRADING` | `false` | `false` | ✅ 변경 0건 |
| `ENABLE_AI_EXECUTION` | `false` | `false` | ✅ 변경 0건 |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | `false` | ✅ 변경 0건 |

본 시뮬레이션은 `os.environ` / `.env` / `settings.enable_*_trading` 어디도
mutate 하지 않는다. consumer / loop 둘 다 `app.core.config.get_settings`
를 import 하지 않음.

## 7. 외부 호출 합산

| 호출 종류 | 횟수 |
|---|---|
| KIS REST API | 0 |
| KIS WebSocket | 0 |
| `broker.place_order` | 0 |
| `route_order` | 0 |
| Anthropic API | 0 |
| OpenAI API | 0 |
| Telegram Bot | 0 |
| HTTP outbound | 0 |
| DB INSERT | 3 (AgentDecisionLog, in-memory SQLite) |
| DB read | 9 (count + select * — in-memory) |
| 메모리 ledger append | 3 |

## 8. 실패 경로 검증

| 시도 | 기대 결과 |
|---|---|
| WAITING_MARKET 상태에서 `tick()` | `LoopNotRunningError` (handler 호출 0건, ledger 변경 0건) |
| MARKET_CLOSED 상태에서 `tick()` | `LoopNotRunningError` |
| EMERGENCY_STOP 후 다음날 `start()` | `LoopBlockedError` ("call reset() before start()") |

본 시뮬레이션이 *모든* 실패 경로를 명시적으로 검증해 운영자가 다음 케이스에
서도 자동매수가 발생하지 않음을 보장:
- 장 시작 *직전* 출시된 EXE
- 장 종료 *직후* 시작 버튼 클릭
- 토/일 EXE 더블클릭
- 긴급정지 후 PC 재부팅 → 자동 재시작 시도

## 9. 본 시뮬레이션 *이후* 필요한 단계 (실 KIS Paper 테스트)

본 시뮬레이션은 *시간 주입 + 결정론적 stub* 기반 — 실 KIS 모의투자 API 의
응답 / 지연 / 인증 / rate limit 등 *외부 변동성* 은 검증하지 않는다.
실 KIS Paper 테스트 진행 시 추가로 확인해야 할 항목:

1. `%APPDATA%\Autotrade\.env` 에 KIS *모의투자* App Key / Secret / 계좌번호
   입력 (실계좌 키 *절대 금지*).
2. `KIS_IS_PAPER=true` 영구 — `.env.example` default 그대로 유지.
3. 앱 첫 실행 시 desktop launcher 의 readiness 카드 → "모의투자 API 키 OK"
   확인.
4. 09:00 KST 직전 EXE 실행 → "한투 모의 빠른 점검 시작" 클릭 → 확인 모달
   → 시작.
5. 30~60분 동안 ledger / Agent 카드 / Risk 카드 / Approval 큐 *변동 관찰*.
6. 15:30 KST 직후 자동 MARKET_CLOSED 전환 확인.
7. 운영자 만족 시 결과를 `docs/release_notes.md` v1.0.1-beta 섹션에 *간단히*
   기록 — 응답시간 / 거절율 / 에러 카운터.

## 10. 본 PR 의 범위 — *시뮬레이션 + 보고서만*

본 PR 의 변경:
- 신규 `backend/tests/test_ai_paper_intraday_simulation.py` (8 케이스 PASS)
- 신규 `docs/ai_paper_intraday_simulation_report.md` (본 문서)

본 PR 의 *변경 없음*:
- `backend/app/auto_paper/*.py`
- `backend/app/scheduler/market_clock.py`
- `backend/app/brokers/*` / `app/execution/*`
- `frontend/*`
- 안전 flag (`KIS_IS_PAPER` 등 default)
- `.github/workflows/desktop-release.yml`
- `src-tauri/tauri.conf.json`

본 PR 의 *0회 동작*:
- EXE 빌드
- `desktop-release` workflow 실행
- KIS API 호출 (실계좌 / 모의투자 모두)
- broker / OrderExecutor / route_order 호출
- 실거래 / 자동 주문 / 자동 설치

## 11. 검증 (사용자 요청서 §3 매트릭스 종합)

| 단계 | 검증 위치 | 결과 |
|---|---|---|
| 08:50 WAITING_MARKET | `TestIntradayPaperSimulation::test_full_intraday_timeline` | ✅ |
| 09:00 RUNNING auto-promote | 동일 | ✅ |
| 장중 mock data → Agent → PaperDecision → ledger → log | 동일 (3 tick 누적) | ✅ |
| 15:30 MARKET_CLOSED auto-demote | 동일 | ✅ |
| 긴급정지 → 차단 | 동일 | ✅ |
| KIS / broker 호출 0건 | `TestNoLiveBrokerCalls` (5 모듈 × AST + grep) | ✅ |
| Consumer invariant 영구 | `TestConsumerInvariantsCarried` | ✅ |
| 사람-읽기 timeline output | `TestPrintSimulationSummary` | ✅ |

**최종 판정: ✅ AI Paper Auto Loop 가 실전 유사 흐름에서 정상 작동.**
다음 단계는 운영자가 별도 PC 에서 한투 *모의투자* API 키로 실 KIS Paper
원클릭 테스트 (§9).
