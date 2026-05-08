# Signal Explainability

> 코드: [`backend/app/explainability/`](../backend/app/explainability/)
> API: `GET /api/signals/{audit_id}/explain` ([`backend/app/api/routes_explainability.py`](../backend/app/api/routes_explainability.py))
> 테스트: [`backend/tests/test_explainability.py`](../backend/tests/test_explainability.py)

## 1. 목적

각 전략 신호와 Agent 판단에 대해 **"어떤 조건이 통과했고, 어떤 조건이
실패했으며, 왜 승인/거절/대기/차단됐는지"** 를 구조화해 저장하고 화면에
표시한다. 왜 매수/매도/거절됐는지 알아야 운영자가 전략을 개선할 수 있다.

본 패키지는 *주문 실행 레이어가 아니다* — read/write **audit 설명 레이어**.
broker / RiskManager / PermissionGate / OrderExecutor / route_order 어떤
함수도 호출하지 않으며, 기존 OrderAuditLog / AgentDecisionLog /
PendingApproval 테이블 스키마를 변경하지 않는다.

## 2. SignalReason 구조

```python
@dataclass(frozen=True)
class SignalReason:
    category: ReasonCategory   # 어느 단계가 만든 reason인가
    status:   ReasonStatus     # PASS / WARN / FAIL / BLOCKED / INFO
    message:  str              # 사람이 읽는 한 줄 (UI에 그대로 표시)
    severity: ReasonSeverity = MEDIUM   # LOW / MEDIUM / HIGH (정렬에 사용)
    source:   str | None = None         # 모듈/함수 이름 (자유 문자열)
    code:     str | None = None         # 머신 가독 코드 (검색/필터)
    details:  dict | None = None        # raw indicator 등 자유 dict
```

### 2.1 ReasonCategory (출처 단계)

| 값 | 의미 |
|---|---|
| `STRATEGY` | 전략(VolumeBreakout / PullbackRebreak / VWAPStrategy / ...)이 만든 신호 사유 |
| `SIGNAL_QUALITY` | `signal_quality` (#136) 산출 결과 — strength/confidence |
| `MARKET_REGIME` | `MarketRegimeFilter` (#32) 결정 — ALLOW/REDUCE_SIZE/WATCH_ONLY/BLOCK_NEW_BUY |
| `RISK_MANAGER` | RiskManager가 부여한 가드 결과 — REJECT/APPROVE 사유 |
| `PERMISSION_GATE` | PendingApproval 큐의 사용자 결정 사유 |
| `DATA_FRESHNESS` | stale data 가드 (#143 등) |
| `AGENT` | Agent Council 판단 사유 (185 — 10 agents) |
| `OPERATOR` | 운영자가 명시한 note |
| `OTHER` | 분류 불가 |

### 2.2 ReasonStatus (UI 배지 색)

| 값 | UI 배지 | 의미 |
|---|---|---|
| `PASS` | green | 조건 통과 |
| `WARN` | amber | 통과는 했으나 운영자/Agent가 살펴야 함 |
| `FAIL` | red | 조건 미충족 |
| `BLOCKED` | red, 강조 | 안전 가드에 의한 hard 차단 |
| `INFO` | neutral | 단순 정보 |

### 2.3 ExplainStatus (전체 최종 상태)

`SignalExplanation.final_status`는 다음 중 하나:

- `APPROVED` — 모든 PASS, 주문 결정 통과
- `PENDING` — PermissionGate가 승인 대기
- `REJECTED` — 어떤 단계에서든 FAIL/BLOCKED
- `WATCH` — 신호는 있으나 진입 보류 (PASS + WARN 공존 또는 WARN만)
- `UNKNOWN` — explanation 미충분

## 3. 단계별 reason 출처

`compose_signal_explanation(...)`에 다음 입력을 주면 카테고리별 SignalReason
으로 정규화되어 합쳐진다:

| 입력 인자 | 출처 |
|---|---|
| `signal` | StrategySignal 또는 SignalExplanation — 전략 단의 reasons |
| `quality_result` | `signal_quality` 결과 — strength/confidence |
| `regime_decision` | `MarketRegimeFilter.evaluate()` 반환값 |
| `risk_result` | RiskManager 결정 (decision + reasons) |
| `permission_result` | PermissionGate 결정 (status + reasons) |
| `agent_decision` | Agent 판단 (decision + reasons) |
| `operator_note` | 운영자 명시 note (str) |

각 단계가 None이면 skip — 호출자가 가진 단계만 합성하면 된다. 결과는
`SignalExplanation` 객체로, UI/Agent/Audit가 모두 같은 데이터를 소비한다.

## 4. "설명 없는 주문 금지" 정책

### 핵심 원칙

> **주문 또는 approval 등록 전 explanation에 최소 하나 이상의 reason이
> 있어야 한다.**

### 강제 방식

```python
from app.explainability import require_explanation_before_order, MissingExplanationError

# 주문 / approval 등록 직전:
try:
    require_explanation_before_order(explanation)
except MissingExplanationError as e:
    # 설명 없는 신호 → 주문 거부
    ...
```

- explanation이 None이거나 reasons가 빈 list이면 `MissingExplanationError`
  raise.
- `raise_on_empty=False`로 호출하면 bool 반환 (사용자 결정).

### 본 PR의 적용 범위

- 본 PR은 helper + tests + docs로 정책을 *명시*만 한다.
- 기존 주문 흐름(`route_order` / `RiskManager` / `PermissionGate`)에
  자동 적용하지는 않는다 — 기존 동작을 깨지 않기 위해. 강제 적용은 별도
  옵트인 PR.
- 운영자/Agent가 새로 작성하는 흐름은 `require_explanation_before_order`를
  사전 가드로 두는 것을 권장.

## 5. UI 패널 설명

`SignalExplanation.grouped_by_status()`는 PASS/WARN/FAIL/BLOCKED/INFO 별로
SignalReason 리스트를 분리해 반환. UI 패널은 다음 카드로 구성 권장:

| 카드 | 색 | 내용 |
|---|---|---|
| 신호 요약 | neutral | symbol / strategy / action / final_status / summary |
| 통과 조건 | green | PASS reason 리스트 |
| 주의 조건 | amber | WARN reason 리스트 |
| 실패/차단 조건 | red/coral | FAIL / BLOCKED reason 리스트 |
| Agent comment | neutral | Agent 카테고리 reasons + AI confidence |
| Audit trace | neutral | `audit_trace_id`로 audit 상세 링크 |

UX 가이드:
- JSON dump 표시 금지 — 사람이 읽기 쉬운 카드/리스트.
- 모바일에서도 readable.
- "왜 매수/거절됐는지" 3초 안에 이해 가능.

본 PR에는 frontend 컴포넌트는 포함하지 않았다 — `/api/signals/{audit_id}/
explain` endpoint와 `grouped` 응답 필드가 카드 분리에 직접 사용 가능한
형태로 노출된다. UI 컴포넌트 도입은 backlog 후속.

## 6. Friendly 문구 예시

### PASS
- "거래대금이 최근 평균 대비 충분히 증가했습니다."
- "가격이 VWAP 위에서 유지되고 있습니다."
- "데이터 freshness가 정상입니다."

### WARN
- "당일 상승폭이 커서 추격 위험이 있습니다."
- "변동성이 커져 포지션 크기 축소가 필요합니다."

### FAIL
- "현재가는 VWAP에서 너무 멀어 추격 매수 위험이 큽니다."
- "시장 국면이 RISK_OFF라 신규 매수를 차단했습니다."
- "시세 데이터가 오래되어 신호를 폐기했습니다."

### BLOCKED
- "RiskManager 기준을 통과하지 못했습니다."
- "PermissionGate 승인 조건을 충족하지 못했습니다."

전략/필터/Agent 모듈은 reason 메시지를 직접 한국어로 surface하므로
UI에서 추가 변환 없이 그대로 표시 가능.

## 7. Agent 분석 연계

- `PostTradeReviewAgent` (Agent Council #185 — 10 agents)는
  `SignalExplanation.to_dict()` 결과를 학습/복기에 사용 — "어떤 reason 조합
  의 신호가 결국 어떤 결과를 냈나" 분석.
- `RiskOfficerAgent`는 본 SignalExplanation을 사전 검토 input으로 활용 가능.
- AI 자동매매 흐름에는 강하게 묶지 않음 — 본 PR은 *advisory + 표시* 레이어
  까지만.

## 8. /api/signals/{audit_id}/explain

read-only 엔드포인트. `OrderAuditLog` row를 읽어
`extract_reasons_from_audit_row`로 SignalExplanation 합성 후 반환.

응답 (`SignalExplainOut`):
```json
{
  "audit_trace_id": 42,
  "symbol": "005930",
  "strategy": "VolumeBreakout",
  "action": "BUY",
  "final_status": "APPROVED",
  "summary": "거래대금 증가 / VWAP 상단 / audit decision = APPROVED",
  "reasons": [{"category":"RISK_MANAGER","status":"PASS",...}, ...],
  "indicators": null,
  "risk_notes": [],
  "operator_note": null,
  "grouped": {
    "PASS":    [...],
    "WARN":    [...],
    "FAIL":    [],
    "BLOCKED": [],
    "INFO":    [...]
  }
}
```

DB write 없음. broker / RiskManager / PermissionGate / OrderExecutor 호출
없음. audit_id 미존재 시 404.

## 9. 한계 및 향후 과제

- 모든 legacy signal이 즉시 explanation을 갖지는 않음 — `OrderAuditLog`의
  `reasons` 필드가 비어 있던 row는 audit decision만 reason으로 노출.
- 다양한 카테고리의 reason을 동시에 수집하려면 `compose_signal_explanation`
  호출자 측에서 의도적으로 모든 단계 입력을 모아야 함.
- "설명 없는 주문 금지" 정책의 강제 적용 (route_order에 `require_*` 가드)은
  별도 옵트인 PR.
- Frontend Explainability Panel UI 컴포넌트는 본 PR 범위 외 — backlog 후속.
- Agent Council의 chain decision을 본 SignalExplanation에 자동 carry하는
  통합도 후속 (`AgentDecisionLog` 외래키 결합).

## 10. 안전 invariant

- broker / RiskManager / PermissionGate / OrderExecutor 어떤 모듈도 import
  하지 않음 (테스트 가드 — `test_module_does_not_import_broker_or_route`,
  `test_routes_does_not_import_broker_or_route`).
- `route_order(` / `place_order(` 함수 호출 0건.
- DB write 없음 — `extract_reasons_from_audit_row`는 read-only,
  `/api/signals/{audit_id}/explain`는 GET만.
- 기존 OrderAuditLog / AgentDecisionLog / PendingApproval 스키마 변경 0건.
