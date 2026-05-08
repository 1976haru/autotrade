# Manual Approval Policy (#41)

> 코드: [`backend/app/permission/gate.py`](../backend/app/permission/gate.py) (PermissionGate) + [`backend/app/api/routes_approvals.py`](../backend/app/api/routes_approvals.py)
> 테스트: [`backend/tests/test_manual_approval_hardening.py`](../backend/tests/test_manual_approval_hardening.py)
> Frontend: [`frontend/src/components/tabs/Approvals.jsx`](../frontend/src/components/tabs/Approvals.jsx)

## 1. 목적

> **처음 실거래는 사람이 승인해야 주문이 나간다.** AI/전략 신호가 승인 대기
> 큐에 표시되고, 운영자가 *각 건마다* 승인해야 broker로 진행된다.

## 2. 동작 흐름

```text
Strategy / AI / Manual
        │
        ▼
   route_order
        │
        ├─► OrderGuard.check        (#38)
        ├─► RiskManager.check_order (#34) → NEEDS_APPROVAL
        │
        ▼
   PermissionGate.submit
        │
        ▼
   PendingApproval (status=PENDING)   ◄── 운영자 결재 대기
        │
        ▼ (operator approve / reject / cancel)
   PermissionGate.approve / reject / cancel
        │
        ├─► (approve) re-evaluate via RiskManager
        │   - 가격 / 잔고 / 포지션 / daily PnL 다시 조회
        │   - AI invariant (158, 159) 다시 검사
        │   - 통과 시 OrderExecutor.execute → BrokerAdapter.place_order
        │   - 실패 시 status=PENDING 유지 + attempts에 사유 누적
        │
        └─► (reject/cancel) 즉시 종료, attempts 변경 없음
```

## 3. 승인 TTL 정책

`Settings.approval_ttl_seconds` (env `APPROVAL_TTL_SECONDS`):
- **기본값 0** = 만료 비활성 (운영자가 명시 활성화 시에만 동작).
- **권장값 600~1800** (10~30분) — 시세 stale 임계와 맞춤.

TTL 초과 시:
- `GET /api/approvals` 호출 시점에 `expire_stale_approvals(ttl_seconds)`가
  lazy 실행 → status=`EXPIRED`, decided_at=now, note=`"auto-expired after Xs TTL"`.
- pending 목록에서 즉시 제외, history?status=EXPIRED에서 surface.

`EXPIRED` vs `CANCELLED`:
- **EXPIRED** — 시간 초과로 자동 만료 (운영자 의사 표시 X).
- **CANCELLED** — 운영자가 명시 취소 (중립적 폐기, "신호 노후 정리" 등).
- **REJECTED** — 운영자가 명시 거부 (의사 표시 — 절대 진행 X).

`ApprovalOut`에 carry되는 TTL 필드:
- `expires_at`: created_at + ttl_seconds (UTC ISO).
- `seconds_until_expiry`: 남은 초 (0이면 만료).
- `is_expired`: bool.

`ttl_seconds=0`이면 모두 None / False — UI는 만료 정보를 표시하지 않는다.

## 4. 승인 시점 재검증

