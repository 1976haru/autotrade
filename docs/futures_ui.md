# Futures UI Policy (#50)

본 문서는 Futures 탭의 *UI 노출 정책*을 정의한다. backend의 `ENABLE_FUTURES_LIVE_TRADING=False` invariant + `FuturesRiskManager.evaluate_order` 항상 REJECTED 위에 추가되는 **frontend 노출 레이어**로, 사용자가 주식 자동매매와 선물 기능을 혼동하지 않도록 한다.

## 1. 핵심 원칙

| 원칙 | 구현 |
|---|---|
| Futures 탭은 **기본 비활성** (navigation 미노출) | `FEATURES.futuresTab=false` default |
| flag로만 노출 | `VITE_ENABLE_FUTURES_TAB=true` env 명시 옵트인 |
| 노출되어도 **Simulation Only / Read-only** | `Futures.jsx`의 7개 안전 섹션 |
| 실제 선물 주문 버튼 **비활성** | `disabled={true}` + onClick 없음 |
| **모바일 BottomNav에 직접 노출하지 않음** | `mobileExclude=true` (flag=true여도 모바일 미노출) |
| 주식/선물 혼동 방지 banner | 화면 최상단 고정 |

본 정책은 *UI 노출* 전용 — backend safety flag(`ENABLE_FUTURES_LIVE_TRADING`)와 *별개*이며, 실제 broker 호출 여부에 영향을 주지 않는다 (broker 호출은 backend의 다층 가드로 0건 유지).

## 2. Frontend feature flag

```js
// frontend/src/config/features.js
import { FEATURES } from "./config/features";

if (FEATURES.futuresTab) {
  // 노출
}
```

| 항목 | 값 |
|---|---|
| 환경변수 | `VITE_ENABLE_FUTURES_TAB` |
| 기본값 | `false` (운영자가 명시 옵트인 안 한 상태에서 자동 노출 X) |
| 변경 위치 | `frontend/.env` |
| 영향 | TopNav (PC) — flag=true 시 노출 |
| 영향 | BottomNav (mobile) — flag 무관 *항상 미노출* (mobileExclude 정책) |
| 영향 | URL/state 강제 접근 — flag=false 시 `<FuturesDisabledNotice />` 표시 |
| **backend invariant 영향** | **0** — 본 flag는 UI 노출 전용 |

테스트 helper (production 코드에서 호출 금지):
```js
import { __setFeatureForTest, __resetFeaturesForTest } from "./config/features";
__setFeatureForTest("futuresTab", true);  // 테스트용 토글
__resetFeaturesForTest();                  // 모든 override 초기화
```

## 3. Navigation 정책

### `getNavTabs()` (PC TopNav)
- `FEATURES.futuresTab=false` → futures 탭 미노출 (10개 탭만)
- `FEATURES.futuresTab=true`  → futures 탭 노출 (11개 탭)

### `getMobileNavTabs()` (BottomNav)
- **flag 값과 무관하게 항상 futures 미노출** (`mobileExclude=true` 정책)
- 사용자가 모바일에서 선물 기능을 발견할 가능성을 줄이기 위해 — 주식 자동매매 화면과 명확히 구분

### `isTabVisible(tabId)`
- 임의 탭 id가 *어느 navigation에서든* 노출되는지 boolean 반환

### URL/state 강제 접근 fallback
`App.jsx`의 `case "futures"`가 flag를 검사:
- flag=true  → `<Futures />` (정식 화면)
- flag=false → `<FuturesDisabledNotice />` (안전 안내)

## 4. Futures 화면 구조 (`<Futures />` — flag true 시)

7개 섹션 + audit / margin sub-card:

