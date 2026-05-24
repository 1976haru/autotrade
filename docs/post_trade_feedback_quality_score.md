# PostTradeReview 피드백 루프 + Agent decision quality_score 고도화 (#50 / 6-05 + #51 / 6-06)

거래 후 복기 결과를 다음 판단에 *참고* 하고(피드백 루프), 무조건 매수를 방지하도록
판단 품질(quality_score)을 고도화해 quality 가 낮으면 HOLD 로 강등한다.

> ⚠️ 본 작업은 **Agent 판단 품질 개선** 작업이다. 실제 주문을 보내지 않으며, KIS
> API 를 호출하지 않고, 실전 기능을 켜지 않는다. **복기 결과/quality_score 만으로
> 실전 전환을 허용하거나 주문을 강제 생성하지 않으며, 수익을 보장하지 않는다.**

관련 코드:
- `backend/app/agents/post_trade_feedback.py` (#50) — 피드백 루프 집계 + threshold 추천
- `backend/app/agents/decision_quality.py` (#51) — 고도화 quality breakdown + HOLD 게이트
- `backend/app/agents/agent_council.py` — `run_agent_council` 에 quality gate 통합
- endpoints (`routes_agents.py`): `GET /api/agents/feedback-loop`,
  `POST /api/agents/decision-quality`
- 프론트엔드: `common/PostTradeFeedbackCard.jsx`, `common/DecisionQualityScoreCard.jsx`
  (AISignal 탭 마운트)
- 테스트: `test_post_trade_feedback_loop.py`, `test_agent_decision_quality_score.py`,
  `PostTradeFeedbackCard.test.jsx`, `DecisionQualityScoreCard.test.jsx`

## 1. PostTradeReview 피드백 루프 목적 (#50)

복기(P-27)/outcome(P-25) 집계(`summarize_episodes`)에서 승패 요인·전략 오류·과잉진입·
늦은 청산·데이터 품질·장세 부적합을 *피드백 태그* 로 모으고, 동일 실패 요인이
반복되면 *threshold 조정 후보* 를 만든다. 피드백은 다음 판단에 참고되는 분석
정보이며, **자동으로 실전 전환을 허용하거나 주문을 생성하지 않는다.**

### 피드백 태그

| 태그 | 의미 | 집계 소스 |
|---|---|---|
| `WINNING_SETUP` | 승리 셋업 | review GOOD + outcome PROFITABLE/AVOIDED_LOSS |
| `LOSING_SETUP` | 패배 셋업 | review BAD + outcome LOSS |
| `OVER_ENTRY` | 과잉진입 | review WEAK_SIGNAL_ENTRY / EARLY_ENTRY |
| `LATE_EXIT` | 늦은 청산 | review LATE_EXIT / IMPROVE_EXIT_TIMING |
| `EARLY_EXIT` | 이른 청산 | outcome MISSED_OPPORTUNITY |
| `POOR_RISK_REWARD` | 손익비 불량 | review FALSE_BREAKOUT / BAD_DECISION |
| `LOW_DATA_QUALITY` | 데이터 품질 | data_status STALE/FAIL + reason PRICE_STALE |
| `MARKET_REGIME_MISMATCH` | 장세 부적합 | reason BLOCK_NEW_BUY / REGIME_MISMATCH |

### threshold recommendation 의미

실패성 태그가 `recommend_min`(기본 3회) 이상 반복되면 추천 후보 생성. 예:
- OVER_ENTRY → "신호 약할 때(quality_score 낮음) 진입 보류 강화 검토"
- LATE_EXIT → "VWAP 이탈 시 청산 판단 강화 검토"
- LOW_DATA_QUALITY → "PRICE_STALE / 데이터 품질 저하 시 진입 금지 유지"
- MARKET_REGIME_MISMATCH → "장세 부적합(BLOCK_NEW_BUY) 시 BUY 억제 유지"

**자동 적용 아님** — 각 추천은 `auto_apply_allowed=False` / `requires_operator_approval=True`.
운영자 승인 없이 기존 설정을 변경하지 않는다. `feedback_penalty_for()` 는 HIGH
severity 실패 태그 수에 비례한 penalty(0~30)를 산출해 *다음 판단 quality 계산 입력*
으로만 쓰인다(설정 변경 아님).

## 2. quality_score 고도화 (#51)

`quality_score` 는 0~100 으로 유지한다. 고도화 점수는 5개 요소의 가중합:

| 요소 | 가중치 | 의미 |
|---|---|---|
| `signal_consistency` | 0.30 | 4전략 vote 일치도 (최종 방향 동의 비율 + 강한 합의) |
| `data_reliability` | 0.20 | 시세 존재 / stale / volume / regime 확정 (부재 시 중립 70) |
| `risk` | 0.20 | risk_flags 수 + RiskOfficer veto |
| `regime_fit` | 0.15 | regime_decision / market_regime 와 최종 방향 정합 |
| `exit_plan` | 0.15 | BUY 는 유효 exit_plan 필수, 그 외 중립 |

`enhanced_quality_score = round(가중합) - feedback_penalty`, 0~100 clamp. grade A~F.

- **signal consistency**: 4전략이 같은 방향을 가리킬수록 높음. 분열되면 낮음.
- **data reliability**: 데이터 *부재(None) 는 과도 감점하지 않는다*(중립 70) — 누락만으로
  BUY 를 강제 HOLD 하지 않기 위함. stale/실패는 감점.
- **risk score**: risk_flags 가 많거나 veto 가 걸리면 낮음.
- **regime fit**: BLOCK_NEW_BUY 에서 BUY → 30, TREND_DOWN + BUY → 40, ALLOW → 90.
- **exit plan score**: BUY 인데 exit_plan 무효/누락 → 20.

### quality 낮으면 HOLD

`run_agent_council` 의 기존 게이트(가중투표 → 장세 → confidence/quality 임계 →
RiskOfficer veto → exit_plan 검증 → 보유 청산) **뒤** 에 별도 quality gate 가 동작한다.
`final_action == BUY` 이고 `enhanced_quality_score < profile min_quality` 면 HOLD 로
강등하고, `pre_quality_action` 에 강등 전 action 을 보존, `reason_code=QUALITY_SCORE_LOW_HOLD`.

- **기존 RiskOfficer / exit_plan HOLD 정책을 우회하지 않는다** — 그 *뒤* 에서 추가 게이트로만 작동.
- `pre_veto_action` / `pre_exit_plan_action` 과 충돌하지 않도록 **별도 `pre_quality_action`** 사용.
- 기존 `quality_score` 필드는 그대로 유지(호환). 고도화 결과는 `quality_gate_result`
  (breakdown / penalties / enhanced_quality_score / should_hold) 로 *추가* carry.
- 이 단계는 *판단* 단계라 `final_action` 만 HOLD 가 된다 — KIS Paper 주문 decision 은
  final_action 기준이므로 결과적으로 주문 0건.

## 3. AgentDecisionLog / episode 저장

`AgentCouncilDecision.to_dict()` 에 `quality_gate_result` + `pre_quality_action` 이
추가되어 episode.council 로 영구화된다. 별도 마이그레이션 0건(기존 JSON 컬럼에 nest).

## 4. UI

AISignal 탭에 2개 카드 마운트:
- `DecisionQualityScoreCard` — quality_score/grade/breakdown/penalties + "quality 낮으면 HOLD".
- `PostTradeFeedbackCard` — 피드백 태그 + threshold 추천 + "자동 적용 안 됨 / 운영자 승인 필요".

두 카드 모두 **주문/실전/승인 버튼 0개, 입력 form 0개**, "분석/표시 전용 · 주문 아님 ·
자동 적용 안 됨 · 수익 보장 아님" 문구 노출.

## 5. 안전 가드 (정적 grep + dataclass 불변)

- `post_trade_feedback.py` / `decision_quality.py`: broker / OrderExecutor / 단일 주문
  라우터 / paper_trader / KIS 어댑터 / anthropic / openai / httpx / requests import 0건,
  broker 주문/취소/route 호출 0건, **DB write 0건**(read-only), secret/계좌 출력 0건.
- `FeedbackLoopReport.auto_apply_allowed=False` / `requires_operator_approval=True` /
  `is_order_signal=False` / `is_live_authorization=False` / `contains_secret=False` 불변.
- `DecisionQuality.is_order_signal=False` / `is_live_authorization=False` /
  `contains_secret=False` 불변, score 0~100 범위 강제.
- 안전 flag (`ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
  `ENABLE_FUTURES_LIVE_TRADING` / `KIS_IS_PAPER`) 변경 0건, `.env` 변경 0건.
- 기존 council 테스트(quality_score 임계/투표) 무회귀 — quality gate 는 약한 BUY 만
  보수적으로 강등하며 강한 BUY 는 통과.
