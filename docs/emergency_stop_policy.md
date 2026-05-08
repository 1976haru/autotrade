# Emergency Stop / Kill Switch Policy (#37)

> 코드: [`backend/app/risk/emergency_stop.py`](../backend/app/risk/emergency_stop.py)
> 라우트: [`backend/app/api/routes_risk.py`](../backend/app/api/routes_risk.py) — `POST /risk/emergency-stop`, `GET /risk/emergency-stop/status`, `GET /risk/emergency-stop/cancel-candidates`, `GET /risk/emergency-stop/liquidation-candidates`
> 테스트: [`backend/tests/test_emergency_stop_kill_switch.py`](../backend/tests/test_emergency_stop_kill_switch.py)
> Frontend: [`frontend/src/components/common/KillSwitchPanel.jsx`](../frontend/src/components/common/KillSwitchPanel.jsx)

## 1. 목적

> **비상상황에서 단계적으로 멈추기.** 한 번에 모든 것을 자동 청산하면 호가
> 공백 / 급락 상황에서 손실이 더 커진다. 운영자가 단계별로 가드를 강화하고,
> 청산은 *수동 승인*으로 진행한다.

## 2. 3단계 정책

| Level | 효과 | RiskManager 동작 |
|---|---|---|
| `OFF` | 정상 운영 | `emergency_stop=False`, 모든 가드 정상 |
| `LEVEL_1` | 신규 매수(BUY) 즉시 차단 | `emergency_stop=True` → `evaluate_order` 모든 신규 주문 REJECTED |
| `LEVEL_2` | + **미체결 취소 후보 표시** | LEVEL_1 + `/cancel-candidates` 활성 (read-only candidate list) |
| `LEVEL_3` | + **청산 후보 표시** | LEVEL_2 + `/liquidation-candidates` 활성 (read-only) |

기존 `emergency_stop` boolean과 동기화: LEVEL_1+ 일 때 True, OFF일 때 False.
runtime은 in-memory — restart 시 자동으로 OFF로 reset (audit row가 영구화된
source of truth).

## 3. 자동 전량청산 금지