1. **혼동 방지 banner** — "이 화면은 주식 자동매매 화면이 *아닙니다*"
2. **Disabled banner + 4 safety badges** — Simulation Only / Read-only / FUTURES_LIVE OFF / 실제 주문 0건
3. **Risk warning** — 레버리지 / 증거금 / 강제청산 / 만기 / 야간 / AI 제한 (6개 항목)
4. **Safety matrix** — ENABLE_FUTURES_LIVE_TRADING / 실제 주문 / AI 선물 실행 / MockFuturesBroker / FuturesRiskManager / Manual approval
5. **`<FuturesMarginRiskCard />`** (#48) — read-only margin/leverage/liquidation 사전 평가
6. **`<FuturesOrderAuditCard />`** (#194) — 가상 선물 audit log
7. **Disabled order area** — 매수/매도/청산 버튼 모두 `disabled`, label "비활성"
8. **Activation checklist** — 8단계 (모두 ❌ 미통과 — 별도 옵트인 PR 필요)

## 5. Visual safety 정책

- 위험은 **red/coral** (#ef4444)
- 주의는 **amber** (#fbbf24, #f59e0b)
- simulation은 **purple/blue** (#a78bfa, #7dd3fc)
- **"실제 주문 가능"처럼 보이는 green primary CTA 금지** — 본 화면에 활성 green button 0개
- "활성화" / "Enable Futures" / "주문 실행 시작" 같은 *enabling* 문구의 활성 button 0개 (테스트로 lock)

## 6. API fallback

`FuturesOrderAuditCard` / `FuturesMarginRiskCard`가 backend API 호출에 실패하면 raw `Failed to fetch`를 노출하지 않는다 — 두 카드 모두 friendly fallback 문구 + empty state 표시:

- "선물 감사 데이터를 아직 불러오지 못했습니다."
- "GitHub Pages 데모에서는 mock 또는 빈 상태로 표시됩니다."
- "실제 선물 주문은 비활성화되어 있습니다."
- "아직 가상 선물 주문 기록이 없습니다."

(본 정책은 기존 카드 구현에 이미 반영됨 — 본 PR에서 추가 변경 0건.)

## 7. 절대 invariant (테스트로 강제)

| invariant | 가드 |
|---|---|
| `FEATURES.futuresTab` default `false` | `features.test.js::defaults to false for futuresTab` |
| `getNavTabs()` flag false 시 futures 제외 | `BottomNav.futures-flag.test.jsx::excludes futures when flag is false` |
| `getMobileNavTabs()` flag 무관 항상 futures 제외 | `BottomNav.futures-flag.test.jsx::excludes futures even when flag is true` |
| TopNav DOM에 flag=true 시에만 `top-nav-futures` 렌더 | `BottomNav.futures-flag.test.jsx::TopNav DOM rendering` (2 cases) |
| BottomNav DOM에 어떤 flag 상태에서도 futures 미렌더 | `BottomNav.futures-flag.test.jsx::BottomNav DOM rendering` (2 cases) |
| `<Futures />`의 모든 주문 버튼 `disabled` | `Futures.test.jsx::renders three disabled order buttons` |
| 주문 버튼 클릭 시 어떤 액션도 발생하지 않음 | `Futures.test.jsx::clicking a disabled button does NOT trigger any action` |
| "활성화" / "주문 실행 시작" 같은 enabling 라벨 활성 button 0개 | `Futures.test.jsx::does NOT render any '활성화' / 'enable' button` |
| `<FuturesDisabledNotice />`에 주문 버튼 0개 | `Futures.test.jsx::does NOT render any order buttons` |

## 8. 활성화 절차 (별도 옵트인)

Futures 탭을 *PC에서* 노출하려면:

1. 운영자 명시 검토 — 본 문서 §1 안전 정책 + activation checklist 8단계 확인
2. `frontend/.env`에 `VITE_ENABLE_FUTURES_TAB=true` 추가
3. `npm run build` 또는 `npm run dev` 재기동
4. PC TopNav에 "🪙 선물" 탭 노출 (모바일 BottomNav는 여전히 미노출)
5. 본 노출은 *UI 노출만* — 실제 broker 호출 0건 invariant 유지

**모바일 사용자가 선물 화면을 보려면**: PC TopNav 옵션을 enable한 상태에서 모바일 브라우저로 직접 URL 접근 시 화면 자체는 표시되지만, BottomNav에는 단축 진입점이 제공되지 않는다.

**실제 선물 주문 활성화는 별도 Phase + 별도 PR**:
- `docs/futures_scope.md` §10 실전 전 필수 조건
- `docs/live_activation_blockers.md` §3.1 9-step blocker
- `docs/futures_margin_risk.md` margin reconciliation
- `docs/futures_strategy_contract.md` strategy 승격 절차

## 9. 변경 시 동기화

- 새 frontend feature flag 추가 → `features.js` `_DEFAULTS` + `.env.example` + 본 문서 §2
- Futures 화면 섹션 추가/제거 → 본 문서 §4 + `Futures.test.jsx` 갱신
- 모바일 노출 정책 변경 → 본 문서 §3 + `BottomNav.futures-flag.test.jsx` 갱신
- "주문 가능"처럼 보이는 button 추가 → 본 문서 §5/§7 + 안전성 재검토 (활성 green CTA는 영구 금지)

## 관련 문서

- [`futures_scope.md`](futures_scope.md) — 선물 1차 범위 (#46)
- [`futures_broker_contract.md`](futures_broker_contract.md) — `FuturesBrokerAdapter` (#47)
- [`futures_margin_risk.md`](futures_margin_risk.md) — Margin/Leverage/Liquidation rules (#48)
- [`futures_strategy_contract.md`](futures_strategy_contract.md) — `FuturesStrategyBase` (#49)
- [`futures_simulation_report.md`](futures_simulation_report.md) — 가상 산식 (#151)
- [`live_activation_blockers.md`](live_activation_blockers.md) — LIVE 활성화 9-step
- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙 (특히 §6)
