# Risk Control Panel 정책 (체크리스트 #62)

## 1. 목적

운영자가 *즉시 위험을 멈출 수 있는* 단일 액션 UI다. 기존 `KillSwitchPanel`
(read-only 상태 표시)과 `BackendPolicyCard`(22개 정책 필드 전체 dump)와
별도로, **3단계 Kill Switch 실행 버튼 + 핵심 5개 제한값 + 미체결/청산 후보**를
한 카드에 모은다.

본 패널은 *AI 매매 전환용 안전 인프라*다 — AI / 전략 / 수동 주문이 자동으로
broker로 가지 않도록 운영자가 한 화면에서 모든 단계를 통제할 수 있게 한다.

## 2. 절대 원칙 (CLAUDE.md)

| 원칙 | 강제 |
|---|---|
| 본 패널은 broker.place_order / cancel_order 호출 0건 | 모든 액션은 `/api/risk/emergency-stop` 토글 + read-only candidate endpoint만 호출. invariant 테스트로 lock |
| 자동 전량청산 / 자동 취소 버튼 *생성 금지* | LEVEL 2 / 3는 candidate 표시까지만. "자동 전량청산 버튼은 의도적으로 생성되지 않았습니다" 경고 banner 항상 노출 |
| 위험 버튼은 모달 없이 실행 불가 | 4개 actionType 모두 ConfirmModal 필수 (테스트로 lock) |
| LIVE / AI / FUTURES flag 토글 노출 0건 | `SafetyFlagsRow`는 상태 *표시*만 — 토글 버튼 없음 |
| AI Key / Secret / 계좌번호 입력 필드 0개 | 모달은 운영자명 / 사유만 받음 |
| backend 주문/브로커/OrderExecutor 로직 변경 0건 | 본 PR은 frontend 신규 컴포넌트 + 문서 + 테스트만 |

## 3. UI 구조

```
RiskControlPanel
├── 헤더 (🛡 리스크 컨트롤 패널 + 현재 level 배지)
├── 안내 문구 (즉시 멈출 수 있다 + broker API 직접 호출 안 함)
├── SafetyFlagsRow (실거래 / AI / 선물 / 긴급정지 chip)
├── 3단계 Kill Switch 버튼 (모달 필수)
│   ├── ⛔ 신규매수 중단 (LEVEL 1)
│   ├── 📋 미체결 취소 후보 확인 (LEVEL 2)
│   ├── 🚨 청산 후보 표시 (LEVEL 3)
│   └── ✓ Kill Switch 해제 (OFF로 복귀) — level != OFF 일 때만
├── 자동 전량청산 비활성 invariant banner
├── RiskLimitsSummary (5개 핵심 한도)
├── CancelCandidatesList (LEVEL 2 확인 후 채워짐)
├── LiquidationCandidatesList (LEVEL 3 확인 후 채워짐)
├── inline error (policy / status 일부 실패)
└── ↻ 새로고침 버튼
```

## 4. 3단계 Kill Switch 의미

