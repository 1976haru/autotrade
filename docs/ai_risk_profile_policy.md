# AI 운용 성향 (Risk Profile) 프리셋 정책

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 프리셋은 *advisory* — Paper 가상 체결 흐름의 threshold 조정만, 실 broker
> 호출 0건.

## 1. 목적

사용자가 AI Paper 자동매매를 시작하기 *전*, **AI 의 위험 성향** 을 셋 중 하나로
선택할 수 있다. 선택된 프리셋은 4-08 `PositionSizingPolicy` + 4-09 `RiskVeto`
의 임계값을 *동시에* 조정해 거래 후보의 선별 강도를 바꾼다.

## 2. 3개 프리셋 정의

| 프리셋 | 한글 | 의미 |
|---|---|---|
| `CONSERVATIVE` | 보수적 | 손실 방어 우선 — confidence 매우 높아야, risk_flag 거의 못 허용, position size 작음 |
| `BALANCED` | 안정적 (**기본값**) | 신규 진입과 거래 기회의 균형 — 4-08 module 의 기존 default 와 일치 |
| `AGGRESSIVE` | 공격적 | 거래 기회를 넓게 — confidence 임계 낮추고 risk_flag 허용 늘림. **실거래 안전장치는 절대 우회 못함** |

기본값은 **`BALANCED` (안정적)**. 운영자가 명시적으로 다른 프리셋을 선택하지
않으면 항상 안정적 모드.

## 3. 성향별 임계값 매트릭스

| 항목 | CONSERVATIVE | BALANCED (default) | AGGRESSIVE |
|---|---:|---:|---:|
| 1회 거래 손실 한도 (`max_risk_per_trade_pct`) | 0.5% | 1.0% | 2.0% |
| 기본 stop-loss (`default_stop_loss_pct`) | 2.0% | 3.0% | 5.0% |
| 1 종목 최대 비중 (`max_position_pct`) | 10% | 20% | 30% |
| 1 종목 최대 KRW (`max_position_krw`) | ₩3,000,000 | ₩5,000,000 | ₩8,000,000 |
| confidence 임계 (`min_confidence_threshold`) | 0.60 | 0.40 | 0.30 |
| 4-08 sizing 차단 risk_flags 수 (`max_risk_flags`) | 2 이상 → 0 | 3 이상 → 0 | 4 이상 → 0 |
| 4-09 신규 진입 차단 risk_flags 수 (`risk_veto_max_flags`) | 0 (어떤 flag 있어도 차단) | 1 까지 허용 | 2 까지 허용 |
| Paper 동시 후보 수 (`max_concurrent_candidates`) | 2 | 3 | 5 |

### 패턴 정리

- **confidence 임계** — 보수적이 가장 높고 공격적이 가장 낮다 (`CONS > BAL > AGG`).
- **모든 허용/한도** — 보수적이 가장 작고 공격적이 가장 크다 (`CONS < BAL < AGG`).
- **BALANCED = 4-08 PositionSizingPolicy default** — 기존 운영 흐름과 backwards-compat.

## 4. 절대 invariant (테스트로 lock)

| 항목 | 검증 위치 |
|---|---|
| `RiskProfilePolicy.is_order_signal=False` | `__post_init__` ValueError |
| `RiskProfilePolicy.auto_apply_allowed=False` | 위 |
| `RiskProfilePolicy.is_live_authorization=False` | 위 — **AGGRESSIVE 포함** |
| `is_live_profile(...)` 항상 False (모든 입력) | `TestLiveProfileInvariant` (6 parametrized) |
| BALANCED 임계값이 4-08 PositionSizingPolicy default 와 1:1 | `test_balanced_matches_position_sizer_defaults` |
| `policy_for(None / "" / unknown)` → BALANCED | `TestPolicyForFallback` |
| 임계값 순서 — 보수적 < 안정적 < 공격적 | `TestPresetThresholds` (5 ascending + 1 descending) |
| broker / OrderExecutor / route_order import 0건 | `TestStaticGuards` |
| Anthropic / OpenAI / httpx / requests import 0건 | 위 |
| `settings.enable_*` mutation 0건 | 위 |
| DB write 0건 | 위 |
| secret 필드 0건 (`api_key` / `account_number` 등) | `test_no_secret_fields_in_dataclass` |

## 5. 핵심 원칙: 공격적 ≠ 실거래

**공격적(`AGGRESSIVE`) 프리셋도** 다음 안전장치를 *절대 우회하지 않는다*:

