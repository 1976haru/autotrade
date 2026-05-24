# Agent 성과 대시보드 + AI 판단 설명 가능성 (#49 / 6-04 + #52 / 6-07)

EXE 대시보드에서 (A) AI/Agent **성과 개선 포인트**(전략별 승률·손익비·MDD·
expectancy·체결 실패율·차단 사유)와 (B) **AI 판단 설명**(왜 매수/매도/보류했는지)을
한눈에 확인할 수 있도록 보강한 read-only 표시 기능.

> ⚠️ 본 작업은 **UI/설명/분석 표시 보강**이다. 실제 주문을 생성하지 않으며, KIS
> API 를 호출하지 않고, 실전 기능을 켜지 않는다. **성과가 좋아 보여도 live
> promotion 으로 연결되지 않으며, 수익을 보장하지 않는다.**

관련 코드:
- 백엔드(read-only):
  - `backend/app/agents/order_quality_metrics.py` — 체결 실패율/거절률/부분체결률 +
    차단 사유 TOP 집계.
  - `backend/app/agents/decision_explanation.py` — entry/counter/exit/risk/veto/
    sell 설명 유도 (fallback 포함).
  - endpoints (`routes_agents.py`): `GET /api/agents/order-quality-metrics`,
    `POST /api/agents/decision-explanation`, `GET /api/agents/decision-explanation/{id}`.
