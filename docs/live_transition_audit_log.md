# Live 전환 감사 로그 (#45 / 5-05)

> 운영자가 *언제, 어떤 조건으로* 실전 전환을 검토/거절/철회했는지 추적하는
> **append-only** 감사 로그입니다. **이 로그는 실전 전환을 승인하거나 실전
> 주문을 생성하지 않습니다** — 감사 기록은 주문 신호가 아니며, 기록이 있어도
> 실전 주문은 만들어지지 않습니다.

이 로그는 실전 전환 Gate 트랙(#41 자금 분리 · #42 Manual Approval · #43 Canary ·
#44 Paper 성과 기준)의 *검토 이력* 을 남기는 보조 장치입니다.

## 목적

- 실전 전환 관련 검토/거절/철회/메모를 시점·조건과 함께 남겨 추적성을 확보합니다.
- 누가(operator), 왜(reason), 언제(timestamp), 어떤 게이트 결과(snapshot)를 보고
  판단했는지 기록합니다.

## append-only 원칙

- 기존 record 를 **수정/삭제하지 않습니다** — update/delete 메서드·API 0건
  (POST 추가 + GET 조회만, PUT/PATCH/DELETE 없음).
- 정정이 필요하면 **새 이벤트를 추가**합니다(`LIVE_AUDIT_NOTE_ADDED` /
  `LIVE_APPROVAL_REVOKED`). `previous_audit_id` 로 이전 record 와 연결됩니다.
- `audit_id` / `created_at` 은 생성 후 변경되지 않습니다(frozen record).

## 기록 항목

| 필드 | 설명 |
|---|---|
| operator | 운영자 (필수) |
| reason | 사유 (필수) |
| created_at | timestamp (자동 생성) |
| action | 표준 action (아래) |
| risk_profile | 위험 성향 snapshot |
| symbol_whitelist | 종목 whitelist snapshot |
| max_order_notional | 최대 주문금액 snapshot |
| daily_live_limit | 일일 live 한도 snapshot |
| capital_review_snapshot | Live Capital Review(#41) snapshot |
| paper_gate_verdict | Paper Gate 성과 기준(#44) verdict |
| canary_gate_verdict | Canary Gate(#43) verdict |
| manual_approval_snapshot | Manual Approval(#42) 상태 |
| notes | 메모 |

## 표준 action

`LIVE_REVIEW_REQUESTED` / `LIVE_REVIEW_REJECTED` / `LIVE_REVIEW_READY_RECORDED` /
`LIVE_CANARY_REVIEW_REQUESTED` / `LIVE_CANARY_REVIEW_REJECTED` /
`LIVE_APPROVAL_REVOKED` / `LIVE_AUDIT_NOTE_ADDED`.

> "APPROVED" 같은 단어는 실제 주문 승인으로 오해될 수 있어 사용하지 않습니다 —
> `..._REVIEW` / `..._READY_RECORDED` 로 표현합니다.

## reason_code

| reason_code | 의미 |
|---|---|
| `LIVE_AUDIT_RECORDED` | 정상 기록됨 |
| `LIVE_AUDIT_OPERATOR_REQUIRED` | operator 누락 |
| `LIVE_AUDIT_REASON_REQUIRED` | reason 누락 |
| `LIVE_AUDIT_INVALID_ACTION` | 표준 action 아님 |
| `LIVE_AUDIT_SECRET_BLOCKED` | 민감정보 감지 → 기록 거부 |

## 감사 로그는 주문/실전 승인이 아님

- 감사 record 는 `is_order_signal=false`, 그리고 기록 후에도
  `is_live_authorization` / `broker_order_sent` / `order_created` / `auto_apply_allowed`
  는 **항상 false** 입니다(dataclass `__post_init__` 가드).
- 감사 로그가 있어도 실전 주문은 생성되지 않으며, Live Capital Review(#41) +
  Manual Approval(#42) + Canary Gate(#43) + Paper 성과 기준(#44)이 **별도로**
  필요합니다.

## secret/account 저장 금지

- operator / reason / notes / 모든 snapshot 은 기록 *전* sanitize(fail-closed)를
  통과해야 하며, API key / Secret / access_token / 계좌번호(8-2 등) 패턴이
  감지되면 **기록을 거부**합니다(`LIVE_AUDIT_SECRET_BLOCKED`). 원문은 저장되지
  않습니다.

## API

- `POST /api/governance/live-transition-audit` — 감사 이벤트 *추가*(append-only).
- `GET /api/governance/live-transition-audit?limit=100` — 최근 기록 조회.
- `PUT` / `PATCH` / `DELETE` 는 **제공하지 않습니다**.

## 사용자 확인 항목

- 감사 로그는 추적용이며 **실전 주문 승인/신호가 아닙니다.**
- 기존 기록은 수정/삭제되지 않으며, 정정은 새 이벤트로 남습니다.
- 화면/응답에 자격정보 원문이 표시되지 않습니다.
- 이 시스템은 **수익을 보장하지 않습니다.**

## 관련 파일

- `backend/app/governance/live_transition_audit.py`
- `backend/app/api/routes_governance.py` (POST/GET)
- `backend/tests/test_live_transition_audit_log.py`
- `backend/tests/test_live_transition_audit_policy.py`
- 배경: [`docs/live_manual_approval_gate.md`](live_manual_approval_gate.md),
  [`docs/live_canary_gate.md`](live_canary_gate.md),
  [`docs/paper_gate_performance_criteria.md`](paper_gate_performance_criteria.md)
