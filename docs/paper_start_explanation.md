# Step 4-05 — Paper 실행 전 최종 설명

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 설명은 *advisory* — 실거래 주문이 아니며 자동 paper trader 시작 / 자동
> 실거래 활성화를 수행하지 **않습니다.**

## 1. 목적

운영자가 *[시작]* 버튼을 누르기 전, AI Agent 가 **왜** 이 전략을 추천했는지 /
**왜** 어떤 전략은 제외 / 보류했는지 한 화면에서 설명. 4-01 ~ 4-04 결과를
*통합* read-only aggregator.

비개발자 친화 UX:
- "오늘의 추천 전략" + 추천 사유 2~5줄
- 제외된 전략과 한국어 제외 사유
- 보류된 전략과 한국어 보류 사유
- 장세 판단 결과 (`market_regime`) + `regime_confidence`
- 과최적화 경고 여부 + `overfit_count`
- 스트레스 테스트 / Walk-forward 위험 요약
- "Paper 전용" / "실거래 주문 아님" / "자동 시작 아님" 배지
- `can_start_paper=False` 시 `blocking_reasons` carry

## 2. 구현 위치

| 파일 | 의미 |
|---|---|
| `backend/app/agents/paper_start_explanation.py` | aggregator + dataclasses (`PaperStartExplanation`, `StrategyExplanation`, `ExplanationVerdict`) |
| `backend/app/api/routes_paper_start_explanation.py` | `POST /api/agents/paper-start-explanation` |
| `backend/tests/test_paper_start_explanation.py` | 31 tests across 8 classes |
| `frontend/src/components/tabs/PaperStartExplanationCard.jsx` | UI 카드 |
| `frontend/src/components/tabs/PaperStartExplanationCard.test.jsx` | 11 frontend tests |
| `docs/paper_start_explanation.md` | 본 정책 |

## 3. 5단계 ExplanationVerdict

| Verdict | 의미 |
|---|---|
| `READY_TO_REVIEW` | 추천 1+ AND 경고 적음 — Paper 검토 가능 |
| `REVIEW_WITH_WARNING` | 추천 1+ AND 경고 다수 (overfit / regime risk / risk_summary ≥2) — 신중 검토 |
| `HOLD` | 모두 보류 (WATCH_ONLY / REJECTED_BY_RISK 만) — 시작 권고 안 함 |
| `DO_NOT_START` | Pre-market BLOCK / LOW_LIQUIDITY / UNKNOWN / 후보 0건 — 차단 |
| `INSUFFICIENT_DATA` | NEED_MORE_DATA dominant — 분석 입력 부족 |

`BUY` / `SELL` / `PLACE_ORDER` 같은 주문 방향 값 **0개** (테스트 lock).

## 4. 우선순위 매트릭스 (verdict 결정 순서)

```
1. Pre-market BLOCK (start_allowed=False)        → DO_NOT_START + blocking_reasons[pre_market_block]
2. LOW_LIQUIDITY regime                           → DO_NOT_START + blocking_reasons[regime_low_liquidity]
3. UNKNOWN regime                                 → DO_NOT_START + blocking_reasons[regime_unknown]
4. 4-02 v2 NO_CANDIDATE (후보 0건)               → DO_NOT_START + blocking_reasons[no_candidate]
5. 4-02 v2 NEED_MORE_DATA (모두 데이터 부족)     → INSUFFICIENT_DATA + blocking_reasons[need_more_data]
6. 4-02 v2 REJECTED_BY_RISK 또는 WATCH_ONLY      → HOLD + blocking_reasons[all_hold_or_rejected]
7. 추천 1+ AND 경고 다수 (overfit/regime/risk)    → REVIEW_WITH_WARNING (can_start_paper=True)
8. 추천 1+ AND 경고 적음                          → READY_TO_REVIEW (can_start_paper=True)
```

