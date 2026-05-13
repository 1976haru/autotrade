# Approval UI 정책 (체크리스트 #61)

## 1. 목적

AI 제안 / 전략 신호가 RiskManager에서 `NEEDS_APPROVAL`로 분류된 주문 후보를
*사람이 검토 → 승인 / 거절 / 취소*하는 결재 흐름의 핵심 UI다.

**본 UI는 LIVE_AI_EXECUTION의 대체재가 아니다** — AI / 전략이 자동으로
broker로 가지 않도록 하는 *안전 게이트*다. 운영자가 화면에서 `✓ 승인`을
누르기 전까지 broker에는 어떤 주문도 가지 않는다.

체크리스트 #44 (`LIVE_AI_ASSIST`)에서 AI 제안이 본 큐로 유입되며,
`LIVE_MANUAL_APPROVAL` 모드의 사람-주도 주문도 같은 큐를 거친다.

## 2. 화면 구성

### 2.1 PENDING 큐 (`Approvals` 탭 상단)

각 결재 행은 *위에서 아래로* 다음 순서로 정보를 노출한다:

1. **종목 / side / 수량 / 주문 타입** — 1행 헤더 (#41 패턴 유지)
2. **운용 모드 / 생성 시각 / RequestSourceBadge / FreshnessBadge** —
   메타 행. AI/STRATEGY/MANUAL/LIQUIDATION/RISK_OVERRIDE 구분.
3. **`ApprovalProposalSummary`** — 제안 *근거*:
   - 출처 배지 (🤖 AI 제안 / 📊 전략 신호 / ✋ 수동)
   - 전략 이름 / signal_confidence / signal_strength
   - `ai_decision_meta.supporting_reasons` / `opposing_reasons` / `risk_note`
   - `expected_reward` / `expected_risk` / `risk_reward_ratio` (있을 때만)
   - **"주문은 아직 실행되지 않았습니다"** 안내 — proposal이지 order가
     아님을 명시.
4. **`ApprovalRiskSummary`** — RiskManager 사유 카테고리화:
   - `freshness` (stale price / market closed)
   - `position` (max_positions / max_symbol_exposure 등)
   - `loss` (max_daily_loss / weekly / consecutive)
   - `ai` (AI permission / rate limit / reasoning)
   - `guard` (duplicate / cooldown / pending same side)
   - `other` (매칭 안 된 사유)
   - **데이터 없으면**: "표시 가능한 리스크 사유 없음." (위험 없음으로
     단언하지 *않는다* — 승인 시점에 재검증된다는 안내 동반)
5. **`ApproveAttemptFailureBadge`** — 재시도 거부 이력 요약 (#076)
6. **`_AttemptsList`** (확장 시) — 시도별 timestamp / 운영자 / 사유
7. **액션 버튼 행** — ✓ 승인 / ✗ 거부 / ⊘ 취소 (모바일에서는 동등 너비)

### 2.2 승인 확인 모달 (`ApprovalDecisionModal`)

`action === "approve"`일 때만 `ApproveConfirmSummary`가 `_OrderSummary` *위에*
추가로 표시된다 — 2차 확인 흐름:

| 항목 | 내용 |
|---|---|
| 모달 제목 | "정말 승인하시겠습니까?" |
| 상단 요약 | 종목 / side / 수량 / 가격 / strategy / mode / confidence |
| Stale 경고 | `isPendingStale(created_at)` true일 때만 — "이 신호는 오래되었습니다. 승인 전 현재 시장 상황을 다시 확인하세요." |
| 리스크 사유 | 최대 3건 + 외 N건 |
| 설명 | "승인 후에도 백엔드가 가격/잔고/리스크를 다시 검증합니다." |
| 입력 | 운영자명 / 사유 (선택, 감사 추적용) |
| 확인 버튼 | "✓ 승인" |

`reject` / `cancel`은 폐기 동작이라 stale 경고를 표시하지 *않는다* (신호
노후 자체가 정당한 폐기 사유이기 때문에 경고가 잡음이 된다).

### 2.3 만료 / 노후 표시 (`ApprovalFreshnessBadge`)

backend가 `expires_at` + `seconds_until_expiry`를 보내면(`settings.
approval_ttl_seconds > 0`) 그것을 우선 사용 — "⏰ N분 후 만료" /
"⏰ 만료됨".

backend가 TTL을 보내지 않으면 `created_at` 기반 age로 fallback — "생성 후
2분 전". `PENDING_STALE_THRESHOLD_MS`(10분) 이상이면 "⚠ 신호 노후 · " 접두.

색상:
- `fresh` — 파랑/회색
- `nearing` — amber (1분 안에 만료)
- `expired` / `stale` — 빨강

기존 `ApprovalExpiryBadge`(TTL 전용)는 하위 호환을 위해 보존되며,
`ApprovalFreshnessBadge`가 TTL + age stale 두 경로를 통합 표시한다.

### 2.4 처리 내역 (history)

`HistoryRow`는 영문 status 옆에 한국어 라벨을 함께 표시한다:

| status | 한국어 라벨 | 의미 |
|---|---|---|
| APPROVED | 승인 | broker로 진행됨 (또는 진행 직전) |
| REJECTED | 거부 | 운영자가 "이 주문은 안 된다" 능동 판단 |
| CANCELLED | 운영자 취소 | "신호 노후" 등 중립적 폐기 |
| EXPIRED | 시간 만료 | TTL 만료로 시스템이 자동 EXPIRED 처리 |

**EXPIRED와 CANCELLED는 둘 다 broker로 진행되지 않은 행**이지만 분석 의미
가 다르다 — EXPIRED는 *결재 적체 / TTL 설정 문제*를 시사하고, CANCELLED는
운영자의 의도된 폐기. 사후 분석 시 두 사유를 절대 합치지 *않는다*.

## 3. 안전 원칙

| 원칙 | 코드 단 강제 |
|---|---|
| 승인 전 실제 broker 주문 없음 | `route_order`가 `NEEDS_APPROVAL`이면 broker.place_order 호출 없이 PermissionGate.submit으로 끝 (#34 / #40) |
| 승인 후에도 백엔드 재검증 | `PermissionGate.approve`가 broker 호출 *전*에 RiskManager 재검증 (#070) — 실패 시 status=PENDING 유지 + attempts에 사유 누적 |
| 오래된 신호 주의 | UI: ApprovalFreshnessBadge + 모달 stale 경고. 백엔드: settings.approval_ttl_seconds > 0이면 TTL 만료된 행 자동 EXPIRED |
| AI 제안은 주문이 아님 | `ApprovalProposalSummary`에 "주문은 아직 실행되지 않았습니다" 명시. `ai_decision_meta.is_order_intent=False` 불변 (#56) |
| 본 UI는 broker API 호출 0건 | ApprovalQueue 모듈은 `broker.place_order` / `cancel_order` 호출 0건. 모든 액션은 기존 `/api/approvals/{id}/approve|reject|cancel`만 사용 |
| AI Key / Secret 변경 0건 | 결재 흐름은 운영자명 / 사유 입력만 받는다 |
| LIVE flag 토글 0건 | 결재 UI에는 emergency_stop / enable_live_trading 토글 노출 0건 |

## 4. 모바일 정책

스마트폰 운용 동선:

1. **핵심 정보 우선** — 종목 / side / 수량 / 출처 배지 / 만료 배지가 상단.
2. **근거 / 리스크는 stack 레이아웃** — 가로 table 없이 한 행에 1 단위 표시.
3. **액션 버튼 동등 너비 큰 터치영역** — `ApprovalActionBar`가 `Btn full`.
4. **history는 기본 펼침** — 모바일에서 모드/시간/사유 chip은 PC와 동일.
5. **모달 max-width 90vw** — 가로 좁은 화면에서도 풀스크린 가까이.

## 5. EXPIRED vs CANCELLED 차이

| 항목 | EXPIRED | CANCELLED |
|---|---|---|
| 발생 주체 | 시스템 (TTL trigger) | 운영자 (의도적 액션) |
| `decided_by` | NULL 또는 시스템 | 운영자명 (모달 입력) |
| `note` | "TTL expired" 등 | 운영자 입력 (예: "신호 노후") |
| 색상 | amber (#facc15) | 회색 (#94a3b8) |
| 한국어 라벨 | "시간 만료" | "운영자 취소" |
| 분석 의미 | 결재 적체 의심 | 정상 운영 (중립 폐기) |

본 두 status를 *합치면 결재 적체를 운영자 의도로 오해*할 수 있으므로, UI
필터 칩 / 카운트 / banner는 두 종류를 항상 분리해 표시한다.

## 6. 승인 / 거절 / 취소 감사 로그

모든 액션은 backend `OrderAuditLog` + `PendingApproval.attempts`에 영구화:

- **승인**: `decided_by` + `note` → `OrderAuditLog.message`, broker 호출 후
  `OrderAuditLog.executed=True`. 실패 시 attempts에 entry 추가, status는
  PENDING 유지.
- **거절**: `OrderAuditLog`에는 새 row가 추가되지 않음 (이미 NEEDS_APPROVAL
  row가 있음). `PendingApproval.status=REJECTED` + decided_by/note.
- **취소**: 거절과 동일하되 `status=CANCELLED`.
- **만료**: `PermissionGate.list_pending` 호출 시 lazy expire — TTL 초과면
  status=EXPIRED + decided_by="(TTL)" 같은 sentinel.

운영자가 사후 분석 시 처리 내역 화면에서:
- 시간 필터 (`1h / 24h / 7d / 전 기간`)
- 상태 필터 (`승인 / 거부 / 취소 / 만료`)
- 종목 필터 (text search)
- 모드 필터 (`수동 / AI 보조`)

네 축을 자유롭게 조합해 사례를 추적할 수 있다 (#082-#092).

## 7. 컴포넌트 트리

```
Approvals (tab)
├── PageHeader
├── Card: 승인 대기 큐
│   ├── [Bulk cancel stale] (조건부)
│   ├── ErrorState (friendly message)
│   ├── ApprovalQueueEmptyState (loading / empty)
│   └── PENDING rows (focus / keyboard nav)
│       ├── 헤더 (symbol / side / qty / order_type / age badge)
│       ├── 메타 (mode / created_at / RequestSourceBadge / ApprovalFreshnessBadge / ApprovalExpiryBadge)
│       ├── ApprovalProposalSummary  ← 체크리스트 #61
│       ├── ApprovalRiskSummary      ← 체크리스트 #61
│       ├── ReasonsLine
│       ├── ApproveAttemptFailureBadge
│       └── 액션 버튼 (✓ ✗ ⊘)
├── Card: 처리 내역
│   ├── 4축 필터 (status / symbol / time / mode)
│   ├── HistoryDecisionTimeSummary
│   ├── HistoryStaleRatio
│   └── HistoryRow[]
│       ├── 헤더 (symbol / side / qty / status + 한국어 라벨)  ← 체크리스트 #61
│       ├── 메타 (id / mode / decided_at / decided_by / note / attempts)
│       └── ReasonsLine
└── ApprovalDecisionModal (조건부)
    ├── ApproveConfirmSummary (approve 시만)  ← 체크리스트 #61
    │   ├── 종목 / side / qty / strategy / mode / confidence
    │   ├── Stale 경고 (조건부)
    │   └── 리스크 사유 top 3
    ├── _OrderSummary
    └── DecisionDialog (운영자명 / 사유 / 확인 / 취소)
```

## 8. 절대 invariant (변경 금지)

1. ApprovalQueue 컴포넌트들은 `broker.place_order` / `cancel_order`를 직접
   호출하지 *않는다*. 모든 broker 호출은 `route_order` → `OrderExecutor` 경유.
2. ApprovalQueue 컴포넌트들은 `/api/approvals/{id}/approve|reject|cancel`만
   호출한다 (props로 받은 handler 통해서) — 새 API contract 0건.
3. `ApprovalProposalSummary`의 "주문은 아직 실행되지 않았습니다" 문구는
   항상 노출 — 자동 매매 환상 방지.
4. `ApprovalRiskSummary`는 reasons가 비어도 "위험 없음" 같은 단언 금지 —
   "표시 가능한 리스크 사유 없음 + 재검증 안내" 패턴.
5. EXPIRED와 CANCELLED는 어떤 chip / count / banner에서도 합치지 *않는다*.
6. `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING`
   토글은 결재 UI에 노출하지 *않는다*.
7. `friendlyErrorMessage`는 raw `Failed to fetch`를 그대로 노출하지 *않는다* —
   Demo Mode / 로컬 안내로 변환.

## 9. 관련 PR / 체크리스트

- #34 RiskManager 표준 진입점 (NEEDS_APPROVAL 분기)
- #41 ApprovalOut TTL + RequestSourceBadge
- #44 LIVE_AI_ASSIST AI 제안 → 큐 등록
- #56 ExecutionRecommender — proposal 객체
- #61 Approval UI 핵심 구조화 (본 PR)
- #070 PermissionGate 승인 시점 재검증
- #076 attempts 영구화
- #082-#092 처리 내역 4축 필터
- #095 IME 가드
- #100 adaptive polling
- #103 / #107 키보드 nav
- #121 / #127 attempts expandable
