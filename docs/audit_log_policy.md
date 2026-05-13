# Audit Log 정책 (체크리스트 #68)

## 1. 목적

문제 발생 시 *원인 추적이 가능하도록* 모든 신호 / 주문 요청 / 승인 / 거절 /
AI 제안 / 리스크 차단 / 긴급정지 등을 영구화한다. **누가 무엇을 왜 주문했는지**
사후 분석으로 재구성 가능해야 한다.

본 PR은 기존 도메인 테이블을 *대체하지 않고* 그 위에 cross-cutting timeline +
Secret redaction + append-only invariant facade를 추가한다.

## 2. 절대 원칙 (CLAUDE.md + #68)

| 원칙 | 강제 위치 |
|---|---|
| audit row **append-only** — 삭제 / 수정 금지 | `app/audit/events.py`에 delete 함수 0개. routes에 DELETE 엔드포인트 0개. `archived` flag 변경만 가능 (test로 lock) |
| Secret 패턴 발견 시 **fail-closed 거부** (redaction 아님) | `log_audit_event`가 summary / reason / details / actor / symbol / strategy 모두 검사. 매칭 시 `SecretLeakError`로 raise — 호출자가 sanitize 후 재시도 |
| audit hook 실패가 핵심 흐름을 깨지 않음 | emergency-stop API의 `log_audit_event` 호출은 `try/except Exception`로 감싼다. 알림 hook과 동일한 안전 정책 (테스트로 lock) |
| 기존 audit 테이블 schema 변경 0건 | OrderAuditLog / PendingApproval / AgentDecisionLog / EmergencyStopEvent / VirtualOrder / FuturesOrderAuditLog 모두 그대로. 본 PR은 *새 audit_event 테이블 1건*만 추가 |
| 본 모듈은 broker / OrderExecutor / route_order 호출 0건 | 정적 grep + AST 검증 (`test_module_does_not_import_broker_or_executor`) |
| frontend에 *삭제 / 수정 버튼 0개* | `AuditEventTimelineCard`에 archive 버튼만. 테스트로 invariant lock |

## 3. 감사 대상

| Kind (EventType) | 발생 위치 (예시) | severity 기본 |
|---|---|---|
| `SIGNAL` | Strategy / Agent 신호 생성 | INFO |
| `ORDER_REQUEST` | route_order 진입 시점 | INFO |
| `APPROVAL_DECISION` | PermissionGate approve / reject / cancel / expire | INFO / WARN |
| `RISK_BLOCK` | RiskManager REJECTED / BLOCKED | WARN |
| `AI_PROPOSAL` | ExecutionRecommender / AI assist | INFO |
| `EMERGENCY_STOP` | POST /api/risk/emergency-stop | CRITICAL (ON) / INFO (OFF) |
| `VIRTUAL_ORDER` | VirtualOrder 라이프사이클 (후속 hook) | INFO |
| `FUTURES_RISK` | FuturesRiskManager 평가 결과 (후속 hook) | WARN / CRITICAL |
| `NOTIFICATION` | Telegram 발송 시도 결과 (후속 hook) | INFO / CRITICAL |
| `OPERATOR_NOTE` | POST /api/audit/events — 운영자 수동 메모 | INFO |
| `STRATEGY_CHANGE` | 전략 / 파라미터 변경 (후속 hook) | INFO |
| `DATA_QUALITY` | 데이터 freshness / quality 이벤트 (후속 hook) | WARN |
| `SYSTEM` | startup / shutdown / migration 등 | INFO |