**과최적화 우선 (4-03 > 4-04 > 본 모듈)**: 4-03 `apply_overfit_filter` 가 OVERFIT_RISK 전략을 *추천에서 영구 제거* → 4-04 regime 정책이 preferred 라도 *원복하지 않음* → 본 모듈은 결과를 그대로 carry. 테스트 lock: `test_overfit_only_marks_excluded` + `test_overfit_risk_is_excluded_not_recommended`.

## 5. 출력 스키마 (`PaperStartExplanation`)

```jsonc
{
  "generated_at": "2026-05-18T...",
  "schema_version": "1.0",
  "verdict": "READY_TO_REVIEW",
  "verdict_label_ko": "AI Paper 검토 가능 — 추천 조합을 운영자가 검토 후 시작 결정",

  "recommended_explanations": [
    {
      "strategy": "sma_crossover",
      "symbol": "005930",
      "bucket": "recommended",
      "paper_candidate_status": "READY_FOR_PAPER",
      "rationale_lines": [
        "검증 단계 통과 + 위험 신호 임계 이내 — Paper 검토 가능",
        "장세 TREND_UP 에서 *우선 검토* 전략군"
      ],
      "risk_flags": [],
      "overfit_verdict": "HEALTHY",
      "overfit_reason": null,
      "train_validation_gap": 0.20,
      "regime_policy_role": "preferred",
      "is_order_signal": false,
      "auto_apply_allowed": false,
      "is_live_authorization": false
    }
  ],
  "watchlist_explanations": [...],
  "excluded_explanations": [
    {
      "strategy": "sma_crossover",
      "symbol": "000660",
      "bucket": "excluded",
      "paper_candidate_status": "OVERFIT_RISK",
      "rationale_lines": [
        "위험 한도 위반 (PF / MDD / 손실 streak)",
        "⚠ 과최적화 의심 — 훈련구간에서만 좋고 검증구간에서 성과 저하 (train/val gap=0.87)"
      ],
      "overfit_verdict": "OVERFIT_RISK",
      "train_validation_gap": 0.87,
      ...
    }
  ],

  "market_regime":          "TREND_UP",
  "regime_confidence":      0.75,
  "regime_reasons":         ["trend_direction=UP"],
  "regime_risk_flags":      [],
  "regime_allowed_tactics": ["sma_crossover", "volume_breakout", ...],
  "regime_blocked_tactics": [],

  "overfit_count":     1,
  "overfit_strategies": ["sma_crossover/000660"],

  "headline":     "오늘 AI Paper 검토 가능: 1건 — sma_crossover/005930. 본 추천은 advisory.",
  "risk_summary": ["overfit_risk_count=1"],
  "operator_note": "상승 추세 — momentum / breakout 계열 우선 검토.",
  "next_actions": [
    "추천 전략을 *수동* 으로 Paper Auto Loop 에 입력",
    "AI Agent 가 표시한 위험 신호 / 제외 사유 확인 후 시작 결정",
    "본 설명은 *advisory* — 실거래 활성화는 별도 옵트인 절차 필요"
  ],

  "can_start_paper":   true,
  "blocking_reasons":  [],

  "advisory_disclaimer": "본 설명은 *advisory* — 실거래 주문이 아니며 ...",
  "metadata": { ... },

  // 최상위 invariant.
  "is_order_signal":       false,
  "auto_apply_allowed":    false,
  "is_live_authorization": false
}
```

## 6. UI 정책 (`PaperStartExplanationCard.jsx`)

**필수 표시**:
- 안전 배지 3종: "Paper 전용 · 모의매매 advisory" / "실거래 주문 아님" / "자동 시작 아님"
- Verdict 헤드라인 (컬러 배지 + 한국어 라벨)
- 장세 정보 (regime / confidence / reasons)
- 추천 / 보류 / 제외 3 bucket 별 strategy 목록 + rationale_lines
- can_start_paper=False 시 `paper-explanation-blocking` 패널
- 위험 요약 / 과최적화 별도 강조 / 다음 행동 / advisory disclaimer

