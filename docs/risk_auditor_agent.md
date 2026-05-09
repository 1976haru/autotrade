# Risk Auditor Agent (#54)

본 문서는 [`RiskAuditorAgent`](../backend/app/agents/risk_auditor.py)의 정책 contract를 정의한다. `OrderAuditLog`, `EmergencyStopEvent`, `AgentDecisionLog`을 read-only로 분석해 *장중 리스크 advisory*를 생성하는 안전 감독 Agent.

**본 Agent는 주문 신호를 만들지 *않는다*.** BUY/SELL/HOLD 반환 금지, approval queue 등록 금지, broker 호출 금지. 또한 **emergency_stop을 *직접 토글하지 않는다*** — 운영자에게 권고만 한다 (`PAUSE_TRADING_RECOMMENDED`, `EMERGENCY_STOP_RECOMMENDED`). 실제 긴급정지 / 거래 중단 / 포지션 청산은 운영자가 기존 Kill Switch UI에서 수동으로 수행한다.

## 1. 목적

장중에 다음 위험 신호가 누적되는지 감사:
- 일일 손실 한도 임박 / 초과
- 거부 주문 / 중복 주문 다수 (전략 오작동 의심)
- 시세 stale (데이터 신선도 문제)
- AI 과신 / AI 저신뢰 폭주
- emergency_stop on/off flapping (운영자 혼란)
- Agent WARN/REJECT 폭주
- broker error / 비정상 거부율
- 선물 margin / liquidation 위험

위 신호 중 어느 것도 본 Agent가 *직접 조치*하지는 않는다. **운영자가 의사결정의 마지막 단계**이며, 본 Agent는 *조기 경보 + 운영자 친화적 요약*을 제공한다 — "중지권한 우선" 원칙.

## 2. 입력 데이터