본 PR은 *EMERGENCY_STOP + OPERATOR_NOTE* hook을 연결하고, 나머지는 facade /
builder를 갖춰 후속 PR에서 점진적으로 hook 추가 (CLAUDE.md '큰 기능은 작은
PR 단위로').

## 4. 기존 도메인 테이블

본 PR은 *건드리지 않는다* — 데이터 / 스키마 / 인덱스 모두 그대로.

| 테이블 | 정의 | 책임 |
|---|---|---|
| `order_audit_log` | OrderAuditLog | 모든 주문 결정 + 체결 + AI 메타 + archival flag |
| `pending_approval` | PendingApproval | 결재 큐 + TTL EXPIRED + attempts |
| `ai_analysis_log` | AiAnalysisLog | AI read-only 분석 결과 |
| `emergency_stop_event` | EmergencyStopEvent | 토글 이력 + reason_code + level |
| `virtual_order` | VirtualOrder | 가상 주문 7-state 라이프사이클 |
| `futures_order_audit_log` | FuturesOrderAuditLog | 선물 주문 별도 audit |
| `agent_decision_log` | AgentDecisionLog | 10-agent council 결정 영구화 |
| `agent_memory` | AgentMemory | 운영자 / Agent 학습 저장소 (#68 이전 PR) |
| **`audit_event`** | **AuditEvent (#68 신규)** | **cross-cutting timeline + Secret 거부** |

`audit_event`는 위 도메인 테이블을 *대체하지 않으며*, `target_kind` /
`target_id` 컬럼으로 원본 row를 참조한다.

## 5. log_audit_event 유틸

```python
from app.audit import log_audit_event, EventType, Severity, SourceKind

# 호출자 안전 — 본 함수는 SecretLeakError만 raise. 다른 예외는 발생 안 함.
try:
    log_audit_event(
        db,
        event_type=EventType.RISK_BLOCK,
        summary="risk manager blocked BUY 005930",
        severity=Severity.WARN,
        source=SourceKind.STRATEGY,
        actor="agent-1",          # 누가
        symbol="005930",
        strategy="sma_crossover",
        reason="max_order_notional exceeded",
        target_kind="OrderAuditLog",
        target_id=audit_row.id,    # 원본 row 참조
        details={
            "notional":   1_500_000,
            "limit":      1_000_000,
            "requested_by_ai": False,
        },
    )
except SecretLeakError as exc:
    # 호출자가 details에 Secret을 끼웠음 — sanitize 후 재시도 또는 skip
    logger.warning("audit event blocked by secret leak: %s", exc)
```

### Builder helpers

자주 쓰이는 패턴은 `build_*` helper로 노출:
- `build_signal_event(...)` — 전략 / Agent 신호
- `build_risk_block_event(...)` — RiskManager 차단
- `build_ai_proposal_event(...)` — AI 제안 (`is_order_intent=False` invariant)
- `build_emergency_stop_event(...)` — 긴급정지 토글
- `build_approval_decision_event(...)` — 결재 큐 결정

이들은 `AuditEventInput` dataclass를 반환하므로 caller가 다시 dict로 풀어
`log_audit_event(...)`에 넘긴다 (장기적으로 `log_audit_event(input=...)` 단일
형태로 통합 검토).

## 6. Secret redaction 정책

`log_audit_event`는 summary / reason / details / actor / symbol / strategy를
재귀 검사 후 **fail-closed 거부**한다 (redaction이 아닌 *거부*).

검사 패턴:
- OpenAI / Anthropic key (`sk-...`, `ANTHROPIC_API_KEY=...`)
- KIS app key / app secret / account no (`PST...`, `KIS_APP_KEY=...`)
- Bearer token
- Telegram bot token shape (`12345678:AAH-...`) + `TELEGRAM_BOT_TOKEN=` / `TELEGRAM_CHAT_ID=`
- 한국 계좌번호 (`501-86-66710` 등)
- 주민등록번호 (`123456-1234567`)
- 신용카드 16자리

매칭 시 `SecretLeakError`로 raise — 호출자가 sanitize한 깨끗한 메시지로 재시도
해야 한다. 본 PR은 *조용한 redaction을 의도적으로 피한다* — Secret 누출이
silent하게 audit row에 들어가는 사고를 차단 (CLAUDE.md '감사 로그 우선' +
'Secret hygiene').

## 7. 삭제 / 수정 방지

| 보호 계층 | 위치 |
|---|---|
| Python 모듈 — delete 함수 0개 | `app/audit/events.py`의 public 멤버에 `delete*` / `remove*` / `drop*` 0개 (테스트로 lock) |
| HTTP API — DELETE 엔드포인트 0개 | `routes_audit.py`에 `@router.delete` decorator 0건. `/openapi.json` 검사 테스트로 lock |
| frontend — 삭제 버튼 0개 | `AuditEventTimelineCard`에 archive 버튼만. invariant 테스트가 button textContent를 `/삭제\|수정\|Delete\|Edit\|Remove/` 매칭 0건으로 lock |
| Archive는 *멱등* | `archive_event` / `PATCH /api/audit/events/{id}/archive`는 이미 archived인 row에 호출 시 archived_by / note를 *덮어쓰지 않음* |
| DB column-level | `archived` / `archived_at` / `archived_by` / `archive_note`만 update 대상. 본 PR은 이 외 컬럼을 update하는 코드 경로를 추가하지 않음 |

archive는 *삭제의 대체*다 — row는 영구 보존되고 운영자가 `include_archived=
true` 쿼리로 다시 본다.

## 8. AI 상세 로그 기준

AI 이벤트(`AI_PROPOSAL`, `RISK_BLOCK` with `requested_by_ai=true`)는 details에
다음을 carry:

| 필드 | 의미 |
|---|---|
| `model` | LLM 모델 이름 (예: `claude-sonnet-4-6`) |
| `confidence` | 0–100 |
| `supporting_reasons` | AI가 BUY/SELL을 *지지*하는 reason list |
| `opposing_reasons` | AI가 *반대*하는 reason list (운영자가 양면 검토) |
| `risk_note` | AI가 명시한 위험 메모 |
| `is_order_intent` | 항상 `false` — AI는 직접 주문하지 않음 (#56 invariant) |
| `analysis_log_id` | `ai_analysis_log` row 참조 |

본 builder는 `target_kind="OrderAuditLog"` / `target_id=<audit row>`로 원본
주문과 연결한다.

## 9. API

### `GET /api/audit/events` (read-only)
필터: `event_type` / `severity` / `source` / `symbol` / `strategy` / `actor` /
`include_archived` (default false). 페이지네이션: `limit` (1-200, default 50)
+ `offset`. **응답에 Secret 0건** — INSERT 단계에서 fail-closed로 차단됐기
때문에 row에 Secret이 존재할 수 없음.

### `GET /api/audit/events/{id}` (read-only)
단건 상세. 없으면 404.

### `POST /api/audit/events` (제한)
**OPERATOR_NOTE만** 추가 가능 — event_type / severity / source가 고정되어
caller가 임의 이벤트를 만들지 못한다. Secret 패턴 발견 시 400 `secret_leak_
blocked`.

### `PATCH /api/audit/events/{id}/archive` (archive only)
`archived=True` set, 멱등. **row 삭제 0건**.

### DELETE endpoint
**없다**. OpenAPI 스키마 검증 테스트가 `/audit/events` path의 delete 메서드를
거부.

## 10. 기존 hook 연결 (#68 PR 시점)

| 엔드포인트 | hook | 이벤트 | severity |
|---|---|---|---|
| `POST /api/risk/emergency-stop` (ON) | `build_emergency_stop_event(enabled=True)` | EMERGENCY_STOP | CRITICAL |
| `POST /api/risk/emergency-stop` (OFF) | `build_emergency_stop_event(enabled=False)` | EMERGENCY_STOP | INFO |
| `POST /api/audit/events` | direct `log_audit_event` | OPERATOR_NOTE | INFO |

후속 PR에서 추가 hook 연결 (현재 builder는 준비됨):
- `route_order` → SIGNAL / ORDER_REQUEST / RISK_BLOCK
- `PermissionGate.approve / reject / cancel` → APPROVAL_DECISION
- `ai.assist.submit_candidate` → AI_PROPOSAL
- `VirtualOrder` 라이프사이클 → VIRTUAL_ORDER
- `FuturesRiskManager` → FUTURES_RISK
- `NotificationService.notify` 결과 → NOTIFICATION

본 hook 추가는 점진적 — 각 PR이 개별적으로 try/except 안전 정책을 검증.

## 11. UI

`AuditLog` 탭의 `events` sub-tab에 `AuditEventTimelineCard`가 마운트되어
기존 `EventTimelineView`(도메인별 머지 view)와 *공존*한다.

표시:
- 이벤트 행 — event_type / severity / source / symbol / summary / reason / actor / created_at
- severity 색상 — INFO 파랑, WARN amber, CRITICAL 빨강, SECURITY 보라
- source 색상 — AI 보라, STRATEGY 청록, OPERATOR 녹색, SYSTEM 회색
- archived 배지 + 별도 색상 처리
- 필터 — severity / source chip + `archived 포함` 체크박스
- archive 버튼 — 확인 모달 필수 (운영자명 / 사유 입력)

**금지 (테스트로 lock)**:
- 삭제 / 수정 / Delete / Edit / Remove 버튼 0개
- archive 직접 호출 (모달 없이) 0건
- broker / order / risk API 호출 0건

## 12. 후속 backlog

- 통합 `log_audit_event(input: AuditEventInput)` 시그니처
- 도메인 hook 점진 추가 (route_order / approve / cancel / AI / virtual /
  futures / notifications)
- Postgres trigger / view로 audit_event row UPDATE / DELETE 시 raise (DB 레벨
  invariant)
- archive batch tooling (운영자 cron으로 오래된 audit_event 자동 archive)
- audit_event row count + 통계 endpoint (운영자 dashboard용)
- 외부 SIEM 연동 (운영 환경 옵트인 후)

## 13. 절대 invariant (변경 금지)

1. `app.audit.events`는 *delete*하는 public 함수를 추가하지 않는다. `archive_
   event`만.
2. `/api/audit/events/*`에 DELETE 메서드가 추가되면 안 된다. OpenAPI 검증으로
   lock.
3. `log_audit_event`는 Secret 패턴 발견 시 *raise* — silent redaction 금지.
4. audit hook이 raise해도 호출자의 핵심 흐름(emergency stop 응답 등)을 깨지
   않는다 — 모든 caller는 try/except로 감싼다.
5. frontend `AuditEventTimelineCard`에 삭제 / 수정 버튼 0개.
6. `archived` 외 컬럼을 update하는 코드 경로 0건 — append-only.

## 14. 관련 PR / 체크리스트

- #34 RiskManager 표준 진입점 (RISK_BLOCK 이벤트 source)
- #38 OrderGuard
- #41 Manual Approval (APPROVAL_DECISION)
- #44 LIVE_AI_ASSIST (AI_PROPOSAL)
- #54 Risk Auditor (audit_level)
- #56 ExecutionRecommender (`is_order_intent=False`)
- #57 Daily Report (집계)
- #64 Notifications (NOTIFICATION 이벤트 후속)
- #68 Audit Log facade (본 PR)
