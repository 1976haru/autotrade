# Smartphone Operator Mode

운영자가 스마트폰에서 사용하는 핵심 흐름. 분석은 backend / Agent Council이 수행하고, 운영자는 *결과 확인 / 시작 / 일시정지 / 긴급중단 / 승인*만 수행한다.

## 운영자 핵심 동선

### 1. 상태 확인 (5초 점검)

스마트폰 첫 화면(Dashboard)에서 즉시 확인:
- **오늘 손익** (`compute_today_realized_pnl(tz=KST)` 결과 — 166)
- **오늘 주문 수** (`count_orders_today_kst` — 183)
- **승인/거절/대기** (`/api/audit/orders` decision 분해)
- **긴급정지 상태** (red banner — `risk.emergency_stop`)
- **현재 시장 위험도** (Agent Council `RiskOfficerAgent` decision summary)
- **virtual mode 표시** (모드 라벨 — 실제 LIVE 여부 즉시 인지)
- **24h activity** (Activity24hCard)

### 2. 시작 / 재개

`POST /api/risk/emergency-stop` body=`{"enabled": false, "decided_by": "ops1", "reason_code": "manual_operator"}`. 또는 frontend `BackendPolicyCard`의 "해제" 버튼 (153 reason 모달 자동).

### 3. 일시정지

운영자가 의도적 중단 시:
- `BackendPolicyCard` "긴급 정지" 버튼 → 153 reason dropdown 모달 (manual_operator / data_stale / agent_warning / 등 선택).
- 즉시 모든 신규 주문 차단 (RiskManager step 1 hard-reject — 060/153).

### 4. 긴급중단

운영자 의도와 무관하게 자동 trigger도 가능 (운영자는 결과만 확인):
- **182 자동 trigger**: N건 연속 REJECTED 시 `auto_stop_consecutive_rejections` 임계 도달 → 자동 emergency_stop + reason 'repeated_order_failure'.
- **운영자 명시 trigger**: BackendPolicyCard 버튼.
- **AI만 정지**: `set_ai_disabled(True)` (178 kill-switch). emergency_stop과 별개로 strategy/manual은 그대로.

### 5. 승인 / 거절

`Approvals` 탭 (058 stale badge / 069 stuck banner 등으로 시간 가시성 확보):
- **PENDING 큐**: 167 TTL 활성 시 stale rows 자동 EXPIRED 전환.
- **결정 흐름**: approve / reject / cancel — DecisionDialog 모달에서 decided_by + note + reason_code 입력.
- **Re-eval at approve**: 070 + 146 가드. submit 후 시세 stale / daily PnL 한도 초과 / AI confidence 임계 변경 등 invariant 재검증. 차단 시 PENDING 유지 → 운영자 재시도 가능.

### 6. AI 판단 이유 확인

`Audit` 탭 / 또는 별도 `Agent` 탭:
- **chain_id** 기반 같은 의사결정 사슬의 10 agent 판단 한 번에 조회.
- **per-strategy stats** (`/api/strategies/scoreboard` — 137/144/147/165): backtest + live PnL + confidence histogram + per-strategy realized PnL.
- **AI agent stats** (`/api/ai/agent-stats` — 162/165): proposal 카운트 / approval rate / top rejection reasons / per-strategy 분포.

## UI invariant

| 요소 | 위치 | 의미 |
|---|---|---|
| 빨간 banner | Dashboard 최상단 | emergency_stop ON |
| ⚠ "AI disabled" badge | Dashboard | 178 kill-switch ON |
| "VIRTUAL" 라벨 | Settings 탭 | LIVE 비활성 (currently default) |
| stale ratio (111) | Approvals 탭 + Dashboard banner (116) | 처리 안 된 PENDING 비율 |
| stuck banner (069) | Dashboard | emergency_stop ON 30분+ 지속 |
| reason badge (153) | EmergencyStopHistoryRow | reason_code 분류 |

## 모바일 친화 design

기존 frontend는 BottomNav (051) + responsive Card 기반. 본 directive 시점까지 추가 개선:
- 핵심 정보가 첫 화면에 모이도록 Dashboard 우선순위 (053 status summary, 055 Activity24hCard, 062 stale-aware pin, 097 bot idle warning).
- 키보드 a11y는 데스크톱용 (063 / 095) — 모바일에서는 BottomNav 탭 + 큰 버튼.
- ChipFilterBar (084) 같은 칩 필터는 가로 스크롤 가능. 폰에서도 작동.

## 안전 invariant

1. **모든 운영자 결정은 audit row** — emergency_stop 토글은 `EmergencyStopEvent` (153 reason_code 포함). 승인 / 거절 / 취소는 `OrderAuditLog` + `PendingApproval` audit.
2. **virtual 모드 표시 명확** — Settings 탭에 `mode_capabilities` matrix 노출. 운영자가 LIVE 여부 즉시 인지.
3. **긴급중단 버튼 항상 명확** — Dashboard 첫 화면에 prominent display. 다른 정보보다 위.
4. **실 LIVE 활성화 절대 금지** — `docs/live_activation_blockers.md` 참조. 본 directive에서는 옵트인 영역.

## 운영 절차 — 일중 흐름

1. 09:00 KST 장 시작 직전 — Dashboard 점검, emergency_stop 해제 (전날 자동 trigger됐을 수 있음).
2. 09:00–15:30 — 자동 흐름. 운영자는 PENDING 큐 + stale banner 확인 + Approvals 결정.
3. 12:00–13:00 — 점심시간 stale 확인. 167 TTL > 0이면 자동 EXPIRED.
4. 15:30 KST 장 종료 — Dashboard에서 일일 PnL / win rate / Agent Council 평가 검토.
5. 15:30 이후 — 운영자가 분석 / scoreboard 검토. 다음 거래일 전 RiskPolicy 임계 조정 결정.

## 관련 문서

- [`docs/risk_guards_matrix.md`](risk_guards_matrix.md) — 27개 RiskPolicy 가드
- [`docs/agent_decision_schema.md`](agent_decision_schema.md) — Agent Council 출력 구조
- [`docs/ai_virtual_execution_report.md`](ai_virtual_execution_report.md) — VIRTUAL_AI_EXECUTION 흐름
- [`docs/live_activation_blockers.md`](live_activation_blockers.md) — LIVE 활성화 차단 매트릭스