| Level | 액션 | 실제 효과 | 운영자 후속 |
|---|---|---|---|
| LEVEL 1 | 신규매수 중단 | `RiskManager.emergency_stop=True` 토글 → 모든 신규 주문이 RiskManager에서 즉시 REJECTED | 위험 상황 해소 후 OFF로 해제 |
| LEVEL 2 | 미체결 취소 후보 표시 | `level=LEVEL_2` 토글 + `GET /api/risk/emergency-stop/cancel-candidates` 호출 → 미체결 / 승인 대기 주문 목록 *표시* | 결재 탭에서 한 건씩 수동 취소 (또는 stale 일괄 취소 #065) |
| LEVEL 3 | 청산 후보 표시 | `level=LEVEL_3` 토글 + `GET /api/risk/emergency-stop/liquidation-candidates` 호출 → 보유 포지션 + 미실현 PnL *표시* | 호가 / 시장 상황 확인 후 별도 수동 승인 흐름으로 청산 |

**LEVEL 2 / 3은 broker.cancel_order / broker.place_order를 호출하지 *않는다***.
실제 취소 / 청산은 항상 운영자의 별도 수동 승인 절차다 — 자동 전량청산은
호가 공백 / 급락 상황에서 시장가 슬리피지로 손실이 폭증할 수 있어 금지.

## 5. 확인 모달 (RiskActionConfirmModal)

각 액션은 ConfirmModal을 *반드시* 거친다. 모달 내용:

- **제목**: actionType별 단정 문구 (예: "신규매수 중단 (LEVEL 1)")
- **요약 패널**:
  - 무엇을 실행할지 (description)
  - 백엔드에서 실제로 일어나는 일 (helperText)
  - **명시적 안전 invariant 글머리표** (actionType별로 다름):
    - LEVEL 1: "신규 매수만 중단 / 자동 청산하지 않음 / 자동 취소하지 않음"
    - LEVEL 2: "미체결 주문 취소 후보만 표시 / 실제 cancel_order 호출 발생하지 않음 / 취소는 결재 탭에서 운영자가 수동 진행"
    - LEVEL 3: "청산 후보로 표시 / 자동 전량청산 비활성화 / 실제 청산은 별도 수동 승인"
    - RESUME: "OFF로 되돌림 / 기본 가드는 유지"
- **운영자명 / 사유 입력** (DecisionDialog 기본 — 감사 추적)
- **확인 버튼** (accent 색상은 위험도에 따라 amber / red / green)
- **취소 버튼** (cancelLabel="취소")

`busy` 상태에서는 버튼이 비활성화되고 본문이 "처리 중…"으로 변경된다.
`onConfirm`이 `{ok: false, message}`를 반환하면 모달은 *열린 채로* 에러
메시지를 inline 표시 (DecisionDialog 072 패턴).

## 6. 후보 목록 표시 invariant

### CancelCandidatesList

LEVEL 2 모달 확인 후 채워진다. 각 row:
- 종목 / side / 수량 / 주문 타입
- created_at + status + reason
- 상단에 amber banner: "⚠ 실제 취소 아님 · 운영자가 결재 탭에서 수동 취소해야 합니다."

빈 상태: "미체결 취소 후보가 없습니다."

### LiquidationCandidatesList

LEVEL 3 모달 확인 후 채워진다. 각 row:
- 종목 / 수량 / 평단 / 현재가 / 미실현 PnL / risk_reason
- 상단에 red banner: "⚠ 자동 청산 아님 · 호가/시장 상황 확인 후 수동 승인 필요. 자동 전량청산 버튼은 비활성화되어 있습니다."
- 총 미실현 PnL 카운트 (positive=green, negative=red)

빈 상태: "청산 후보가 없습니다."

## 7. 제한값 표시 (RiskLimitsSummary)

핵심 5개만 카드로 highlight — 22개 전체는 기존 `BackendPolicyCard`에서:

1. **1회 주문 한도** (`max_order_notional`, KRW)
2. **종목별 노출 한도** (`max_symbol_exposure`, KRW)
3. **총 노출 한도** (`max_total_exposure`, KRW)
4. **최대 보유 종목** (`max_positions`, 건)
5. **일일 손실 한도** (`max_daily_loss`, KRW)

값이 `0`이면 "비활성"으로 회색 표시 — 운영자가 "한도를 안 걸어놨다"를 명확히
인지하도록.

값이 null / undefined이면 "설정값 없음".

policy 전체가 null이면 "설정값 없음 — 백엔드 연결 대기 중 또는 기본 안전값
사용."

## 8. 안전 flag 표시 (SafetyFlagsRow)

| Chip | OK 조건 | OK 색 | 위험 색 |
|---|---|---|---|
| 실거래 | `enable_live_trading=false` | green (비활성) | red (활성화됨) |
| AI 실행 | `enable_ai_execution=false` | green (비활성) | red (활성화됨) |
| 선물 실거래 | `enable_futures_live_trading=false` | green (비활성) | red (활성화됨) |
| 긴급 정지 | `emergencyStop=false` | green (OFF) | red (ON) |

토글 버튼은 *없다* — chip은 상태 표시 전용. 변경은 backend env 또는 본 패널의
3단계 Kill Switch 버튼을 통해서만 가능.

## 9. 모바일 UX

- 헤더 + level 배지는 `flexWrap`으로 좁은 화면에서도 2줄로 자동 정렬
- 3단계 버튼은 `Btn full` (가로 100%) — 모바일에서 큰 터치 영역
- 후보 list는 row마다 padding 6-8px + 2-3줄로 분리 — 가로 table 없음
- 모달은 `DecisionDialog` 기본 `maxWidth: 90vw` — 모바일에서 거의 풀스크린
- `RiskLimitsSummary`는 2-column grid — 모바일에서도 5개가 한 화면에 들어감

## 10. 에러 / Empty 상태

모든 raw 에러 메시지는 `friendlyErrorMessage`를 거친다:
- raw `Failed to fetch` → "백엔드 연결이 끊겼습니다. 'uvicorn ...' 실행 후 새로고침" (로컬) 또는 GitHub Pages Demo 안내
- 그 외 → 백엔드의 의미 메시지 그대로

policy + status 둘 다 실패: `ErrorState` 전체 카드 표시 + "다시 시도" 버튼
하나만 실패: inline 노란색 작은 텍스트로 어느 쪽이 안 됐는지 명시

candidate list 실패: list 자리에 red banner로 친화 메시지

## 11. 컴포넌트 / 함수 export

`RiskControlPanel.jsx`는 다음을 export하여 다른 화면에서도 재사용 가능:

- `RiskControlPanel` — 메인 orchestrator (StrategyRisk 탭에 마운트됨)
- `RiskLimitsSummary` — 5개 한도 카드 (독립 테스트 가능)
- `SafetyFlagsRow` — 4개 chip (독립 테스트 가능)
- `CancelCandidatesList` — LEVEL 2 list
- `LiquidationCandidatesList` — LEVEL 3 list
- `RiskActionConfirmModal` — 4 actionType ConfirmModal

## 12. 절대 invariant (변경 금지)

1. **`broker.place_order` / `broker.cancel_order` / `route_order` 직접 호출 0건** — backendApi mock 검증으로 lock.
2. **`ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 변경 0건** — env 변경 또는 backend 코드 변경 없음.
3. **자동 전량청산 버튼 생성 금지** — 코드/테스트로 lock ("자동 청산 아님" / "Liquidate Now" 등 라벨 0개).
4. **위험 버튼은 모달 필수** — 직접 onConfirm 호출 없이 setPendingAction → ConfirmModal → handleConfirm 분기.
5. **`PermissionGate.approve` / `cancel` 직접 호출 0건** — 본 패널은 결재 흐름과 *별개*. 결재 탭에서만 처리.
6. **AI Key / Secret / 계좌번호 입력 필드 0개** — 모달은 운영자명 / 사유만.
7. **`friendlyErrorMessage` 경유** — raw "Failed to fetch" 노출 금지.

## 13. 관련 PR / 체크리스트

- #34 RiskManager 표준 진입점
- #37 3-Level Kill Switch (LEVEL_1/2/3 enum + apply_kill_switch_to_risk + compute_cancel/liquidation_candidates)
- #46 Emergency Stop audit trail
- #47-#048 EmergencyStopConfirmModal + decided_by 입력
- #053 Dashboard risk summary
- #060 emergency_stop hard short-circuit
- #62 Risk Control Panel (본 PR)
- #069 Emergency Stop stuck banner
- #213 frontend runtime resilience