- `ENABLE_LIVE_TRADING=false` 기본값 — 본 PR 변경 0건.
- `KisBrokerAdapter.place_order(is_paper=False)` → `NotImplementedError`.
- `RiskManager` → `PermissionGate` → `OrderExecutor` 단일 진입점 (#34/#40).
- 4-Live-Separation 의 정적/동적 가드.
- 본 모듈은 broker / OrderExecutor / route_order 를 *import 하지 않는다*
  (정적 grep + AST 가드).

공격적 프리셋은 *Paper* 단계의 진입 임계만 낮추며, 실거래 활성화는 별도 옵트인
PR + Paper Gate (#72) / Live Manual Gate (#73) / AI Assist Gate (#74) /
AI Execution Activation Gate (#75) / AIExecutionGate (#45) 5단계 통과 필요.

## 6. 사용 API

```python
from app.agents.risk_profile import (
    RiskProfile, DEFAULT_RISK_PROFILE, policy_for,
    sizing_policy_for, risk_veto_policy_for, list_profiles,
)

profile = RiskProfile.BALANCED          # 사용자 선택 (default)
preset = policy_for(profile)            # full RiskProfilePolicy
sizing = sizing_policy_for(profile)     # 4-08 PositionSizingPolicy 변환
veto = risk_veto_policy_for(profile)    # 4-09 임계값 dict
catalog = list_profiles()               # UI / API 노출용 3개 row
```

`sizing_policy_for(profile)` 결과를 `bridge_explanation_to_paper_decisions(...,
sizing_policy=...)` 의 인자로 그대로 넘기면 4-08 흐름이 프리셋 반영. 본 PR 은
adapter 함수만 제공 — caller (예: `auto_paper/agent_consumer` 후속 PR) 가
명시적으로 호출.

## 7. 구현 위치

| 파일 | 의미 |
|---|---|
| `backend/app/agents/risk_profile.py` | `RiskProfile` enum + `RiskProfilePolicy` + 3 presets + adapters |
| `backend/tests/test_ai_risk_profile.py` | 58 cases |
| `docs/ai_risk_profile_policy.md` | 본 정책 |

## 8. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건 (정적 AST 가드)
- ✅ KIS / Anthropic / OpenAI / 외부 HTTP / `httpx` / `requests` import 0건
- ✅ DB write 0건 — 순수 dataclass + lookup 함수
- ✅ secret 필드 0건 (`api_key` / `account_number` 등)
- ✅ 안전 flag default 변경 0건 — `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
  `ENABLE_FUTURES_LIVE_TRADING` / `KIS_IS_PAPER` 그대로
- ✅ `is_live_authorization=False` 영구 — 어떤 프리셋도 실거래 허가 아님
- ✅ AGGRESSIVE 도 *Paper 단계의 임계값 조정* 만 — 실거래 흐름 절대 우회 못함

## 9. 적용 (#4-RiskProfileApply)

본 절은 *프리셋이 실제 판단에 반영되는 경로* 를 정의한다 (별도 PR 에서 추가):

### 9.1 Consumer 통합

`consume_agent_recommendations(..., risk_profile=...)`:

```python
result = consume_agent_recommendations(
    loop_state="RUNNING",
    recommendation_provider=...,
    risk_profile=RiskProfile.AGGRESSIVE,   # 또는 "aggressive" / "CONSERVATIVE" 등
    ...
)
# result.metadata["risk_profile"] == "AGGRESSIVE"
# result.metadata["risk_veto_max_flags"] == 2
```

- `risk_profile` 가 주어지면 `sizing_policy_for(profile)` 으로 4-08
  `PositionSizingPolicy` 자동 도출 → bridge 의 `sizing_policy` 인자.
- `risk_veto_max_flags` 가 bridge → `evaluate_risk_veto` 까지 전파.
- 명시 `sizing_policy` 인자가 함께 주어지면 *operator override 우선* —
  profile 라벨은 metadata 에 carry 만.

### 9.2 Risk veto 통합

`evaluate_risk_veto(..., risk_veto_max_flags=N)`:

- `risk_veto_max_flags=0` (CONSERVATIVE): 어떤 flag 1개라도 BLOCK (기존 동작).
- `risk_veto_max_flags=1` (BALANCED): flag 1개 까지 허용, 2개 이상 BLOCK.
- `risk_veto_max_flags=2` (AGGRESSIVE): flag 2개 까지 허용, 3개 이상 BLOCK.
- `EMERGENCY_STOP` / `PRE_MARKET_BLOCK` / `RISK_OFFICER_REJECT` 는 임계값과
  *무관* 하게 항상 BLOCK — 본 완화의 영향을 받지 *않는다*.

### 9.3 Position size 차이

동일한 high-confidence 입력에서 (`equity=1억`, `price=70k`, `confidence=0.95`):
- CONSERVATIVE → 가장 작은 quantity
- BALANCED → 중간
- AGGRESSIVE → 가장 큰 quantity
순서는 `CONS < BAL < AGG` 영구 (`TestPositionSizeOrdering`).

### 9.4 Confidence threshold 차이

`confidence=0.35` 입력에서:
- CONSERVATIVE (임계 0.60) → quantity=0 → HOLD
- BALANCED (임계 0.40) → quantity=0 → HOLD
- AGGRESSIVE (임계 0.30) → quantity > 0 → BUY

(`TestConfidenceThreshold`)

### 9.5 안전 invariant (적용 후에도 영구)

| 항목 | 검증 위치 |
|---|---|
| 어떤 프리셋도 broker spy 호출 0건 | `TestSafetyInvariants` (3 parametrized) |
| 모든 AgentDecisionLog row `mode="PAPER"` | `test_agent_decision_log_rows_are_paper_mode` |
| `is_order_signal=False` / `auto_apply_allowed=False` / `is_live_authorization=False` carry | 위 |
| EMERGENCY_STOP / PRE_MARKET_BLOCK / RISK_OFFICER_REJECT 모든 프리셋에서 BLOCK | `TestRiskVetoFlagThreshold` 4 cases |
| 명시 sizing_policy 가 risk_profile 보다 우선 | `TestExplicitOverridePrecedence` |
| 비RUNNING + 모든 프리셋 → 0 decisions / 0 log row | `TestNonRunningProfileInert` (parametrized profile × state) |

## 10. UI 선택 (#4-RiskProfileUI)

`frontend/src/components/AgentRiskProfileSelector.jsx` 가 3 라디오 카드
(보수적 / 안정적 / 공격적) + Paper 전용 배지 + AGGRESSIVE 경고 + 영구
disclaimer 를 노출. `AutoPaperLoopCard` 가 본 selector 를 *시작 버튼 위*
에 렌더 → 사용자가 선택한 프리셋이 `POST /api/auto-paper/start` body
의 `risk_profile` 필드로 동봉.

| UI 요소 | testid | 안전 invariant |
|---|---|---|
| selector container | `agent-risk-profile-selector` | secret 입력 form 0개, 라벨 button 외 button 0개 |
| 3 라디오 카드 | `risk-profile-card-{CONSERVATIVE,BALANCED,AGGRESSIVE}` | 카드 자체는 button — 라벨은 "보수적/안정적/공격적" 만, 주문 라벨 0건 |
| 라디오 상태 | `risk-profile-radiogroup[data-selected]` | 기본값 BALANCED, 알 수 없는 value fallback BALANCED |
| Paper-only 배지 | `risk-profile-paper-only-badge` | "Paper 전용 · 실거래 아님" 영구 |
| AGGRESSIVE 경고 | `risk-profile-aggressive-warning` | AGGRESSIVE 선택 시에만 노출, "실거래 안전장치를 우회하지 않습니다" 영구 |
| 영구 footer note | `risk-profile-footer-note` | `is_order_signal=false` / `auto_apply_allowed=false` / `is_live_authorization=false` 텍스트 carry |

**선택값 전달**: `AutoPaperLoopCard.onStart` 가 `apiClient.autoPaperStart({
risk_profile: <selected>, ...pre_market })` 형태로 호출. backend 의
`POST /api/auto-paper/start` body 가 `risk_profile` 을 받아 consumer
runner 주입 시점에 활용 (별도 후속 PR 에서 wire). 본 PR 은 frontend
선택 + 전달까지.

**Disabled 동작**: `state ∈ {RUNNING, WAITING_MARKET}` 또는 `busy=true` 시
selector 카드 비활성화 — RUNNING 중 프리셋 변경 불가 (정책: 한 cycle 끝난 뒤만 변경).

## 11. 후속 PR 권고

- **API endpoint** — `GET /api/agents/risk-profiles` (catalog) + `POST
  /api/auto-paper/start` 가 `risk_profile` body 필드 수용 (backend 도
  본 정보를 consumer 에 forward).
- **operator override** — 운영자가 본 프리셋 위에 추가 override 를 원하면
  별도 옵트인 PR (사용자 명시 승인 필요).
- **per-strategy profile** — 본 PR 은 전체 portfolio 1 프리셋. 후속 PR 에서
  strategy 별 profile 도 검토 가능.
