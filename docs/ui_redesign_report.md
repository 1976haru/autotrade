# UI Premium Light Redesign — Completion Report

## 배경

기존 UI 피드백:
- 배경이 너무 어둡고 글자가 잘 안 보임
- 글자 크기가 작고 대비가 약함
- 카드 위계가 약함
- 데스크톱에서도 모바일 화면처럼 답답함
- "Failed to fetch" 같은 원시 에러 문구가 그대로 보임
- 기능은 많지만 첫인상이 개발자용 콘솔처럼 보임

목표: **Soft Fintech + Smart Assistant + Lifestyle App** 톤. 밝은 라이트 테마 +
파스텔 포인트 + 둥근 카드 + 큰 글자 + 명확한 위계 + Agent 강조.

## 7-Phase 결과 (Light-001 ~ Light-007)

### Light-001 — Design system + responsive shell

`feat(ui): introduce light design system and responsive shell`

- `index.css` `:root` 토큰을 light palette로 재정의:
  - `--c-bg`: `#f6f8fc` (slate-50 + 청회색 hint)
  - `--c-surface`: `#ffffff` (pure white cards)
  - `--c-text`: `#0f172a` (slate-900 — 흰 위에 진한 글씨)
  - 상태색: emerald-500 / amber-500 / red-500 / blue-500 / violet-500
- body·#root에 light 배경 + Inter / Apple SD Gothic Neo 폰트 적용.
- App shell의 인라인 dark `#010a14`를 토큰 reference로 교체.
- TopNav: white sticky bar + 활성 탭 `#eff6ff` blue-200 border.
- Demo Mode banner: indigo light gradient.
- EmptyState/ErrorState/LoadingState 토큰 기반 light 카드.

### Light-002 — Dashboard premium redesign

`feat(ui): redesign dashboard for premium operator view`

- **HeroSummaryCard subtitle**: "지능형 에이전트가 시장을 분석하고, 나는 핵심만
  확인하는 자동매매 대시보드"로 사용자 친화 카피.
- **OperatorPanel** light migration: white surface + accent border (emergency
  시 적색). VIRTUAL MODE 배지 violet-50/violet-500. 3 buttons: idle 시 light
  fill + 색 텍스트, active 시 강조 색 fill. Status 6-cell grid 큰 글자 + 토큰.
- Error 카피: "일부 데이터를 불러올 수 없습니다 (데모 모드일 수 있어요)" —
  raw "Failed to fetch" 노출 X.

### Light-003 — Friendly error/empty/loading states

`fix(ui): normalize empty error and loading states`

- AgentCouncilCard / AgentDecisionSummaryCard / AgentStatsCard: raw error 표시
  를 `friendlyErrorMessage` + light red surface(`#fef2f2` + `#fecaca`)로 교체.
- BackendOfflineBanner offline 분기: "백엔드 연결 대기 중입니다 / 실데이터를
  보려면 backend와 frontend를 함께 실행하세요" 친절 카피, raw error 제거.
- 모든 폴백 카피에 친근한 어조 적용("…를 불러올 수 없어요").

### Light-004 — Navigation refresh

`feat(ui): improve navigation for desktop and mobile`

- BottomNav light migration: white surface + 위쪽 부드러운 그림자, 활성 탭
  `var(--c-info)` blue, 비활성 `var(--c-text-3)`. 라벨 11px + 활성 시 fw-700.
- TopNav는 Light-001에서 이미 light 전환.

### Light-005 — Agent emphasis

`feat(ui): highlight intelligent agent decision summaries`

- **AgentDecisionHero confidence 시각화**: 큰 monospace symbol(28px) + confidence
  progress bar (linear-gradient indigo→violet, 8px height, 둥근 끝).
- Reasons chip 스타일 + accent dot.
- OperatingLoopCard 인라인 dark hex → 토큰 전부 교체. sub-row helpers.
- MarketRegimeBadge: white surface + box-shadow + 큰 글자.

### Light-006 — Core primitives light

`feat(ui): polish core operator tabs`

- `components/common/index.jsx`의 Card / SectionLabel / Btn / Inp / ScoreBar /
  StatBox / Toggle / Slider 8개 primitive를 light token 기반으로 일괄 교체.
