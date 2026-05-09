# UI Operator Final Pass

본 문서는 *Agent Trader v1 UI*를 운영자 친화적인 관제앱 수준으로 끌어올리는 후속 라운드 작업을 정리한다. **자동매매 로직 / backend 주문 / 브로커 / RiskManager / PermissionGate / OrderExecutor는 변경하지 않으며**, frontend UI / 레이아웃 / 데이터 출처 표시 / 문서만 다룬다.

## 1. 본 PR의 변경사항

### 1.1 모바일 BottomNav 5-슬롯 (4 primary + 더보기)

`frontend/src/components/layout/BottomNav.jsx`:

- `_ALL_TABS`에 `mobileTier: "primary" | "secondary"` 필드 추가.
- **primary (4)**: `dash`(홈) / `signal`(에이전트) / `approve`(승인) / `strat`(리스크).
- **secondary (6+)**: `bot`(자동봇) / `chart`(차트) / `backtest`(백테스트) / `audit`(로그) / `engine`(엔진) / `config`(설정). futures는 `mobileExclude=true`로 *더보기에서도 제외* (Pages demo / 안전 운영 위해 모바일에서 직접 접근 불가).
- 모바일 BottomNav는 *항상 5 슬롯*: primary 4개 + "더보기" 토글.
- "더보기" 클릭 시 secondary 탭이 grid sheet로 슬라이드 업. backdrop 클릭 또는 secondary 탭 선택 시 닫힘.
- "더보기" 토글에 secondary 탭의 badge 합계 표시 — 운영자가 "더보기 안에 처리할 일이 있다"는 것을 모바일에서도 한눈에 인지.

신규 export:
```js
getMobilePrimaryTabs()    // 4 tabs, always primary
getMobileSecondaryTabs()  // secondary tabs (futures 제외)
```

기존 API (`getNavTabs` / `getMobileNavTabs` / `isTabVisible` / `TABS` proxy) backwards compat 유지.

PC TopNav (`getNavTabs`)는 영향 없음 — 모든 탭을 그대로 노출.

### 1.2 PC 3-열 관제 대시보드 helper CSS

`frontend/src/index.css`:

- `.dashboard-3col` wrapper class — `grid-template-columns: minmax(280px, 1fr) minmax(360px, 1.4fr) minmax(280px, 1fr)`.
- `.dashboard-col-left` / `.dashboard-col-center` / `.dashboard-col-right` — 카드를 column별로 배치하는 wrapper.
- 1100px 이하: 2열 + right column이 아래로 stack.
- 모바일 (≤767px): `display: contents`로 children을 부모 `.dashboard-3col` flex column에 직접 노출 — 1열 stack.

본 PR은 *helper class만 추가*하고 Dashboard.jsx의 카드 배치 변경은 *후속 PR*로 미룬다 — 카드 reordering은 운영자 검토가 필요한 변화이므로 (어떤 카드를 어디 배치할지 의견 정렬 필요).

### 1.3 테스트 (신규 20건)

`frontend/src/components/layout/BottomNav.tier.test.jsx`:

- `getMobilePrimaryTabs` / `getMobileSecondaryTabs` 명세 (4건)
- BottomNav 5-슬롯 layout (3건)
- 더보기 menu open / close / 항목 클릭 / backdrop / futures 제외 (5건)
- 더보기 토글 active 강조 + secondary badge 합산 (3건)
- PC TopNav backwards compat (3건)

기존 BottomNav.test.jsx (20건) + futures-flag.test.jsx (8건) 모두 통과 (backwards compat 보장).

## 2. 의도적으로 *deferred*된 항목 (backlog)

본 PR은 안전한 단일 round로 완료 가능한 변경만 다루며, 다음 항목은 별도 PR로 분리:

### 2.1 Legacy 탭 토큰화

대상 9개 탭: Approvals / AuditLog / BotControl / Backtest / StrategyRisk / VirtualOrders / VirtualPositions / Futures / Settings.

- **이유 (deferred)**: 9개 파일 / 수백 줄의 인라인 스타일 / 다크 카드를 전수 토큰화하면 단일 PR이 불안정해지고 시각 회귀를 일으킬 위험. 작은 단위 (탭 1-2개씩)로 나눠 진행 권고.
- **체크리스트** (PR 분할 가이드):
  - PR-A: Approvals / AuditLog (가장 자주 사용 → 우선순위 상)
  - PR-B: BotControl / StrategyRisk / Settings
  - PR-C: Backtest / VirtualOrders / VirtualPositions
  - PR-D: Futures (default 비활성이지만 노출 시 검토)
- **공통 패턴**:
  - 인라인 dark 색 → `var(--c-surface)` / `var(--c-text)` / `var(--c-border)` 토큰
  - 작은 글자 → 본문 14-16px, 카드 제목 16-20px, 핵심 숫자 24px
  - red/coral (위험) / amber (주의) / green/mint (정상) 표준 팔레트
  - `Failed to fetch` 원문 노출 0건 — `friendlyErrorMessage(error)`로 변환 후 `<ErrorState hint />`