### `OrderAuditLog` (모든 주문 결정의 audit 진실)
- `decision`, `decision_reasons`, `created_at`
- `client_order_id`, `chain_id`
- `signal_confidence` (AI 후보 confidence carry, optional)
- `source` (#40 — STRATEGY/AI/MANUAL/...)

### `EmergencyStopEvent` (#37 Kill Switch 이력)
- `level`, `reason`, `enabled`, `created_at`
- on/off flapping 감지 입력

### `AgentDecisionLog` (#51 Agent 결정 이력)
- `outcome`, `agent`, `created_at`
- WARN / REJECT 폭주 감지 입력

### Caller가 주입하는 외부 metric (DB에 없는 값)
- `daily_realized_pnl`: 운영자가 별도 모듈 (PnL aggregator)에서 계산해 전달
- `max_daily_loss`: Settings 또는 운영자가 입력한 한도
- `margin_risk_pct`, `futures_liquidation_pct`: 선물 활성화 시 별도 모듈에서 계산해 전달 (현재 stub — 모두 None 가능)
- `window_seconds`: 분석 윈도우 (default 3600s)

### DB read-only helpers
```python
load_recent_audit_rows(db, since=..., limit=500)
load_recent_emergency_events(db, since=..., limit=100)
load_recent_agent_decisions(db, since=..., limit=200)
```
- 모두 read-only SELECT
- INSERT / UPDATE / DELETE 0건 (정적 grep 가드)
- caller가 미리 조회한 row를 `audit_risk()`에 전달

## 3. 출력 데이터 (`RiskAuditorReport`)

```python
@dataclass(frozen=True)
class RiskAuditorReport:
    audit_level:                       AuditLevel       # GREEN/YELLOW/ORANGE/RED
    risk_score:                        int              # 0-100 (clamped)
    events:                            list[RiskEvent]
    pause_trading_recommended:         bool             # advisory only
    emergency_stop_recommended:        bool             # advisory only
    recommended_stop_reason:           EmergencyStopReason | None
    summary_lines:                     list[str]        # 운영자용 자연어 요약
    total_audit_rows_inspected:        int
    total_emergency_events_inspected:  int
    total_agent_decisions_inspected:   int
    is_order_signal:                   bool             # *항상 False* (가드)
    created_at:                        datetime
```

### `AuditLevel` enum (4단계, BUY/SELL/HOLD 0개)
| 값 | 의미 | 트리거 |
|---|---|---|
| `GREEN` | 정상 | 이벤트 0건 |
| `YELLOW` | 경고 | INFO/WARN 이벤트 있음, score < 40 |
| `ORANGE` | 주의 | HIGH 이벤트 또는 score ≥ 40 → `pause_trading_recommended=True` |
| `RED` | 긴급 | CRITICAL 이벤트 또는 score ≥ 70 → `emergency_stop_recommended=True` |

### `RiskEventType` enum (12종)
| 값 | 의미 |
|---|---|
| `daily_loss_breach` | 일일 손실 한도 임박 (≥ 80%) 또는 초과 (≥ 100%) |
| `repeated_order_failure` | REJECTED 주문 다수 — 전략/리스크 오작동 의심 |
| `duplicate_order_burst` | OrderGuard DUPLICATE 폭주 |
| `data_stale` | 시세 timestamp stale 거부 다수 |
| `ai_overconfidence` | confidence 높은데 REJECTED 폭주 (모델 calibration 의심) |
| `ai_low_confidence_burst` | 낮은 confidence AI 후보 폭주 |
| `emergency_stop_flapping` | 단기간 emergency on/off 다수 (운영자 혼란) |
| `agent_warn_burst` | Agent WARN/REJECT 폭주 |
| `margin_risk` | 선물 margin_risk_pct 위험 (HIGH 30% / CRITICAL 50%) |
| `futures_liquidation_risk` | 선물 liquidation distance 임계 (HIGH 7% / CRITICAL 3%) |
| `broker_error_burst` | broker error / connectivity 거부 다수 |
| `abnormal_rejection_rate` | 전체 거부율 비정상 |

### `RiskEventSeverity`
`INFO` (2점) / `WARN` (8점) / `HIGH` (20점) / `CRITICAL` (40점) — `risk_score`는 합산 후 0-100 clamp.

## 4. 안전 원칙 (절대 invariant)

| 원칙 | 가드 |
|---|---|
| 주문 신호 아님 | `is_order_signal=False` 불변 (`__post_init__` ValueError) |
| BUY/SELL/HOLD 반환 금지 | `AuditLevel` enum에 해당 값 0개 |
| approval queue 직접 등록 금지 | `submit_candidate` / `route_order` import 0건 |
| broker / OrderExecutor 호출 금지 | 정적 grep 가드 |
| **emergency_stop 직접 토글 금지** | `risk.emergency_stop = True` / `.set_emergency_stop(` 호출 0건 (정적 grep 가드) |
| RiskManager 상태 변경 금지 | RiskManager mutation 호출 0건 |
| DB INSERT/UPDATE/DELETE 금지 | 정적 grep 가드 (read-only SELECT only) |
| 외부 HTTP / AI 호출 금지 | httpx / requests / urllib3 / anthropic / openai import 0건 |

`emergency_stop_recommended=True`일 때도 본 Agent는 *플래그를 켜지 않는다* — `recommended_stop_reason`을 carry해 운영자에게 *권고만* 보여준다. 실제 토글은 `POST /api/risk/emergency-stop`을 운영자가 별도 호출.

## 5. 임계값 표

| 상수 | 기본값 | 의미 |
|---|---|---|
| `_DAILY_LOSS_HIGH_PCT` | 80 | 손실/한도 ≥ 80% → HIGH |
| `_DAILY_LOSS_CRITICAL_PCT` | 100 | 손실/한도 ≥ 100% → CRITICAL |
| `_REJECTED_BURST_THRESHOLD` | 5 | 윈도우 내 REJECTED 5건 → HIGH |
| `_REJECTED_HIGH_THRESHOLD` | 10 | REJECTED 10건 → CRITICAL |
| `_DUPLICATE_BURST_THRESHOLD` | 3 | DUPLICATE 3건 → HIGH |
| `_BROKER_ERROR_THRESHOLD` | 3 | broker error 3건 → HIGH |
| `_AGENT_WARN_THRESHOLD` | 5 | Agent WARN/REJECT 5건 → HIGH |
| `_AI_LOW_CONF_THRESHOLD` | 30 | confidence < 30 |
| `_AI_LOW_CONF_BURST` | 5 | 위 조건 5건 → WARN |
| `_AI_HIGH_CONF_REJECTED` | 80 | confidence ≥ 80인데 REJECTED |
| `_AI_HIGH_CONF_BURST` | 3 | 위 조건 3건 → HIGH (calibration 의심) |
| `_EMERGENCY_FLAPPING_THRESHOLD` | 4 | 윈도우 내 emergency toggle 4건 → HIGH |
| `_MARGIN_RISK_PCT_HIGH` / `_CRITICAL` | 30.0 / 50.0 | 선물 margin |
| `_LIQUIDATION_DISTANCE_HIGH` / `_CRITICAL` | 7.0 / 3.0 | liquidation 거리 (가까울수록 위험) |

## 6. API surface

| Endpoint | 메서드 | 의미 |
|---|---|---|
| `/api/agents/risk-auditor/report` | GET | DB 기반 read-only 리포트. 쿼리 파라미터: `window_seconds`, `daily_realized_pnl`, `max_daily_loss`, `margin_risk_pct`, `futures_liquidation_pct` |
| `/api/agents/risk-auditor/mock` | POST | 결정적 mock — 외부 metric / 이벤트 카운트만 받아 deterministic report 반환 (테스트 / Demo) |

두 endpoint 모두 broker 호출 0건, audit row 0건, DB write 0건.

## 7. UI

[`frontend/src/components/tabs/RiskAuditorCard.jsx`](../frontend/src/components/tabs/RiskAuditorCard.jsx) — Dashboard / Agent / Risk 탭에 마운트.

**필수 표시**:
- "주문 신호 아님 · 안전 리포트" 배지
- 운영자 요약 3줄 (audit_level + 주요 위험 + 권고 액션)
- audit_level 색상 (GREEN/YELLOW/ORANGE/RED)
- risk_score (0-100)
- 위험 이벤트 목록 (severity별 색상)
- `EMERGENCY_STOP_RECOMMENDED` 박스 (켜졌을 때만) — "운영자가 Kill Switch UI에서 수동 토글" 안내 문구 + recommended_stop_reason
- `PAUSE_TRADING_RECOMMENDED` 박스 (STOP 미권고일 때만)
- "본 리포트는 *주문 신호가 아닙니다*" disclaimer

**금지된 UI 요소** (테스트로 lock):
- BUY / SELL / HOLD 버튼
- 매수 / 매도 CTA
- **emergency_stop 직접 토글 버튼** — 본 카드에서 절대 제공하지 않음. Kill Switch UI는 #37 Risk 탭에서만.

## 8. Agent 관계

| Agent | 본 RiskAuditor와의 관계 |
|---|---|
| **MarketObserverAgent** (#52) | 시장 regime이 STRESS일 때 본 Agent의 임계값을 운영자가 보수적으로 조정 가능 |
| **NewsTrendAgent** (#53) | overheating / used_for_order 위반은 본 Agent가 별도로 다루지 않음 — News Agent가 자체 carry |
| **StrategySelectionAgent** | `pause_trading_recommended=True`일 때 운영자가 신규 진입 회피 |
| **ChiefTradingAgent** | `risk_score`와 `audit_level`을 운영자 dashboard에 노출 |

본 Agent는 다른 Agent에게 *판단을 강제하지 않으며*, 어떤 caller도 본 리포트를 바탕으로 자동 주문/자동 정지를 해서는 안 된다.

## 9. 변경 시 동기화

- 새 `RiskEventType` 값 추가 → 본 문서 §3 + `_derive_stop_reason` 매핑 + 테스트
- 임계값 조정 → 본 문서 §5 + 테스트 boundary 갱신
- 새 입력 metric → `RiskAuditorInput` + Pydantic schema + 본 문서 §2
- emergency_stop 직접 토글 *금지 invariant* 변경 → 절대 금지. 토글이 필요하면 운영자가 기존 Kill Switch UI를 사용
- 외부 metric source(PnL aggregator, margin calculator) 추가 → 본 문서 §2 + caller 책임 명시

## 관련 문서

- [`agent_architecture.md`](agent_architecture.md) — 6개 표준 Agent 역할 contract (#51)
- [`agent_design.md`](agent_design.md) — Agent 분리 정책
- [`emergency_stop_policy.md`](emergency_stop_policy.md) — Kill Switch (#37) — 본 Agent의 권고 대상
- [`risk_policy.md`](risk_policy.md) — RiskManager 평가 순서
- `app/agents/risk_auditor.py` — 본 Agent 구현
- `CLAUDE.md` — 절대 원칙 1번 (AI 직접 호출 금지) + #37 Kill Switch 정책
