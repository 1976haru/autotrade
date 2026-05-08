# AI Permission Gate (#39)

> 코드: [`backend/app/risk/ai_permission_gate.py`](../backend/app/risk/ai_permission_gate.py)
> API: `GET /api/risk/ai-permission/status` ([`backend/app/api/routes_risk.py`](../backend/app/api/routes_risk.py))
> 테스트: [`backend/tests/test_ai_permission_gate.py`](../backend/tests/test_ai_permission_gate.py)
> Frontend: [`frontend/src/components/common/AiPermissionCard.jsx`](../frontend/src/components/common/AiPermissionCard.jsx)

## 1. 목적

> **AI 자동매매로 넘어가도 권한 범위를 명확히 통제한다.** AI가 어떤 모드에서
> 어떤 행동을 할 수 있는지 명시적인 매트릭스로 분리해, AI가 허용 모드 밖에서
> 주문 API를 호출할 수 없게 한다.

## 2. 핵심 원칙 (절대 invariant)

1. **AI API Key는 주문 권한이 아니다.** API key가 있다고 주문 권한이 생기지
   않는다.
2. **AI는 broker를 모른다.** AI 모듈은 `app.brokers.*`를 import하지 않는다
   (CLAUDE.md 절대 원칙 5).
3. **AI는 직접 OrderExecutor를 호출하지 않는다.** `route_order` 단일 진입점만
   주문을 만들 수 있다 (CLAUDE.md 절대 원칙 6).
4. **권한은 mode + flags + operator approval로만 결정.** 본 모듈도 API key를
   입력으로 받지 않는다 (`AiPermissionFlags`에 secret-like 필드 0건 — 테스트
   가드).
5. 모든 주문은 RiskManager → PermissionGate → OrderExecutor 흐름 통과.

## 3. 5단계 권한 (`AiPermissionLevel`)

| Level | 의미 |
|---|---|
| `FULL_STOP` | AI 완전 중지. 추천도 차단. emergency_stop / disable_ai_orders / LIVE_MANUAL_APPROVAL 모드. |
| `RECOMMEND_ONLY` | 신호/추천만, 주문 흐름 진입 X. SIMULATION / PAPER / LIVE_SHADOW. |
| `APPROVAL_REQUIRED` | AI가 NEEDS_APPROVAL 큐에 제안, 운영자 수동 승인. LIVE_AI_ASSIST. |
| `VIRTUAL_EXECUTION` | AI가 가상 broker에서 자동 실행 (실거래 X). VIRTUAL_AI_EXECUTION. |
| `LIMITED_LIVE_EXECUTION` | AI가 실거래 실행 가능 (제한적). LIVE_AI_EXECUTION + 두 flag(`enable_live_trading` + `enable_ai_execution`) 모두 ON. |

## 4. 5가지 행동 (`AiAction`)

| Action | 의미 |
|---|---|
| `RECOMMEND` | 단순 신호 / 추천 생성. 주문 미생성. |
| `SUBMIT_FOR_APPROVAL` | 운영자 승인 큐(NEEDS_APPROVAL)에 주문 제안. |
| `VIRTUAL_EXECUTE` | 가상 broker로 자동 실행. |
| `LIVE_EXECUTE` | 실 broker 호출. |
| `FUTURES_LIVE_EXECUTE` | 선물 실거래 (`enable_futures_live_trading` 별도 필요). |

## 5. 모드별 권한 매트릭스

기본 flags (`enable_live_trading=False`, `enable_ai_execution=False`,
`enable_futures_live_trading=False`)에서:

| Mode | RECOMMEND | SUBMIT_FOR_APPROVAL | VIRTUAL_EXECUTE | LIVE_EXECUTE | FUTURES_LIVE_EXECUTE |
|---|---|---|---|---|---|
| `SIMULATION` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `PAPER` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `LIVE_SHADOW` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `LIVE_MANUAL_APPROVAL` | ❌ | ❌ | ❌ | ❌ | ❌ |
| `LIVE_AI_ASSIST` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `VIRTUAL_AI_EXECUTION` | ✅ | ❌ | ✅ | ❌ | ❌ |
| `LIVE_AI_EXECUTION` (default) | ✅ | ✅ | ❌ | ❌ | ❌ |
| `LIVE_AI_EXECUTION` (live+ai 둘 다 ON) | ✅ | ✅ | ✅ | ✅ | ❌ |
| `LIVE_AI_EXECUTION` (위 + futures ON) | ✅ | ✅ | ✅ | ✅ | ✅ |

`emergency_stop=True` 또는 `disable_ai_orders=True`이면 위 매트릭스와 무관하게
**모든 mode/action 차단** (FULL_STOP).

## 6. AI API Key와 주문 권한 분리

본 모듈의 입력 (`AiPermissionFlags`):

```python
@dataclass(frozen=True)
class AiPermissionFlags:
    enable_live_trading:          bool = False
    enable_ai_execution:          bool = False
    enable_futures_live_trading:  bool = False
    emergency_stop:               bool = False
    disable_ai_orders:            bool = False
```

- `api_key` / `secret` / `account_no` 필드 **0건** — 테스트 가드 (`test_module_
  does_not_take_api_key`).
- `evaluate_ai_permission` 시그니처에 api_key/secret 매개변수 0건 — 테스트
  `test_evaluate_signature_does_not_accept_api_key`로 가드.
- 모듈 자체가 `app.brokers.*`, `OrderExecutor`, `route_order` 호출 0건 —
  테스트 `test_module_does_not_import_broker_or_executor`로 가드.

→ AI Agent에 어떤 API Key를 발급해도 본 모듈을 우회해 broker로 직접 갈 수
없다. 모든 주문 흐름은 RiskManager → PermissionGate → OrderExecutor 단일
진입점을 통과해야 하며, AI가 그 흐름에 진입하려면 mode + flags + 운영자
승인이 필요하다.

## 7. RiskManager와 연계

본 PR(#39)에서는 **AI Permission Gate를 자동으로 route_order에 wire하지 않는다.**
이유:
- 기존 `RiskManager.evaluate_order`가 이미 `requested_by_ai=True`에 대해
  `disable_ai_orders` / `min_ai_confidence` / `enforce_ai_reasoning` /
  `can_ai_execute` 검사를 수행 — 사실상 같은 매트릭스를 강제 중.
- 본 게이트는 그 위에 *명시 표시 + UI 노출 + audit_note 생성* 레이어.
- 자동 wire-in은 광범위 테스트 회귀 검증 후 별도 옵트인 PR.

호출자는 다음과 같이 사용 가능:

```python
from app.risk.ai_permission_gate import (
    AiAction, AiPermissionFlags, evaluate_ai_permission,
)

flags = AiPermissionFlags(
    enable_live_trading=settings.enable_live_trading,
    enable_ai_execution=settings.enable_ai_execution,
    enable_futures_live_trading=settings.enable_futures_live_trading,
    emergency_stop=risk.emergency_stop,
    disable_ai_orders=risk.policy.disable_ai_orders,
)
decision = evaluate_ai_permission(
    action=AiAction.LIVE_EXECUTE, mode=current_mode, flags=flags,
)
if not decision.allowed:
    # decision.audit_note로 audit row에 carry, decision.reasons로 응답.
    ...
```

## 8. /api/risk/ai-permission/status

read-only 엔드포인트. 응답 (`build_status`):
```json
{
  "mode": "SIMULATION",
  "level": "RECOMMEND_ONLY",
  "allowed_actions": ["RECOMMEND"],
  "blocked_actions": ["SUBMIT_FOR_APPROVAL", "VIRTUAL_EXECUTE", "LIVE_EXECUTE", "FUTURES_LIVE_EXECUTE"],
  "requires_human_approval": false,
  "virtual_only": false,
  "live_execution_disabled": true,
  "futures_live_disabled": true,
  "flags": { ... },
  "matrix": { "SIMULATION": { ... }, ... },
  "notice": "AI API Key는 주문 권한이 아닙니다. ..."
}
```

UI는 본 응답으로:
- 현재 level 배지 표시
- 허용된 행동 / 차단된 행동 목록 표시
- 사람 승인 필요 / 가상 only / LIVE 비활성 배지
- 매트릭스 (Dashboard 표 형태) 표시
- "API Key는 권한이 아닙니다" 안내 문구

## 9. 향후 과제 (AI Permission Gate backlog)

- **route_order 자동 wire-in** — 기존 RiskManager 검사와 함께 본 게이트도
  명시 호출. 광범위 테스트 회귀 검증 후 별도 옵트인 PR.
- **Agent 자동 통합** — Agent Council의 `RiskOfficerAgent`가 본 게이트
  결과를 자기 결정에 carry.
- **per-strategy override** — 특정 전략은 더 보수적인 level 강제.
- **operator 승인 횟수 한도** — APPROVAL_REQUIRED에서 일정 시간 내 승인
  요청 횟수 cap.
- **audit_note 자동 영구화** — OrderAuditLog row에 별도 컬럼으로 저장.

## 10. 안전 invariant (테스트로 강제)

- `AiPermissionFlags`에 api_key / secret / account_no 필드 0건 — 권한은
  flag로만 결정.
- `evaluate_ai_permission` 시그니처에 api_key 매개변수 0건.
- `app.risk.ai_permission_gate` 모듈에 `app.brokers` / `OrderExecutor` /
  `route_order` import 0건. `place_order(` / `cancel_order(` 호출 0건.
- `AiPermissionDecision`은 frozen dataclass — 호출자가 결과를 임의 변경 불가.
- `AiAction.FUTURES_LIVE_EXECUTE`는 `enable_futures_live_trading` 별도 검사
  통과해야만 허용 — FUTURES_LIVE 활성화는 절대 원칙 3.
- LIVE flag / API Key / 계좌번호 변경 0건.