### 2.2 PC 3-열 카드 배치

본 PR은 helper CSS만 추가. 실제 Dashboard.jsx의 카드 reordering / wrapping은 후속 PR로 미룸 — 운영자가 어떤 카드를 어느 column에 배치할지 의견 정렬 필요.

권장 구조 (참고):
- **left**: 운영 제어 (`OperatorPanel` / `EmergencyStopStuckBanner` / 결재 대기)
- **center**: AI 결정 + KPI (`AgentDecisionHero` / `AgentStrategyChoiceCard` / `HeroSummaryCard` / `Activity24hCard`)
- **right**: 리스크 / 상태 (`MarketRegimeBadge` / `RiskAuditorCard` / `ShadowSummaryCard` / freshness)

### 2.3 모바일 Operator Mode 풍부화

현재 `OperatorPanel.jsx`가 모바일 핵심 기능(시작 / 일시정지 / 긴급중단 + 핵심 상태)을 제공하나, 다음 항목 후속 PR:

- 푸시 알림 (PWA 변환 필요 — service worker)
- haptic feedback (mobile API)
- 1열 스택 강제 (현재는 `.dashboard-pc-only`로 일부 카드 숨김 — 좀 더 명시적인 mobile-first 레이아웃)
- 3초 안에 상태 파악 가능한지 사용자 테스트 + iteration

### 2.4 Cross-tab Error / Empty / Loading 일관성 audit

`components/common/primitives.jsx`의 `<EmptyState>` / `<ErrorState>` / `<LoadingState>`는 이미 존재. `friendlyErrorMessage`도 통합되어 있음 (#59). 다만 *모든* 카드가 이 패턴을 동일하게 사용하는지 audit 필요:

- audit 목록: 90+ 컴포넌트 — 일괄 검사 필요
- pattern: hook의 `{loading, error, data}` → `<LoadingState>` / `<ErrorState hint={friendlyErrorMessage(error)} />` / `<EmptyState>` / data render 4분기
- 후속 PR로 검사 + 누락된 곳 wrap

### 2.5 Agent 판단 history 시각화

현재 `AgentDecisionHero` / `AgentLatestTile` / `AgentDecisionSummaryCard`가 최신 / 통계만 표시. 시계열 timeline (`chain_id` 기반 trace + 시각적 ribbon)은 미구현. AgentCouncilCard에 timeline 추가하는 별도 PR 권고.

## 3. 변경 파일

| 파일 | 종류 | 변경 |
|---|---|---|
| `frontend/src/components/layout/BottomNav.jsx` | 수정 | 5-슬롯 + 더보기 menu + tier filter |
| `frontend/src/components/layout/BottomNav.tier.test.jsx` | 신규 | 20 tests |
| `frontend/src/index.css` | 수정 | `.dashboard-3col` + col helper class |
| `docs/ui_operator_final_pass.md` | 신규 | 본 문서 |

## 4. 안전 invariant

| 원칙 | 검증 |
|---|---|
| 자동매매 로직 / backend / broker / RiskManager / PermissionGate / OrderExecutor 변경 0건 | git diff backend/ → 0줄 |
| 실 broker live order 호출 0건 | UI / CSS만 변경 |
| `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 변경 0건 | env 파일 미변경 |
| API key / Secret / 계좌번호 변경 0건 | frontend / .env / 백엔드 미변경 |
| API contract 변경 0건 | client.js 미변경 |
| 기존 테스트 회귀 0건 | 1306+ frontend tests pass |

## 5. 다음 작업 권고 순서

1. **(중)** `Approvals.jsx` / `AuditLog.jsx` 토큰화 — 가장 자주 사용되는 탭
2. **(중)** Dashboard.jsx에 `.dashboard-3col` wrap + 실제 카드 배치 (운영자 의견 받은 후)
3. **(저)** `BotControl` / `StrategyRisk` / `Settings` 토큰화
4. **(저)** Cross-tab error/empty/loading audit
5. **(저)** Agent decision history timeline
6. **(저)** Mobile PWA 변환 + push notification

## 관련 문서

- [`frontend_integration.md`](frontend_integration.md) — #59 API client + DataSourceBanner
- [`smartphone_operator_mode.md`](smartphone_operator_mode.md) — Mobile Operator 정책
- [`agent_design.md`](agent_design.md) — Agent 분리 정책 (UI는 advisory만 surface)
- `frontend/src/components/layout/BottomNav.jsx` — 5-슬롯 mobile nav
- `frontend/src/index.css` — `.dashboard-3col` helper
- `CLAUDE.md` — 절대 원칙 5번 (frontend는 관제 / 승인 / 설정 UI, 실 broker / AI 호출은 backend에서만)
