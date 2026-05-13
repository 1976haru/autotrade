# Loss Tagging Policy — 체크리스트 #79

> 손실 거래의 *추정 원인*을 자동 태깅하고 LossReasonLog 에 *append* 한다.
> **태그는 추정값이며 확정 원인이 아니다.** 운영자 검토가 필요하며, 태그를
> *투자 조언*이나 *주문 차단 / 실행*에 사용하지 않는다.

---

## 1. 목적

- AI 리포트 / Daily Report / Strategy Researcher 의 *품질 향상*.
- 반복 손실 원인을 축적해 사후 학습 자료로 사용.
- DailyReportAgent / StrategyResearcherAgent / RiskAuditorAgent / AgentMemory
  가 *read-only* 로 참조할 수 있게 정보 제공.

---

## 2. 태그 목록 (7 카테고리, 25 tag)

| Category | Tags |
|---|---|
| `strategy`  | `stop_loss_hit` / `failed_breakout` / `false_rebreak` / `vwap_loss` / `target_not_reached` / `time_stop` / `reversal_signal` |
| `market`    | `market_selloff` / `sector_drop` / `regime_change` / `volatility_spike` |
| `execution` | `low_liquidity` / `high_slippage` / `partial_fill` / `price_gap` |
| `risk`      | `risk_limit_hit` / `emergency_stop` / `over_exposure` |
| `data`      | `data_stale` / `bad_quote` / `missing_bar` |
| `agent`     | `ai_overconfidence` / `ai_low_confidence` / `news_theme_faded` |
| `unknown`   | `unknown` (자동 분류 실패 시 fallback) |

### Primary tag 우선순위
`risk` > `data` > `market` > `execution` > `strategy` > `agent` > `unknown`.

운영 차단 사유(risk) > 데이터 무결성(data) > 시장 외부 요인(market) > 체결
품질(execution) > 전략 신호 문제(strategy) > AI 신호(agent) 순으로 우선 표시.

---

## 3. 추정값 원칙

본 모듈의 모든 결과는 *추정*이다.

- **확정 원인 아님** — `LossEstimateResult.is_estimated=True` 불변
  (dataclass `__post_init__` ValueError 가드).
- **사람이 검토 가능** — `LossReasonLog.review_status` / `reviewed_by` /
  `review_note` 컬럼으로 운영자가 *"추정 맞음/아님"* 의견 추가.
- **여러 태그 가능** — 하나의 거래에 여러 태그가 동시에 부여될 수 있다.
- **primary_tag** — 가장 가능성이 높은 추정. 위 우선순위에 따라 자동 선정.
- **confidence** — 0~100 (휴리스틱 확신도, tag 수에 비례).

---

## 4. 저장 방식 (`LossReasonLog`)

| 컬럼 | 의미 |
|---|---|
| `source_table` / `source_id` | 출처 (order_audit / virtual_order / futures_audit / manual / agent) |
| `symbol` / `strategy` / `mode` | 거래 기본 정보 (검색용 인덱스) |
| `trade_pnl` / `is_loss` | 손익 (음수 = 손실) |
| `primary_tag` / `primary_category` | 가장 유력한 추정 |
| `tags` / `rationale` | 전체 태그 + 추정 근거 (JSON) |
| `confidence` | 휴리스틱 확신도 |
| `is_estimated` | 항상 True (invariant) |
| `review_status` / `reviewed_by` / `review_note` / `reviewed_at` | 운영자 review |

### Append + review only — 삭제 금지

- LossReasonLog 에 DELETE 경로 0개 (정적 grep 가드).
- 운영자 review 는 review_* 컬럼만 update — *원본 추정 데이터는 변경되지 않는다*.
- 마이그레이션 0022 (alembic `20260526_0022_loss_reason_log.py`).

---

## 5. Agent / Report 연계 (read-only helpers)

본 모듈은 다른 Agent 를 *직접 호출하지 않는다* — 호출자(Agent / Report)가
helper 결과를 *재사용*.

### DailyReportAgent
- `summarize_for_daily_report(results, top_n=5) → dict`
- markdown 섹션 생성에 사용 가능: `{loss_count, top_tags, by_category, note,
  is_estimated}`

### StrategyResearcherAgent
- `summarize_for_strategy_researcher(results) → dict`
- 반복 손실 태그 (count ≥ 2) 를 개선 후보 발굴에 사용