- 프론트엔드:
  - `frontend/src/components/common/PerformanceMetricsSummary.jsx` (#49)
  - `frontend/src/components/common/AgentDecisionExplanationCard.jsx` (#52)
  - `frontend/src/utils/decisionExplanation.js` (client-side 설명 유도 미러)
  - AISignal 탭에 마운트 (기존 `PerformanceDashboard`(P-28) 와 함께).
- 테스트: `backend/tests/test_agent_decision_explanation.py`,
  `frontend/.../PerformanceMetricsSummary.test.jsx`,
  `frontend/.../AgentDecisionExplanationCard.test.jsx`.

## 1. 성과 대시보드 목적 (#49 / 6-04)

전략별 성과와 주문 품질을 기간별로 보여 **어디를 개선해야 하는지** 파악한다. 기존
P-28 `PerformanceDashboard` 는 승률/평균/손익비/PF/MDD + Agent Council vs 단일
전략을 이미 보여준다. 본 PR 의 `PerformanceMetricsSummary` 는 P-28 이 빠뜨린
**expectancy + 체결 실패율 / 거절률 / 부분체결률 + 차단 사유 TOP** 를 한 카드에
요약해 보강한다.

## 2. 기간별 성과 보는 법

- `agentStrategyPerformance({limit})` / `agentOrderQualityMetrics({limit})` 의
  `limit` 이 분석 대상 episode 수(기간 proxy)다. limit 을 키우면 더 긴 기간을 집계.
- decision episode 기록 기반 *추정* 이며 **실제 계좌 잔고가 아니다**.

## 3. 승률 / 손익비 / PF / MDD / expectancy

- **win_rate** 이익 거래 / 전체. **payoff_ratio** 평균수익/|평균손실|.
- **profit_factor** 총이익/|총손실| (손실 0건이면 —). **max_drawdown** 최대 낙폭.
- **expectancy** 1거래 기대값(= 승률·평균수익 + 패율·평균손실). 0 이하면 개선 필요.
- 전략별로 ORB / MOMENTUM / GAP / VWAP / **AGENT_COUNCIL** 비교.

## 4. 체결 실패율 / 거절률 / 부분체결률 / 차단 사유 TOP

`order-quality-metrics` 가 episode 의 `order_quality_summary`(P-24)에서 집계:
- **order_failure_rate** = (REJECTED + ERROR) / 주문 시도 수.
- **rejected_rate** = REJECTED / 주문 시도 수.
- **partial_fill_rate** = PARTIALLY_FILLED / 주문 시도 수.
- **fill_rate** = FILLED / 주문 시도 수. + **avg_slippage_bps**.
- **blocked_reasons_top** — 주문이 차단/거절/보류된 주된 사유 코드 빈도 TOP-N.
  우선순위: 주문 거절 사유 > RiskOfficer veto > exit_plan 검증 실패 > HOLD reason_code.

## 5. Agent Council vs best single

`agent_vs_single` 의 `agent_outperforms_best_single` / `best_single_strategy` /
PF 비교로 Agent Council 결합이 최고 단일 전략보다 나은지 표시한다(분석 비교일 뿐
실전 근거 아님).

## 6. AI 판단 설명 (#52 / 6-07)

`AgentDecisionExplanationCard` 가 episode.council(또는 council dict)의 *이미 있는*
필드에서 사람이 읽는 설명을 유도한다. 새로운 판단 로직을 만들지 않는다.

표시 항목:
- **최종 판단 + 이유** (final_action / reason)
- **entry_reason** — 최종 방향과 같은 신호를 낸 전략 + 근거 (예: "MOMENTUM, VWAP 가
  같은 방향(매수) 신호 — 상승 모멘텀")
- **counter_reason** — 반대 방향 전략의 근거 (예: "GAP(SELL): 과열 위험")
- **exit_plan** — 손절/익절/RR (예: "청산 계획: 손절 2%, 익절 3%, RR 1.5")
- **risk_flags** — (예: "리스크 플래그: PRICE_STALE, HIGH_VOLATILITY")
- **risk_veto_result** — RiskOfficer veto 시 (예: "RiskOfficer veto: 위험 플래그
  초과 → HOLD 강등")
- **exit_plan_validation** — BUY 차단 시 (예: "BUY 차단: exit_plan invalid")
- **sell_reason** — SELL 시 (STOP_LOSS / TAKE_PROFIT / TRAILING_STOP / MARKET_CLOSE_EXIT)
- selected_strategies / quality_score / confidence / market_regime / time_phase

### fallback (구버전 episode 호환)

설명 필드가 없어도 **에러 없이** 친절한 문구로 대체한다:
- entry_reason 없음 → "진입 근거 미기록"
- counter_reason 없음 → "반대 근거 미기록"
- exit_plan 없음 → "청산 계획 미기록"
- risk_flags 없음 → "위험 플래그 없음"
- risk_veto 없음 → "RiskOfficer veto 없음"

백엔드 helper 와 프론트엔드 `utils/decisionExplanation.js` 는 동일한 유도 규칙을
사용한다(미러). 카드는 `decision`(council) prop 이 있으면 client-side 로 유도하고,
없으면 최신 episode 를 read-only 로 가져와 표시한다.

## 7. 모든 표시는 read-only — 주문/실전/승인 아님

- 두 카드 모두 **매수/매도/실전/승인/Place Order 버튼 0개, 입력 form 0개**
  (테스트로 lock).
- 필수 문구: "이 화면은 분석/설명(표시) 전용입니다", "주문 버튼이 아닙니다",
  "실전 전환 승인과 무관합니다", "수익을 보장하지 않습니다".
- 백엔드 응답 invariant: `is_order_signal=False` / `is_live_authorization=False` /
  `contains_secret=False`.

## 8. 안전 가드 (정적 grep + dataclass 불변)

- `decision_explanation.py` / `order_quality_metrics.py`: broker / OrderExecutor /
  단일 주문 라우터 / paper_trader / KIS 어댑터 / anthropic / openai / httpx /
  requests import 0건, broker 주문/취소/route 호출 0건, **DB write 0건**
  (read-only), secret/계좌 출력 0건.
- 신규 endpoint 는 read-only — broker 호출 0건, DB write 0건.
- 안전 flag (`ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
  `ENABLE_FUTURES_LIVE_TRADING` / `KIS_IS_PAPER`) 변경 0건, `.env` 변경 0건.
- 본 PR 은 Agent 판단 로직을 변경하지 않는다 — 기존 council 결과를 *설명/표시*만 한다.