**금지 라벨** (frontend test lock):
- `매수` / `매도` / `BUY` / `SELL` / `EXIT` / `Place Order` / `실거래 시작` /
  `ENABLE_LIVE_TRADING` / `AI 자동매매 켜기` 같은 button 텍스트 0개
- 추천/제외/보류는 `<button>` 이 아니라 `<span>` 배지 (Paper 판단 *라벨*)
- 본 카드는 실제 시작 버튼을 *제공하지 않음* — 운영자가 별도 BotControl /
  Paper Auto Loop 에서 명시 시작

## 7. 절대 invariant (테스트로 lock)

| 항목 | 강제 위치 |
|---|---|
| `PaperStartExplanation.is_order_signal=False` | `__post_init__` ValueError |
| `PaperStartExplanation.auto_apply_allowed=False` | 위 |
| `PaperStartExplanation.is_live_authorization=False` | 위 |
| `StrategyExplanation` 동일 3 invariant | 위 |
| `ExplanationVerdict` 5단계 lock — BUY/SELL/PLACE_ORDER 값 0개 | enum 정의 |
| `regime_confidence` ∈ [0, 1] | `__post_init__` 범위 검증 |
| `bucket` ∈ recommended/watchlist/excluded | `__post_init__` 검증 |
| broker / OrderExecutor / route_order import 0건 | `TestNoForbiddenImports` |
| 외부 HTTP / AI SDK import 0건 (anthropic/openai/httpx/requests) | 정적 grep |
| schema 에 API key / Secret / 계좌번호 필드 0건 | `test_schema_has_no_secret_fields` |
| **OVERFIT_RISK 전략은 *추천이 아니라 제외* 사유에 표시** | `test_overfit_only_marks_excluded` |
| `can_start_paper=False` 시 `blocking_reasons` 필수 carry | `test_can_start_false_carries_blocking_reasons` |
| frontend 카드에 `매수/매도/Place Order/실거래 시작/ENABLE_*` 라벨 button 0개 | frontend integration test |

## 8. API

`POST /api/agents/paper-start-explanation` — read-only.

**요청** (모두 optional):
```jsonc
{
  "market_state": {
    "trend_direction": "UP",     // "UP" / "DOWN" / "SIDEWAYS" / null
    "volatility_pct": 0.025,
    "liquidity_score": 0.7,
    "momentum_score": 0.4,
    "choppiness_index": 0.45
  },
  "pre_market": {
    "start_allowed": true,
    "verdict": "READY_TO_START",
    "blocking_reasons": [],
    "warnings": []
  },
  "demote_to_watchlist": false
}
```

**응답**: `PaperStartExplanation.to_dict()` (위 §5 참조).

**broker 호출 0건** — endpoint 가 호출하는 모든 흐름은 결정론적 read-only.

## 9. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건 (정적 grep)
- ✅ KIS 주문 API / Anthropic / OpenAI / 외부 HTTP import 0건
- ✅ 실제 매수 / 매도 / Place Order 0건 — *advisory aggregator* 전용
- ✅ 안전 flag default 변경 0건 — `KIS_IS_PAPER=true` / `ENABLE_LIVE_TRADING=false`
  / `ENABLE_AI_EXECUTION=false` / `ENABLE_FUTURES_LIVE_TRADING=false`
- ✅ secret / API key / 계좌번호 / `.env` 노출 0건
- ✅ AI Agent broker/executor 직접 호출 0건
- ✅ `READY_TO_REVIEW` 라벨은 *검토 권고* — 실제 시작은 운영자가 BotControl
  / Paper Auto Loop 에서 *명시 수행*

## 10. 후속 PR 권고

- AI Agent prompt context 에 본 설명 carry — LLM 이 자연어로 사용자에게 설명
  (별도 `app.ai.client` wiring PR)
- 운영자가 Paper Auto Loop 시작 시 본 explanation 의 `recommended_strategies`
  를 *자동 채워주는* form (수동 입력 불필요화) — 단, 시작 자체는 명시 클릭
- 매일 자동 새로고침 + 일일 ledger 비교 (이번 추천 vs 어제 추천 변화)
