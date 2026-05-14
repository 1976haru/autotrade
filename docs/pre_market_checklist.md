# Pre-market Checklist (#80 + #91) — 초보자 흐름 요약

본 문서는 **베타테스터가 EXE 앱을 더블클릭한 직후** 보게 되는 `Pre-market
Checklist` 카드의 *동작 요약*이다. 정책 원문 / 항목 매트릭스는
[`docs/pre_market_check_policy.md`](pre_market_check_policy.md) 를 참조.

## 1. 한 줄 요약

| 단계 | 답 |
|---|---|
| 이 화면은 무엇? | 자동매매 시작 *전* 안전 점검 결과 |
| 무엇을 점검? | 11 카테고리 (API/DB/Broker/Data/Watchlist/Strategy/Risk/KillSwitch/Agent/Notification/Governance) + #91 desktop / kis_paper 2 카테고리 |
| 이 카드가 직접 시작하는가? | ❌ 아니오 — 본 카드는 *읽기 전용*, 시작은 BotControl / KIS Paper test 카드에서 |
| FAIL 이 있으면? | One-click paper test 시작 버튼이 *모두 disabled* |
| `.env` 를 수정하는가? | ❌ 아니오 — 수정 안내만 제공 |
| Secret (API Key / 계좌번호) 입력 받는가? | ❌ 아니오 — 본 화면은 입력 form 0개 |

## 2. 사용자가 보는 흐름

```
1. AgentTrader EXE 더블클릭
        ↓
2. Tauri 메인 윈도우 + backend sidecar 자동 spawn
        ↓
3. 대시보드 진입
        ↓
4. Pre-market Checklist 카드 노출 (모드별 헤드라인)
        ├─ READY_TO_START   → "오늘 자동운용 가능"  (녹색)
        ├─ WARN_BUT_START   → "주의 필요"           (주황)
        └─ DO_NOT_START     → "시작 금지"           (빨강) + 초보자 안내 블록
        ↓
5. (DO_NOT_START 시) backend/.env 의 4개 flag 점검
        - KIS_IS_PAPER=true
        - ENABLE_LIVE_TRADING=false
        - ENABLE_AI_EXECUTION=false
        - ENABLE_FUTURES_LIVE_TRADING=false
        ↓
6. backend 재시작 + "다시 점검" 버튼 클릭
        ↓
7. READY_TO_START 도달 시 → KIS Paper One-Click Test 카드의 시작 버튼 활성
        ↓
8. 시작 버튼 클릭 → 확인 모달 → 모의투자 자동매매 진행
```

## 3. #91 신규 항목 (desktop / kis_paper 카테고리)

| 항목 | 카테고리 | 의미 | 차단 시 안내 |
|---|---|---|---|
| `desktop_sidecar` | `desktop` | backend sidecar 가 frontend 에 연결됐는지 | 앱을 다시 시작하거나 `scripts/start_kis_paper_test_windows.bat` 으로 backend 수동 실행 |
| `desktop_status_endpoint` | `desktop` | `/api/status` 응답 OK 여부 | backend 가 실행 중인지 확인 |
| `kis_is_paper_safety` | `kis_paper` | `KIS_IS_PAPER=true` 여부 (SIM/PAPER/LIVE_SHADOW 필수) | `backend/.env` 에서 `KIS_IS_PAPER=true` 후 재시작 |
| `kis_paper_readiness` | `kis_paper` | `/api/kis-paper/readiness` aggregate (#89) | 차단 사유 (예: `ENABLE_LIVE_TRADING_TRUE`) 라벨 표시 |
| `kis_paper_capability` | `kis_paper` | KIS 모드 / Mock 모드 둘 다 차단? mock 만? 둘 다 가능? | FAIL/WARN/PASS 분기 |
| `enable_live_trading_safety` | `agent` | `ENABLE_LIVE_TRADING=false` 인지 | true 면 backend/.env 에서 false 로 변경 후 재시작 |
| `enable_ai_execution_safety` | `agent` | `ENABLE_AI_EXECUTION=false` 인지 | true 면 backend/.env 에서 false 로 변경 후 재시작 |
| `enable_futures_safety` | `agent` | `ENABLE_FUTURES_LIVE_TRADING=false` 인지 | true 면 backend/.env 에서 false 로 변경 후 재시작 |

## 4. 신규 출력 필드

`kis_paper_test_allowed` — `start_allowed=True` 이고 KIS Paper / Mock 중 하나라도
가능할 때만 True. 본 값이 False 면 `KisPaperOneClickTestCard` 의 quick / slow /
mock 3개 시작 버튼이 *모두 disabled*.

## 5. 절대 원칙 (확장 후에도 동일)

1. **본 카드는 read-only** — 주문 / mode 변경 / 안전 flag 토글 0건.
2. **실거래 시작 / 지금 매수 / Place Order 라벨 버튼 0개** (테스트로 lock).
3. **secret 입력 form 0개** — KIS Key / Secret / 계좌번호는 `backend/.env`
   에서만 관리.
4. **required FAIL 우회 불가** — "확인했습니다" 버튼은 UI 상태만 변경,
   `start_allowed` 는 절대 우회되지 않음.
5. **`.env` 자동 수정 0건** — 본 카드는 사용자에게 *수정 안내*만 제공.

## 6. 자주 묻는 질문

**Q. 점검을 통과했는데 KIS Paper 모드 시작 버튼이 여전히 회색이에요.**
→ 본 카드의 `start_allowed=True` 와 별개로 `KisPaperOneClickTestCard` 의
`readiness.can_run_kis_paper` 가 False 일 수 있습니다. `backend/.env` 에 KIS
App Key / Secret / 계좌번호가 입력돼 있는지 확인하세요.

**Q. 모든 항목이 PASS 인데 SIMULATION 모드에서 시작이 안돼요.**
→ SIMULATION 모드에서는 KIS 모드 시작 버튼이 아닌 **Mock 모드 시작 버튼**을
사용해야 합니다. KIS API 호출 없이 내부 mock broker 로 진행됩니다.

**Q. 점검 결과를 어떻게 운영자에게 공유하나요?**
→ "세부 항목 펼치기" 버튼으로 모든 점검 항목을 표 형태로 노출 후 스크린샷
권장. backend CLI `scripts/pre_market_check.py --format markdown` 로 markdown
출력도 가능합니다.

## 7. 참고

- [`docs/pre_market_check_policy.md`](pre_market_check_policy.md) — #80 정책 원문 + §10-A #91 확장
- [`docs/exe_oneclick_installation.md`](exe_oneclick_installation.md) — EXE 설치 흐름 (#90)
- [`docs/desktop_exe_status.md`](desktop_exe_status.md) — EXE 빌드 산출물 상태 (#90 / 90-A)
- [`docs/kis_paper_oneclick.md`](kis_paper_oneclick.md) — KIS Paper one-click test 정책 (#89)