**자동 전량청산은 구현하지 않는다.** 본 PR(#37) + 향후 PR 모두 절대 원칙 5/6/7:

- LEVEL_3에서도 청산은 **read-only candidate list**만 표시.
- 운영자가 후보를 보고 **수동 승인**으로 청산을 트리거.
- 호가 공백 / 급락 상황에서 시장가 전량청산은 위험 — 손실이 더 커지는
  역효과 (CLAUDE.md '손실 방어 우선' 원칙).
- 본 모듈은 broker.cancel_order / broker.place_order / route_order 어떤
  함수도 호출하지 않는다 — 테스트 가드 (`TestSafety::test_module_does_not_
  call_cancel_or_place_order`).

## 4. BUY와 SELL/청산 차이

| 한도 도달 | BUY | SELL/청산 |
|---|---|---|
| LEVEL_1+ 활성 | 즉시 REJECTED (기존 `emergency_stop` 동작) | 현재는 동일 차단 — 향후 옵트인 별도 정책 |

**현재 동작**: 기존 `emergency_stop=True`는 *모든* 주문을 차단한다 (route_order
경유). 본 PR은 *3단계 표시 모델 + 후보 surface*만 도입했다. side-aware 정책
(SELL은 통과, BUY만 차단)은 별도 옵트인 PR — 기존 테스트 광범위 호환성
유지를 위해 본 PR에서는 변경하지 않는다 (체크리스트 #37 절대 원칙 8/9).

운영 정책: 운영자가 LEVEL_2/3에서 *청산 후보*를 보고 별도 cancel/sell route
를 통해 수동 진행. 그 route는 별도 옵트인 PR.

## 5. Reason taxonomy (`EmergencyStopReason`)

| 코드 | 의미 |
|---|---|
| `manual_operator` | 운영자 수동 정지 |
| `daily_loss_limit` | 일일 손실 한도 도달 |
| `data_stale` | 시세 stale 검출 (#143) |
| `broker_error` | broker 응답 이상 |
| `repeated_order_failure` | 연속 주문 실패 (#182 auto-stop) |
| `abnormal_slippage` | 비정상 슬리피지 |
| `agent_warning` | AI Agent 경고 |
| `margin_risk` | 선물 증거금 위험 |
| `futures_liquidation_risk` | 선물 강제청산 임박 |

신규 reason은 [`backend/app/risk/emergency_reasons.py`](../backend/app/risk/emergency_reasons.py)
의 `EmergencyStopReason` enum에 추가.

## 6. Audit (history / summary)

`POST /risk/emergency-stop` 토글 시 `EmergencyStopEvent` row가 추가된다:
- `enabled`: True/False
- `level`: OFF / LEVEL_1 / LEVEL_2 / LEVEL_3 (#37 신규 컬럼)
- `decided_by`: 운영자 식별자
- `note`: 자유 메모
- `reason_code`: 위 enum 값

조회:
- `GET /risk/emergency-stop/history` — 최근 토글 이력 (최신순). #37 신규 `level`
  필드 포함; legacy NULL row는 enabled=True/False에 따라 LEVEL_1/OFF로 정규화.
- `GET /risk/emergency-stop/summary` — 활성 여부 + reason별 카운트 + 토글 수.

## 7. 새 endpoint들 (#37)

### `GET /risk/emergency-stop/status`
현재 level + reason_code + decided_by + active_since + 후보 카운트:
```json
{
  "level": "LEVEL_2",
  "emergency_stop": true,
  "reason_code": "data_stale",
  "decided_by": "ops1",
  "note": "stale price spike",
  "active_since": "2026-05-09T08:00:00+00:00",
  "cancel_candidate_count": 3,
  "liquidation_candidate_count": 0
}
```
broker 호출 회피 — `liquidation_candidate_count`는 별도 endpoint에서 명시 호출.

### `GET /risk/emergency-stop/cancel-candidates`
LEVEL_2가 표시할 미체결 / 승인 대기 주문. 두 소스 합산:
- `PendingApproval` 중 `status=PENDING` (운영자 승인 큐).
- `OrderAuditLog` 중 `decision=NEEDS_APPROVAL`이지만 PendingApproval row가 없는
  drift case.

### `GET /risk/emergency-stop/liquidation-candidates`
LEVEL_3가 표시할 청산 후보. broker.get_positions()의 quantity > 0 항목.
응답에 `note: "자동 청산은 비활성화되어 있습니다..."` 명시.

## 8. RiskManager 연계

- `RiskManager.kill_switch_level` (in-memory) — 현재 level 추적.
- `RiskManager.emergency_stop` (boolean) — LEVEL_1+ 일 때 True (기존 호환).
- `apply_kill_switch_to_risk(risk, level)` — 두 필드 동시 동기화.
- `evaluate_order` / `check_order` 자체 로직은 변경되지 않는다 — 기존
  `emergency_stop=True` hard-reject가 그대로 LEVEL_1+ 일 때 활성화.

## 9. Frontend UI

- `frontend/src/components/common/KillSwitchPanel.jsx` — 3단계 read-only
  status panel.
- StrategyRisk 탭의 BackendPolicyCard 다음에 mount.
- 표시: 현재 level 배지 + 3단계 row 시각화 (active row 강조) + 후보 카운트
  + 위험 경고 문구.
- **자동 청산 / 자동 취소 버튼은 절대 만들지 않는다** — 테스트 가드 (`button[data-testid*="liquidate"]` / `cancel-all"]` 등 부재 검증).

## 10. 실제 LIVE 전 확인

- [ ] **미체결 취소 API는 수동승인 필수** — 별도 옵트인 PR. 본 PR에는 자동
      취소 흐름 없음.
- [ ] **청산 후보 자동 주문 금지** — UI / API 어디에도 자동 청산 진입점이
      없음을 검증.
- [ ] **broker reconciliation** (#212) — broker view ↔ audit view drift가
      청산 후보에 영향 미치지 않는지 검증.
- [ ] **side-aware 정책** — SELL 통과 정책을 옵트인하기 전 광범위 테스트
      회귀 검증 필수.
- [ ] **legacy row 처리** — level NULL row가 LEVEL_1로 정규화되는지 확인
      (테스트로 가드).

## 11. 향후 과제 (Kill Switch backlog)

- **수동 cancel 흐름** — LEVEL_2 후보를 운영자가 선택 → 단일 cancel API
  호출 → audit. 별도 옵트인 PR.
- **수동 청산 흐름** — LEVEL_3 후보를 운영자가 선택 → 시장가 SELL 주문
  생성. broker 단의 LIVE 활성화 + 별도 PR.
- **side-aware emergency_stop** — SELL/EXIT은 통과시키는 정책. 기존 테스트
  광범위 영향 검증 후.
- **자동 emergency_stop trigger** — `daily_loss_limit` / `consecutive_loss_
  limit` 도달 시 자동 LEVEL_1 토글 (#36과 통합 가능).
- **운영자 acknowledgement TTL** — 일정 시간 내 운영자 ack 없으면 자동
  LEVEL 상승.
- **개인 알림** (Slack/SMS) — LEVEL 변경 시 외부 알림.

## 12. 안전 invariant

- broker / RiskManager / PermissionGate / OrderExecutor / route_order 어떤
  함수도 본 모듈이 직접 호출하지 않음 — 테스트 가드.
- 자동 cancel_order / 자동 place_order / 자동 청산 호출 0건 — 테스트로
  영구 강제.
- DB write는 `POST /emergency-stop`이 EmergencyStopEvent 한 row만 추가
  (no-op 토글은 skip).
- LIVE flag / API Key / Secret / 계좌번호 변경 0건.
- 기존 `emergency_stop` ON/OFF API 응답 호환성 유지 (level 필드는 additive).
