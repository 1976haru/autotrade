# KIS Paper One-Click Test — #89

> 한투 모의투자 API 를 사용한 *원클릭* AI 자동매매 모의 테스트. 사용자는
> "준비상태 확인" 후 "한투 모의 빠른 점검 시작" 버튼만 누르면, AI 가 자동으로
> 매수/매도 *판단* → 모의 *주문* → 체결 *조회* → 잔고/포지션 *확인* → 결과
> *리포트* 까지 수행한다.
>
> **본 테스트는 실거래 전환이 *아니다*.** 실거래 활성화는 별도 PR + 사용자
> 명시 승인 + Live Manual Gate (#73) + Live Activation Blockers 통과 후에만
> 가능.

## 1. 한 줄 요약

| 항목 | 값 |
|---|---|
| 대상 | 한투 KIS Open API *모의투자* 전용 (`KIS_IS_PAPER=true` 강제) |
| 실거래 발생 | ❌ — KIS LIVE place_order 는 `NotImplementedError` |
| 사용자 UX | 5개 버튼 (준비상태 / quick / slow / mock / 정지) |
| 안전 flag | `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false` 강제 |
| 우회 가능 | ❌ — `route_order` → RiskManager → OrderExecutor 그대로 |

## 2. 3가지 테스트 모드

| 모드 | KIS API | tick 수 | 간격 | 용도 |
|---|---|---|---|---|
| **quick** (한투 모의 빠른 점검) | ✅ KIS Paper | 1~3 | ≥3초 | 1~3분 내 시작/종료 확인 |
| **slow** (한투 모의 느린 스트레스) | ✅ KIS Paper | 20~50 | 3~5초 | 안정성 — KIS rate limit 검증 |
| **mock** (내부 Mock 고속 스트레스) | ❌ MockBroker | 50+ | 0초 | 외부 의존성 0건 — 내부 시스템 부하 확인 |

각 모드는 *명확히 다른 버튼* — UI 에서 "한투 모의" 모드와 "내부 Mock" 모드를
혼동하지 않게 라벨 구분.

## 3. 안전 정책

### 3.1 14개 절대 원칙 (CLAUDE.md + 본 PR)

1. 실계좌 주문 0건 — `KisBrokerAdapter.place_order(is_paper=False)` 는
   `NotImplementedError` (kis.py L181)
2. `KIS_IS_PAPER=false` 변경 0건
3. `ENABLE_LIVE_TRADING=true` 변경 0건
4. `ENABLE_AI_EXECUTION=true` 변경 0건
5. `ENABLE_FUTURES_LIVE_TRADING=true` 변경 0건
6. 실 계좌번호 / API Key / Secret 변경 0건
7. Frontend 에 KIS key/secret/account 저장 0건 — 입력 form 0개 (테스트로 lock)
8. EXE 안에 Secret 굽지 않음
9. Tauri bundle 에 `.env` 포함 0건
10. 실전 주문 버튼 0개
11. "지금 매수" / "지금 매도" / "실거래 시작" / "Place Order" 라벨 button 0개
    (테스트로 lock)
12. UI 에 *"한투 모의투자 / 실제 돈 안 나감"* 영구 배지
13. KIS API rate limit 고려 — 초당 다중 호출 금지 (rate_limit_seconds=3 default)
14. **MockBroker 로의 silent fallback 0건** — KIS Paper API 실패 시 사용자에게
    친화 메시지로 surface, 자동 swap 안 함

### 3.2 readiness 가드 (`evaluate_readiness`)

다음 *어느 하나라도* 위반되면 `can_run_kis_paper=False`:

- `kis_is_paper == False`
- `enable_live_trading == True`
- `enable_ai_execution == True`
- `enable_futures_live_trading == True`
- `default_mode` 가 `LIVE_*` 계열 (LIVE_SHADOW 제외)
- KIS key / secret / account 셋 중 하나라도 빈 값

readiness 응답은 **Secret 원문 0건** — `*_present: bool` 만 carry. 테스트로 lock.

## 4. 흐름

```
사용자 → "준비상태 확인" 클릭
   ↓
GET /api/kis-paper/readiness
   → readiness.can_run_kis_paper / can_run_mock / safety_flags / detail_messages
   ↓
사용자 → "한투 모의 빠른 점검 시작" 클릭
   ↓
[확인 모달] — "모의투자 주문 테스트 시작" 버튼 클릭 필수 (confirm=true 강제)
   ↓
POST /api/kis-paper/start { mode: "quick", confirm: true }
   ↓ readiness 재검증 → 실패 시 400 BLOCKED
   ↓ engine.start(mode=QUICK, readiness=rd)  ← async background task
   ↓
각 tick (≥3초 간격):
   - AI 판단 (counter += 1)
   - BUY/SELL 신호 → (engine 의 tick_runner 가 주입한 broker 호출 흐름)
   - 결과 → counters 갱신
   - KIS rate limit 또는 예외 → 즉시 break + failure 기록
   ↓
GET /api/kis-paper/status (사용자가 주기적으로 확인)
   → state / mode / counters / failures
   ↓
완료 시:
GET /api/kis-paper/report
   → score (0~100) + 4등급 + safety_note + counters
```

## 5. 점수판 (0~100, 4 등급)

| 항목 | 점수 |
|---|---|
| readiness 통과 | 10 |
| KIS paper 또는 Mock 연결 성공 | 15 |
| 잔고 조회 성공 | 10 |
| AI 판단 1회 이상 | 10 |
| 모의 주문 1건 이상 실행 | 15 |
| 체결/미체결 조회 1건 이상 | 10 |
| 포지션/잔고 재조회 | 10 |
| RiskManager 차단 1건 이상 관찰 | 10 |
| audit 누락 0건 | 5 |
| 오류 0건 | 5 |
| **합계** | **100** |

**4 등급**:
- **90~100**: 장기 Paper/Shadow 검증 후보
- **75~89**: Paper 추가 검증 필요
- **60~74**: 전략/주문 안정성 보완 필요
- **0~59**: 실전 검토 금지

> **"실거래 가능" / "LIVE 시작" 같은 라벨은 점수 문구에 *절대* 들어가지
> 않는다** — dataclass `__post_init__` 가드 + 테스트로 lock.

## 6. 사용자 흐름 (베타테스터)

### 6.1 EXE 가 있는 경우 (후속 PR)

1. `AgentTrader-v1-Setup.exe` 더블클릭 설치
2. 바탕화면 아이콘 실행
3. 대시보드 → "한투 모의투자 AI 자동매매 테스트" 카드
4. "준비상태 확인" → KIS_IS_PAPER / LIVE flag 확인
5. "한투 모의 빠른 점검 시작" → 확인 모달 → "모의투자 주문 테스트 시작"
6. 1~3분 후 결과판 + 점수판 확인

### 6.2 EXE 가 없는 현재 (#89 PR 시점)

1. 프로젝트 clone / zip 다운로드
2. `scripts/start_kis_paper_test_windows.bat` 더블클릭
   - Python 의존성 자동 설치
   - backend 자동 실행 (port 8000)
3. 브라우저 → `http://localhost:5173` (frontend dev 서버 별도 시작 필요)
4. 이후 §6.1 의 4~6 단계와 동일

자세한 EXE 상태: [`docs/desktop_exe_status.md`](desktop_exe_status.md).

## 7. AI 자동 매수·매도 흐름

본 시스템은 *기존 6개 전략* 의 신호를 사용 (#81):
- `sma_crossover`, `rsi_reversion`, `vwap_strategy`, `orb_vwap`,
  `volume_breakout`, `pullback_rebreak`

각 tick 에서:
1. 전략 1개 또는 여러 개 → `generate_signal(context)` → `StrategySignal`
2. RiskManager 사전검사 (#34)
3. BUY/SELL → `route_order` → OrderExecutor → broker (paper or mock)
4. 체결 결과 → `OrderAuditLog` + `AgentDecisionLog`

본 PR (#89) 의 engine 은 *default tick runner* 가 카운터 갱신만 함 — 실 broker
호출 흐름은 운영자가 본인 PC 에서 `KIS_APP_KEY` 등을 채운 상태에서 별도 wiring
(후속 PR) 으로 활성화. 본 PR 의 카운터 / 점수 / 안전 흐름은 모두 *engine
오케스트레이션 수준* 에서 검증 가능.

## 8. UI 구성 — `KisPaperOneClickTestCard`

- 헤더: "🧪 한투 모의투자 AI 자동매매 테스트" + "한투 모의투자 전용 · 실제
  돈 안 나감" 배지
- 안내 박스: RiskManager / PermissionGate / OrderExecutor 우회 0건 명시
- 준비 상태: 모드 / KIS_IS_PAPER / 실거래 차단 / AI 자동 실행 차단 / KIS Key
  입력 여부 / KIS Paper 가능 / Mock 가능
- 5개 버튼:
  1. 준비상태 확인
  2. 한투 모의 빠른 점검 시작 (KIS paper 불가하면 disabled)
  3. 한투 모의 느린 스트레스 시작 (동일)
  4. 내부 Mock 고속 스트레스 시작 (live flag false 면 활성)
  5. 테스트 정지 (RUNNING 상태에서만 활성)
- 확인 모달: "모의투자 주문 테스트 시작" 버튼 클릭 필수
- 결과판: AI 판단 / 매수 / 매도 / 주문 시도 / 실행 / 거절 / 체결 / 미체결 /
  리스크 차단 / 오류
- 점수판: 0~100 + 등급 라벨 + 한 줄 평가
- 실패 / 차단 메시지 박스 (있을 때만)

## 9. 안전 invariant (테스트로 lock)

| invariant | 검증 위치 |
|---|---|
| readiness 응답에 secret 원문 0건 | `test_kis_paper_readiness.py::test_readiness_does_not_carry_secret_values` |
| `is_order_intent` / `is_order_signal` `False` 불변 | `test_kis_paper_readiness.py::test_readiness_rejects_true_*` |
| BLOCKED 모드에서 engine 진행 0건 | `test_kis_paper_engine.py::test_kis_paper_blocked_when_*` |
| KIS 모드 예외 시 mock 으로 silent swap 0건 | `test_kis_paper_engine.py::test_kis_mode_does_not_silent_fallback_to_mock_on_error` |
| 점수 문구에 "실거래 가능" / "LIVE 시작" 단어 0건 | `test_kis_paper_engine.py::test_score_one_liner_does_not_contain_live_trading_phrases` |
| UI 에 "지금 매수" / "Place Order" 버튼 0개 | `KisPaperOneClickTestCard.test.jsx` |
| UI 에 KIS key/secret 입력 form 0개 | 동일 |
| 확인 모달 통과 전 backend 호출 0건 | 동일 |
| broker / OrderExecutor / route_order 직접 import 0건 (engine) | `test_kis_paper_engine.py` 정적 grep |

## 10. 후속 작업 (#89 의 의도적 미완 부분)

- 실 KIS Paper API 호출을 흘리는 tick runner — 본 PR 시점에는 default runner
  가 *카운터 갱신만* 한다. 운영자가 본인 PC 에서 `KIS_APP_KEY` 등을 채운
  상태에서 별도 wrapper 주입 시 활성화.
- DailyReport 와의 통합 — `KisPaperRunReport` 를 `reports/kis-paper/` 에 markdown
  저장 (storage 정책은 `.gitignore` 의 `reports/*` 와 lockstep).
- AuditEvent 와의 통합 — 본 PR 시점 engine 자체는 *AuditEvent 미작성*.
  실 broker 호출 흐름 활성화 시점에 `route_order` 가 자동으로 OrderAuditLog
  를 남기므로 추가 코드 0건.

## 11. 참고

- [`docs/system_audit_2026_05.md`](system_audit_2026_05.md) — 6 전략 / 안전
  가드 / 모드 전체 카탈로그 (#87)
- [`docs/risk_policy.md`](risk_policy.md) — RiskManager 평가 순서
- [`docs/promotion_policy.md`](promotion_policy.md) — Paper → Live 승격 정책
- [`docs/desktop_exe_status.md`](desktop_exe_status.md) — EXE 상태
- [`docs/beta_tester_install_guide.md`](beta_tester_install_guide.md) — 초보자
  설치 가이드
- [`docs/status/known_risks.md`](status/known_risks.md) — KIS 실 연결 미검증
  (§3.3)