- 한 번의 변경으로 모든 탭의 카드 컨테이너 + 버튼 + 입력 + 통계가 동시에 light.
- StatBox: `--fs-2xl` 큰 숫자 + uppercase 라벨로 KPI 가독성 ↑.
- Btn: primary blue (#3b82f6) + 흰 텍스트 + 더 큰 hit area.
- Toggle: 더 큰 토글 + 그림자 — 모바일 탭하기 편함.

### Light-007 — Pages demo final + report

`docs(ui): finalize responsive light dashboard redesign`

본 문서. README는 Light-001 시점에 이미 확인 주소(Local UI / API Docs / Pages
Demo) + 모바일 명령 정리됨.

## 변경 통계

| 항목 | Before | After |
|---|---|---|
| Frontend tests | 1008 | **1008** (regression-free) |
| Lint errors | 0 | 0 |
| Build | OK | OK |
| Theme | Dark (slate-950) | **Light** (slate-50 + white cards) |
| Body bg | `#010a14` | `#f6f8fc` |
| Card bg | `#020e1c` | `#ffffff` |
| Primary text | `#c9d6e3` | `#0f172a` |
| Primitive count | 8 (dark) | **8 (light)** |
| Raw "Failed to fetch" | 다수 surface | **0 사용자-facing surface** |
| Agent confidence | 숫자만 | **progress bar + 큰 symbol** |

## 개선 전 vs 개선 후

| 영역 | 개선 전 | 개선 후 |
|---|---|---|
| 첫인상 | 개발자 콘솔 풍 어두운 배경 | Soft fintech 라이트 톤 |
| 가독성 | 9-11px 글자 + 약한 대비 | 13-16px + slate-900 ↔ white |
| 카드 위계 | 인라인 hex 색 매번 다름 | 토큰 + box-shadow + 둥근 radius |
| 버튼 | 작은 hit area | 큰 padding + 명확한 primary blue |
| Agent | 카드 깊숙이 | progress bar + 큰 symbol + chip |
| Demo Mode | 빨간 에러 분위기 | 친근한 인디고 light banner |
| Error UX | "Failed to fetch" raw | 친절한 한국어 안내 + light red |
| Navigation | 한 줄 작은 라벨 | desktop sticky top + 모바일 light bottom |
| Mobile | 그대로 | 그대로 + 더 큰 글자/탭 영역 |

## 확인 주소

- **Local UI**: <http://localhost:5173>
- **Local API Docs**: <http://127.0.0.1:8000/docs>
- **GitHub Pages Demo**: <https://1976haru.github.io/autotrade/>

스마트폰에서 PC dev 서버를 보려면:

```bash
cd frontend
npm run dev -- --host 0.0.0.0
# 접속: http://<PC_IP>:5173
```

## 남은 UI Backlog (다음 단계)

이번 시리즈에서 다루지 않은 후속 후보:

1. **Tab 내부 카드 inline dark 정리** — 일부 row(예: Approvals row, AuditLog
   timeline row)는 여전히 인라인 hex `#0c2035` 등을 사용. 토큰으로 마이그레이션.
2. **Table 데스크톱 레이아웃** — Audit timeline / Approvals history는 모바일은
   row stack OK, 데스크톱에서는 진짜 table grid가 가독성 ↑.
3. **다크 테마 토글** — 시스템 prefers-color-scheme 또는 운영자 setting.
4. **focus ring 표준화** — 키보드 접근성 일관성 (현재 inline outline 없음).
5. **애니메이션 micro-interaction** — 카드 hover, 토글 transition, 차트 진입 등.
6. **차트 시각화** — 24h activity / KPI / agent confidence를 sparkline으로.

## 안전성 (절대 원칙 준수)

- 실 broker live order endpoint 호출: **0건**
- LIVE_AI_EXECUTION 활성화: **0건**
- FUTURES_LIVE 활성화: **0건**
- API Key / App Secret / 계좌번호 추가/수정/커밋: **0건**
- RiskManager / PermissionGate / OrderExecutor 우회: **0건**
- backend API contract 변경: **0건**

본 시리즈는 **frontend UI만** 개선. 자동매매 비즈니스 로직 / 주문 흐름 /
리스크 가드는 어떤 코드도 수정되지 않음.

## 테스트 결과

- `npm test` (frontend): **1008 passed** (regression 0)
- `npm run lint` (frontend): **0 errors / 66 warnings** (기존 baseline)
- `npm run build` (frontend): **OK** (~417 KB JS / ~7 KB CSS)
- backend pytest: 영향 없음 — 이전 head 996 unit + 10 stress 그대로.

## 종료

7개 phase 모두 완료. 각 phase는 독립 feature 브랜치 → test/lint/build →
commit → main merge → 브랜치 삭제까지 진행. main ↔ origin/main 동기화,
working tree clean. 신규 후보 제안 없이 종료.

---

# 후속: Agent-Centered Operator Experience (UI Redesign 추가 라운드)

> 사용자 추가 피드백: "Dashboard가 에이전트 중심이 아니라 일반 관리자 화면",
> "PC와 모바일 UI가 같아 모바일에서 핵심만 보기 어려움", "AI가 어떤 전략을
> 골랐는지 흐름이 약함" — 본 라운드는 이 3가지 pain point에 집중.

## 본 라운드 변경 (이번 세션)

### 1. Agent Strategy Choice Card (PHASE 5 — 신규)
- `frontend/src/components/common/AgentStrategyChoiceCard.jsx` (신규).
- 4 전략(Volume Breakout / Pullback Rebreak / VWAP Reclaim / ORB+VWAP)을
  chip으로 동등하게 나열, 현재 활성 전략을 강조 + 선택 이유/적합 시장 표시.
- `backendApi.engineStatus()` + `engineRegistry()` 기반 — read-only.
- 운영자 토글 / 활성화 / 주문 버튼 부재 (테스트 가드 — `agent-strategy-choice-card`
  내 `activate` / `execute` / `place` testid 부재 검증).
- Dashboard에서 `AgentDecisionHero` 바로 아래에 mount → "AI 판단 → 어떤
  전략을 골랐나" 흐름이 자연스럽게 인접.

### 2. Mobile / PC 정보 분기 (PHASE 4)
- 신규 CSS 클래스 `.dashboard-pc-only` (`frontend/src/index.css`).
  - `<768px` (모바일): `display: none`.
  - `≥768px` (PC): `display: block`.
- 적용 (모바일 기본 숨김, PC 표시):
  - `Activity24hCard` (24시간 활동 요약)
  - `AgentLatestTile` (Agent chief 결정 요약 — AgentDecisionHero가 상위에서
    이미 핵심을 노출)
  - `WatchlistSummaryTile` (관심종목)
  - `ThemeSummaryTile` (테마 후보)
- 모바일 노출 카드 ("3초 안에 핵심 인지" 동선): HeroSummary → OperatorPanel
  → MarketRegime → AgentDecisionHero → AgentStrategyChoice → StatusSummary →
  KPI 3-grid → BotControl.

### 3. EmergencyStopStuckBanner 가독성 (PHASE 2/8)
- 글자 11→13/14px로 키움 (긴급정지 30분 이상 ON 알림).
- 보조 텍스트 색 회색(#94a3b8) → amber-800(#92400e)로 대비 향상.
- 중복 `fontSize` 속성 제거.

## 본 라운드 미실행 PHASE (backlog)

| PHASE | 상태 | 사유 |
|---|---|---|
| PHASE 1 (Agent-first IA) | 부분 완료 | 카드 순서는 이미 Agent 중심. AgentStrategyChoice 추가로 흐름 보강 |
| PHASE 2 (디자인 시스템 120점) | backlog | 잔존 legacy 탭(Approvals/AuditLog/BotControl/MarketChart/Backtest) 토큰화 — 탭당 별도 PR 권장 |
| PHASE 3 (PC 12-column grid) | backlog | 현재 auto-fit grid가 카드 2~3열 자동 배치. 명시 3열은 운영자 피드백 후 |
| PHASE 4 (Mobile Operator Mode) | 부분 완료 | CSS triage로 핵심만 표시. 별도 페이지/라우팅은 backlog |
| PHASE 5 (Strategy Selection) | 완료 | AgentStrategyChoiceCard 신규 |
| PHASE 6 (Error/Empty 상태) | 이미 완료 | 신규 카드 4개(#33/#37/#39/#42) 모두 친화 메시지 적용 |
| PHASE 7 (Navigation) | backlog | BottomNav 5탭 + 더보기 — 모든 탭 라우팅 동시 변경 필요 |

## 검증 결과 (이번 라운드)

- frontend vitest **1095 passed** (+8 신규 AgentStrategyChoiceCard 테스트).
  1087 → 1095.
- frontend lint **0 errors** (66 warnings — 사전 baseline).
- frontend build **OK** — 478 KB → 131 KB gzipped.
- Dashboard 단독 회귀 **82 passed**.
- backend 변경 0건 — pytest 영향 없음.

## 안전 invariant

- broker / RiskManager / PermissionGate / OrderExecutor / route_order /
  실 주문 로직 변경 **0건**.
- 실제 broker live order 호출 **0건**.
- LIVE / AI execution / Futures live flag 변경 **0건**.
- API Key / App Secret / 계좌번호 / AI Key 변경 **0건**.
- backend API contract 변경 **0건**.
- 변경: frontend UI 1개 신규 컴포넌트 + CSS 1 클래스 + Dashboard mount/wrap +
  banner 글자 키움.

## 후속 권장 PR (단독 분리 권장)

1. **legacy 탭 토큰화** — Approvals / AuditLog / BotControl / Backtest (각각
   별도 PR, 200+ 테스트 회귀 검증).
2. **Mobile BottomNav 5탭** — 홈 / 에이전트 / 승인 / 리스크 / 설정 + 더보기.
3. **PC 명시 3열 grid** — 좌 운영 / 중앙 KPI+Agent / 우 리스크.
4. **Agent decision history 그래프** — 시간순 변동 시각화.
5. **`MobileOperatorMode` 별도 페이지** — 운영자 피드백 수렴 후 결정.