`PermissionGate.approve(approval_id, broker, risk, ...)`는 broker 호출 *전*에
RiskManager.evaluate_order를 다시 실행:
- 현재 가격 / 잔고 / 포지션 / daily PnL 재조회
- AI invariant (#158 confidence / #159 reasoning) 재검사
- 통과 시 `OrderExecutor.execute` → audit row 갱신 + broker 호출
- 실패 시 `ApprovalRiskCheckFailedError` raise → API는 409
  `{"error": "risk_check_failed_at_approve", "reasons": [...]}`,
  approval status는 PENDING 그대로, attempts에 `{at, decided_by, reasons}` 누적

`ApprovalOut`에 carry되는 재검증 정보:
- `attempts`: 누적 시도 배열.
- `attempt_count`: 시도 횟수.
- `last_attempt_at`: 마지막 시도 시각.
- `last_attempt_reasons`: 마지막 실패 사유 list.

운영자는 "이 결재는 막혔던 적이 있다 → 다시 시도하면 또 막힌다" 컨텍스트를
즉시 본다.

## 5. 승인 타입 (request_source)

`OrderAuditLog`의 `requested_by_ai` / `strategy` / `source`(#40) / `trade_reason`
을 합산해 분류:

| `request_source` | UI 라벨 | 조건 |
|---|---|---|
| `AI` | AI 제안 | audit.source=AI 또는 requested_by_ai=True |
| `STRATEGY` | 전략 신호 | audit.source=STRATEGY 또는 strategy 필드 set |
| `MANUAL` | 수동 주문 | strategy/AI 모두 없음 |
| `LIQUIDATION` | 청산 후보 | trade_reason에 "liquidation" 또는 "stop" 포함 |
| `RISK_OVERRIDE` | 리스크 예외 요청 | audit.source=OPERATOR_OVERRIDE |
| `UNKNOWN` | 알 수 없음 | 위 어떤 단서도 없음 (legacy row) |

`ApprovalOut.request_source` (코드) + `request_source_label` (한글) 두 필드
모두 carry. Frontend는 `<RequestSourceBadge>` 컴포넌트로 색상별 배지 표시.

## 6. AI 역할

- **초기 단계 AI는 매수·매도 *제안*만 수행.** AI가 만든 주문은
  `RiskManager.evaluate_order` 결과가 NEEDS_APPROVAL로 분류되어 PendingApproval
  큐로 들어간다.
- 운영자가 명시 승인하지 않으면 broker로 가지 않는다 — `ENABLE_AI_EXECUTION=
  false` (default) + `MODE != LIVE_AI_EXECUTION` 동안.
- `LIVE_AI_EXECUTION` 활성화는 별도 옵트인 PR (CLAUDE.md 절대 원칙 3, #39
  AI Permission Gate 정책).

## 7. 실전 전 기준

LIVE 활성화 (`ENABLE_LIVE_TRADING=true`) 전 충족:

- [ ] **최소 수동 승인 기간**: 30거래일 이상 LIVE_MANUAL_APPROVAL 모드 운영.
      모든 주문이 이 큐를 거쳐 운영자가 시각/근거를 검증.
- [ ] **audit 누락 0건**: PendingApproval 모든 행이 OrderAuditLog row와 1:1
      매칭. 누락 / orphan 없음.
- [ ] **승인 실패 reason 분석**: attempts 배열의 patterns 분석 — 가장 자주
      실패하는 사유가 운영 정책과 일치.
- [ ] **TTL 운영 확정**: approval_ttl_seconds 값 결정 + 만료된 결재 처리
      운영 정책 문서화.
- [ ] **request_source 분포**: AI/STRATEGY/MANUAL 비율이 운영자 의도와 일치.
- [ ] **Frontend UI 검증**: stale / expired / failure attempts 표시가 운영자
      직관과 맞음.

## 8. 향후 과제 (Manual Approval backlog)

- **승인 알림** (Slack / SMS) — 새 결재 도착 시 운영자에게 push.
- **승인 권한 RBAC** — 결재자 역할 분리 (junior / senior / supervisor).
- **TTL을 운영모드별 다르게** — LIVE_MANUAL_APPROVAL은 짧게, LIVE_SHADOW는 길게.
- **bulk approve / reject** — UI에서 같은 종목/사유 다건 동시 처리.
- **결재 히스토리 search** — symbol / decided_by / 사유로 검색.
- **승인 후 결과 자동 알림** — 체결 결과 성공/실패를 결재자에게 회신.

## 9. 안전 invariant

- 운영자 명시 승인 없이는 broker로 가지 않는다 — PermissionGate.approve가
  유일한 진입점.
- approve가 RiskManager 재검증을 통과하지 못하면 status=PENDING 유지 (broker
  호출 0건, audit row 갱신 0건).
- `EXPIRED` 결재는 broker로 가지 않는다 — list_pending에서 제외 + 별도 status.
- `ApprovalOut`의 신규 필드는 모두 *additive* — 기존 클라이언트가 무시해도
  동작. 기존 200+ 결재 테스트 무수정 통과.
- LIVE flag / API Key / Secret / 계좌번호 변경 0건.