### RiskAuditorAgent / AgentMemory
- `summarize_loss_reasons(db, days=7) → dict` (DB collector)
- 운영자가 review_note 작성한 LossReasonLog row 를 Agent Memory (#56) 에
  *별도로* 저장하는 helper 는 후속 PR.

---

## 6. API

| Endpoint | 메서드 | 설명 |
|---|---|---|
| `/api/analytics/loss-tags/estimate`     | POST  | 단일 거래 추정 (`persist=true` 면 LossReasonLog 에 append) |
| `/api/analytics/loss-tags/summary`      | GET   | 기간별 집계 (`days` / `strategy`) |
| `/api/analytics/loss-tags/recent`       | GET   | 최근 LossReasonLog 목록 |
| `/api/analytics/loss-tags/{id}/review`  | PATCH | 운영자 review 추가 (review_* 컬럼만 update) |

**DELETE 엔드포인트 없음** — 정적 grep 가드로 강제.

응답 모든 엔드포인트에 invariant 필드 포함:
- `is_estimated=true`
- `is_order_signal=false` (estimate 응답)
- `is_investment_advice=false` (estimate 응답)
- `live_flag_changed=false`
- `mode_changed=false`

---

## 7. 금지 사항

- 🚫 태그만으로 *주문 차단 / 실행* 금지 — advisory only.
- 🚫 태그를 *투자 조언*으로 사용 금지.
- 🚫 LossReasonLog row 삭제 금지 — append + review only.
- 🚫 운영자 review 시 원본 추정 데이터 변경 금지 — review_* 컬럼만 update.
- 🚫 "확정 원인" 표현 금지 — UI / API / 리포트 모두 "추정 원인" 또는
  "가능성이 높은 요인" 사용.

---

## 8. UI

`frontend/src/components/tabs/LossReasonCard.jsx`:

- 표시: 통계 (loss_count / pnl_sum / period) + Top tags + 카테고리별 + 전략별 +
  최근 손실 거래 + primary_tag + confidence + review.
- "추정 원인 · 확정 원인 아님" 영구 배지.
- disclaimer *항상* 노출: "본 카드의 태그는 *추정 원인 / 가능성이 높은 요인*입니다.
  **확정 원인이 아닙니다.** 운영자 검토가 필요하며, 손실 태그를 *투자 조언*이나
  *주문 차단 / 실행*에 사용하지 마세요."
- **삭제 / 강제 적용 / 자동 비활성 / 확정 원인 라벨 버튼 0개** (테스트로 lock).
- BUY/SELL/HOLD/긴급정지 토글 문구 0건.
- Secret 패턴 0건.
- "추정 원인 요약 새로 고침" 버튼만.

---

## 9. 절대 원칙 — 본 모듈 강제

`tests/test_loss_tagging.py`의 정적 grep 가드:

1. broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` /
   `app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` import 0건.
2. `broker.place_order(` / `route_order(` / `OrderExecutor(` /
   `submit_candidate(` / `AiClient(` 호출 0건.
3. `settings.enable_*_trading =` mutate 0건.
4. evaluator `loss_tagging.py` 는 DB write 0건 — storage 모듈에만 INSERT.
5. storage `loss_tagging_storage.py` 는 `db.delete(` / `DELETE FROM` 0건.
6. routes `routes_analytics.py` 는 `@router.delete` 0건.
7. `LossEstimateResult.is_estimated=False` 생성 불가 (ValueError).
8. `LossEstimateResult.is_order_signal=True` 생성 불가.
9. `LossEstimateResult.is_investment_advice=True` 생성 불가.
10. UI 카드 "강제 적용" / "자동 비활성" / "전략 비활성화" / "삭제" / "확정 원인" /
    "주문 차단 적용" / "ENABLE_*" / "Place Order" 라벨 버튼 0개.

---

## 10. 후속 backlog

- **ML 기반 분류** — 룰베이스 → 손실 거래 표본으로 학습된 분류기 (별도 PR)
- **operator feedback loop** — review_status 통계 → 추정 룰 가중치 조정
- **strategy별 loss pattern dashboard** — 시계열 손실 태그 추세
- **자동 collector** — OrderAuditLog 청산 row → 자동 estimate + append
- **AgentMemory 통합** — review_note 있는 row 를 Memory 로 저장 helper
- **DailyReportAgent / StrategyResearcherAgent 자동 호출** — Agent 측 PR
- **RiskAuditorAgent (#54) 통합** — 반복 risk 카테고리 태그 surface
- **multi-tag confidence weighting** — tag 수가 아니라 강도 가중치

---

## 11. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙
- [`docs/daily_report_agent.md`](daily_report_agent.md) — DailyReportAgent (#57)
- [`docs/strategy_researcher_agent.md`](strategy_researcher_agent.md) — StrategyResearcher (#55)
- [`docs/risk_auditor_agent.md`](risk_auditor_agent.md) — RiskAuditor (#54)
- [`docs/agent_memory.md`](agent_memory.md) — AgentMemory (#56)
- [`docs/audit_log_policy.md`](audit_log_policy.md) — #68 audit event facade
- [`docs/alpha_decay_monitor.md`](alpha_decay_monitor.md) — #77 (관련 — 전략 단위)
- [`docs/correlation_guard_policy.md`](correlation_guard_policy.md) — #78
