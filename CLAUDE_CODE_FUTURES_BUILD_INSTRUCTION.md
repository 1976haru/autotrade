# Claude Code 통합지시문 — 국내선물 자동매매(autotradesun) 1차 확장 빌드

> **이 문서 한 장을 Claude Code에 그대로 붙여넣으면, 중간에 멈추지 않고 끝까지 작업한 뒤 결과만 보고하도록 설계되어 있다.**
> 원본(SOURCE): 국내주식 자동매매 `autotrade` (로컬 클론)
> 대상(TARGET): 국내선물 자동매매 `autotradesun` (현재 빈 저장소)

---

## 0. 사용법 (사람이 1회만 읽는 부분)

1. 로컬에 두 저장소를 나란히 둔다.
   ```bash
   git clone https://github.com/1976haru/autotrade.git
   git clone https://github.com/1976haru/autotradesun.git
   ```
2. `autotradesun` 폴더 안에서 Claude Code를 실행한다.
3. **이 문서 전체를 복사해 Claude Code에 한 번 붙여넣는다.**
4. 그 뒤로는 입력하지 않는다. Claude Code가 알아서 끝까지 작업하고, 마지막에 §11 형식의 단일 결과 보고만 출력한다.

> SOURCE 경로 기본값은 `../autotrade` 로 가정한다. 다르면 첫 줄에서만 경로를 알려주고, 그 외에는 질문하지 말 것.

---

## 1. 실행 모드 (Claude Code가 지켜야 할 최상위 규칙)

1. **중간에 사람에게 질문하거나 승인을 기다리지 말 것.** 모호한 결정은 "SOURCE(autotrade)의 기존 관례를 그대로 따른다"는 원칙으로 스스로 정하고, 어떤 선택을 했는지 §11 최종 보고에만 한 줄로 남긴다.
2. **단계별 중간 설명을 길게 출력하지 말 것.** 각 Phase는 조용히 수행하고, 끝나면 짧게 `✅ Phase N 완료`만 남긴다. 자세한 내용은 마지막 결과 보고로 모은다.
3. **모든 Phase를 끝까지 수행한 뒤에 멈춘다.** 한 Phase가 끝났다고 사람에게 넘기지 말 것.
4. **각 Phase는 독립 커밋으로 남긴다.** Phase 단위로 `git add -A && git commit` 한다(메시지 규칙 §9).
5. **테스트/린트/빌드가 빨간불이면 다음 Phase로 넘어가지 말고 그 자리에서 고친다.** 우회(`--no-verify`, force, skip)는 금지.
6. **작업이 막혀 도저히 자동 진행이 불가능할 때만** 멈추고, 무엇이 왜 막혔는지 §11 형식으로 보고한다(그 외에는 멈추지 않는다).
7. 마지막에 `git push` 까지 수행한다(§9). 인증 실패 시 push 명령만 보고에 적어둔다.

---

## 2. 목표 · "시작 버튼" 정의 · 안전 경계

### 2.1 목표
`autotrade`(국내주식)의 검증된 안전 아키텍처를 토대로, **국내선물(KOSPI200 선물 중심) 모의/Paper 자동매매 시스템**을 `autotradesun`에 완성한다. 사용자가 UI에서 **"국내선물 모의 자동매매 시작" 버튼 하나만 누르면 자동매매 루프가 실제로 끝까지 동작**해야 한다.

### 2.2 "시작 버튼 = 실제 작동"의 정확한 의미
시작 버튼을 누르면 다음이 **자동으로 끝까지 돈다**:
시세 수신 → 선물 전략 신호 생성 → `FuturesRiskManager` 평가(증거금/레버리지/청산거리) → 가상 체결(`MockFuturesBroker`) → `FuturesOrderAuditLog` 기록 → 포지션/손익/마진 대시보드 갱신 → 만기 임박 시 롤오버 *권고* 표시.
이 전체가 **`SIMULATION` / `PAPER` 모드에서 실제 broker 호출 0건으로 동작**하면 "실제 작동" 완료다.

### 2.3 안전 경계 (절대 변경 금지)
- 실제 돈이 들어가는 **선물 LIVE 실거래는 시작 버튼으로 켜지지 않는다.** `ENABLE_FUTURES_LIVE_TRADING=false` 기본값을 유지하고, `FuturesRiskManager`의 LIVE `evaluate_order`는 항상 `REJECTED`를 반환한다.
- 선물 LIVE 활성화는 SOURCE의 `docs/futures_promotion_policy.md` / `docs/live_activation_blockers.md` 다단계 게이트를 그대로 이식해 그 뒤에 둔다. 본 빌드 범위는 **모의/Paper까지**다.
- UI에 "실거래 시작", "LIVE 주문" 같은 *활성화된* 버튼을 만들지 않는다(존재하더라도 `disabled` 고정).

---

## 3. 소스 → 대상 매핑 원칙

1. SOURCE(`../autotrade`)는 **읽기 전용 참고 원본**이다. SOURCE를 수정하지 말 것.
2. 검증된 공통 골격은 **이식(port)**하되 선물에 맞게 변형한다. 주식 전용 로직을 그대로 복붙하지 말고, 선물 의미에 맞게 바꾼다(양방향 진입, 증거금, 레버리지, 만기/롤오버, 계약 multiplier 등).
3. SOURCE에 이미 있는 **선물 자산은 최우선으로 재사용**한다(아래 §3.1). 새로 짜기 전에 SOURCE에서 먼저 찾는다.
4. `autotradesun`은 빈 저장소이므로, 이식 시 디렉토리 구조·CI·안전 플래그·테스트 관례를 SOURCE와 동일하게 맞춘다.

### 3.1 SOURCE에서 반드시 먼저 확인·재사용할 선물 자산
다음 파일/문서가 SOURCE에 있으면 그대로 이식 후 확장한다(없으면 본 지시문 기준으로 신규 작성):
- `backend/app/brokers/futures_base.py` — `FuturesBrokerAdapter`, `FuturesOrder`, `FuturesContractSpec`, `FuturesMarginSnapshot`, `MockFuturesBroker`
- `backend/app/futures/strategies/base.py` — `FuturesStrategyBase`, `FuturesSignalAction`, mock 전략 3종(Trend/VolatilityBreakout/Hedge)
- `backend/app/futures/margin_rules.py` — `LeverageLimitRule`, `FuturesMarginRule`, `LiquidationRiskRule`
- `FuturesRiskManager` / `FuturesRiskPolicy`, `FuturesSimulationEngine`, `FuturesOrderAuditLog`
- 프론트엔드 `Futures` 탭, `FuturesMarginRiskCard`, `FuturesOrderAuditCard`, `FuturesDisabledNotice`, `frontend/src/config/features.js`
- 문서: `docs/futures_scope.md`, `docs/futures_broker_contract.md`, `docs/futures_strategy_contract.md`, `docs/futures_margin_risk.md`, `docs/futures_ui.md`, `docs/futures_promotion_policy.md`

---

## 4. 불변 안전 원칙 (SOURCE의 CLAUDE.md를 그대로 계승)

1. **AI가 broker 주문 API를 직접 호출하는 코드를 만들지 않는다.**
2. **모든 주문은 단일 진입점을 통과한다.** 선물도 `route_order` 동등물(`futures order_router`) → `FuturesRiskManager` → `OrderExecutor`(선물용 단일 실행자) 순서를 강제한다. 새 주문 경로를 만들면 반드시 이 단일 진입점을 통과시킨다.
3. **기본 운용모드는 `SIMULATION` 또는 `PAPER`.** `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 기본 `false`.
4. **API Key / App Secret / 계좌번호 / Anthropic·OpenAI Key는 frontend에 저장·커밋 금지.** backend `.env` 또는 환경변수로만 주입. `.gitignore` 강제.
5. **advisory 모듈(전략 리서처/리스크 감독/리포트/관측 Agent 등)은 broker / OrderExecutor / order_router / 외부 HTTP / AI SDK를 import 하지 않는다**(정적 grep 가드 테스트로 잠금). 모든 advisory 산출물은 `is_order_signal=False` / `auto_apply_allowed=False` / `is_live_authorization=False` 불변.
6. 위 원칙은 **테스트로 강제**한다(정적 grep 가드 포함). SOURCE의 동일 테스트 패턴을 이식한다.

---

## 5. 기술 스택 · 디렉토리 구조 (SOURCE와 동일하게)

- Backend: FastAPI + SQLAlchemy + Alembic (`backend/`)
- Frontend: React + Vite PWA (`frontend/`)
- Desktop(선택): Tauri v2 (`src-tauri/`) — SOURCE에 있으면 이식, 없으면 생략
- CI: `.github/workflows/backend-ci.yml`(ruff + pytest), `frontend-ci.yml`(eslint + vitest + build)
- 안전 스캐너: `scripts/security_scan.py` 이식 — HIGH/MEDIUM/LOW/INFO 모두 0 findings 여야 머지 가능

```
autotradesun/
├─ frontend/        # React/Vite 관제 UI (Futures 중심)
├─ backend/         # FastAPI 엔진 (futures, risk, execution, brokers, market, agents)
├─ docs/            # 선물 운용/리스크/승격 정책 문서
├─ scripts/         # 백테스트/스모크/보안스캔 등
├─ .github/         # CI workflow
├─ CLAUDE.md        # 본 지시문 기반 작업 지침(선물판)
└─ README.md
```

---

## 6. Phase별 작업 (순서대로, 멈추지 말 것)

### Phase 0 — 부트스트랩
- `autotradesun`에 SOURCE의 공통 골격 이식: backend 앱 구조, settings/안전 플래그, DB/Alembic, 단일 주문 진입점 패턴, frontend 골격, CI, `security_scan.py`, `.gitignore`, `.env.example`.
- 주식 전용 전략/유니버스 등 선물과 무관한 대용량 모듈은 가져오지 않는다(필요한 공통 유틸만).
- `CLAUDE.md`(선물판) 작성: §2~§4를 선물 기준으로 명시.
- 산출: 빈 저장소가 빌드·테스트가 도는 최소 골격이 됨. `✅ Phase 0 완료`.

### Phase 1 — 국내선물 계약 레지스트리 + 만기 캘린더
- `backend/app/futures/contracts/domestic_registry.py`: KOSPI200 선물, **미니 KOSPI200**, **마이크로 KOSPI200**(존재 상품 기준), 코스닥150 선물의 `FuturesContractSpec`(code/underlying/multiplier/tick_size/tick_value_krw/leverage_max/currency/market_hours).
- 만기 규칙(분기물 + 월물), `days_to_expiry`, `is_contract_expiring_soon`, `should_rollover` — **모두 advisory bool/int만 반환, 자동 주문 트리거 0건**.
- 테스트: 계약 스펙 유효성, 만기 계산, 롤오버 권고 경계값.
- `✅ Phase 1 완료`.

### Phase 2 — 선물 시세 어댑터
- `backend/app/futures/market/futures_market_data.py`: `MARKET_DATA_PROVIDER`에 따라 mock(연속 합성 시세) / CSV / yfinance fallback 제공. KIS 선물 실시세는 **placeholder**(공식 endpoint 확인 전 실제 호출 0건, httpx/requests import 0건).
- 데이터 부족/오류 시 조용히 멈추지 말고 명시적 사유 코드 반환.
- 테스트: provider 분기, 빈 데이터 fallback.
- `✅ Phase 2 완료`.

### Phase 3 — 선물 리스크/실행 단일 경로 + Paper 자동 루프 (핵심)
- `FuturesRiskManager` + `margin_rules`(레버리지/증거금/청산거리) 이식·확장. LIVE `evaluate_order`는 **항상 REJECTED** 유지.
- 선물 단일 주문 진입점 `backend/app/futures/execution/futures_order_router.py` + 선물 `OrderExecutor`: 시세/잔고/포지션 조회 → `FuturesRiskManager` 평가 → `FuturesOrderAuditLog` 기록 → 분기(REJECTED / NEEDS_APPROVAL / 가상 체결).
- **자동 루프** `backend/app/futures/auto_loop.py` (`FuturesAutoPaperEngine`): start/stop/status, tick마다 전략→리스크→가상체결→감사로그→포지션 갱신. `SIMULATION`/`PAPER` 전용. SOURCE의 주식 auto-paper 루프 구조를 그대로 본떠 안전 invariant 동일.
- 엔드포인트: `POST /api/futures/auto/start`, `POST /api/futures/auto/stop`, `GET /api/futures/auto/status`, `GET /api/futures/positions`, `GET /api/futures/audit`.
- 테스트: 루프 1 사이클 e2e(가상 체결 발생, broker 실호출 0건), start/stop 멱등성, LIVE 차단.
- `✅ Phase 3 완료`.

### Phase 4 — 선물 전략 + Council(advisory)
- mock 전략 3종 이식·확장 + 선물 신호 통합기/Council(가중 투표 → 장세 → confidence/quality → 리스크 veto → exit_plan → 보유청산). `is_order_intent=False` 불변.
- 양방향(롱/숏) 명시, 만기≤임계 시 신규진입 강등 + 롤오버 plan carry.
- 테스트: Council 게이트, 양방향 신호, 만기 강등.
- `✅ Phase 4 완료`.

### Phase 5 — 선물 백테스트 / Walk-forward / Stress (Paper 검증)
- 표준 메트릭(승률/손익비/PF/MDD/연속손실/expectancy) 재사용. 선물 손익은 multiplier·tick_value 반영, 숏 진입 손익 정상 계산.
- CLI: `scripts/run_futures_backtest.py`, `scripts/run_futures_walk_forward.py`, `scripts/run_futures_stress_test.py`. 산출물 `reports/*` gitignore.
- 모든 리포트 객체 `is_order_signal=False`/`is_live_authorization=False`/`auto_apply_allowed=False` 불변.
- 테스트: 모듈 + CLI subprocess.
- `✅ Phase 5 완료`.

### Phase 6 — 프론트엔드 Futures 탭 + "시작 버튼" (핵심)
- `SIMULATION`/`PAPER`에서 **사용 가능한** Futures 대시보드: 포지션, 증거금/레버리지/청산거리(`FuturesMarginRiskCard`), 감사로그(`FuturesOrderAuditCard`), 손익, 만기/롤오버 권고.
- **"국내선물 모의 자동매매 시작 / 중지" 버튼**을 `/api/futures/auto/start|stop|status`에 연결. 시작하면 상태가 RUNNING으로 바뀌고 대시보드가 실시간 갱신.
- 백엔드 미기동 시 빈 화면이 아니라 안내 배너(SOURCE 패턴 이식). ErrorBoundary 격리.
- LIVE/실거래 버튼은 `disabled` 고정. "실거래 시작" 등 활성 enabling 라벨 0개(테스트 lock).
- 테스트(vitest): 시작/중지 버튼 동작, RUNNING 상태 렌더, LIVE 버튼 disabled invariant.
- `✅ Phase 6 완료`.

### Phase 7 — 안전 게이트 회귀 + 승격 정책 이식
- 선물 LIVE 다단계 게이트 문서/스텁 이식(`docs/futures_promotion_policy.md` 등) — 본 빌드에서 활성화 0건.
- 선물용 premarket check / preflight smoke에 선물 항목 추가.
- 정적 grep 가드 테스트: advisory 모듈 broker/HTTP/AI SDK import 0건, 안전 플래그 기본값 검증.
- `✅ Phase 7 완료`.

### Phase 8 — 통합 스모크 + 문서 + README
- `scripts/futures_smoke_test.py`: health/안전 플래그/DB/auto loop/계약 레지스트리 1회 점검(주문 0건, read-only).
- `README.md`(선물판): 실행법, 시작 버튼 사용법, 안전 고지, 확인 주소.
- `docs/` 인덱스 정리.
- `✅ Phase 8 완료`.

### Phase 9 — 전체 검증 (DoD §7) + push
- §7 전체 통과 확인 → §9 커밋/푸시 → §11 결과 보고.
- `✅ Phase 9 완료`.

---

## 7. 완료 기준 (Definition of Done) — 전부 PASS 해야 함

1. 깨끗한 클론에서 다음이 동작:
   ```bash
   # backend
   cd backend && python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   uvicorn app.main:app --reload          # :8000
   # frontend
   cd frontend && npm ci && npm run dev    # :5173
   ```
2. 브라우저에서 Futures 탭 → **"국내선물 모의 자동매매 시작"** 클릭 → 상태 RUNNING + 가상 체결/포지션/마진/감사로그가 실제로 갱신됨. **실제 broker 호출 0건.**
3. `DEFAULT_MODE=SIMULATION` 기본값에서 위 2번이 추가 설정 없이 즉시 작동.
4. CI green: `cd backend && ruff check app tests && pytest -q` / `cd frontend && npm run lint && npm test && npm run build` 모두 통과.
5. `python scripts/security_scan.py` → HIGH/MEDIUM/LOW/INFO 모두 0 findings.
6. `python scripts/futures_smoke_test.py` → PASS(또는 장 닫힘은 WARN).
7. 안전 invariant 확인: `ENABLE_FUTURES_LIVE_TRADING=false` 기본, LIVE `evaluate_order` 항상 REJECTED, advisory 모듈 broker/HTTP/AI SDK import 0건, secret/계좌번호 커밋 0건.

---

## 8. 자가검증 (각 Phase 종료 시 자동 실행)
```bash
cd backend && ruff check app tests && pytest -q
cd ../frontend && npm run lint && npm test && npm run build
cd .. && python scripts/security_scan.py
```
하나라도 실패하면 그 자리에서 고치고 재실행. 통과 전에는 다음 Phase로 가지 않는다.

---

## 9. Git 워크플로우 · 커밋/푸시 규칙
- 작업 브랜치: `feature/futures-domestic-v1` 생성 후 작업.
- Phase 단위 커밋 메시지: `feat(futures): Phase N - <요약>` / 수정은 `fix(futures): ...` / 문서는 `docs(futures): ...`.
- 빈 저장소 초기화가 필요하면 `main` 브랜치에 첫 커밋 후 작업 브랜치 분기.
- 마지막에:
  ```bash
  git add -A
  git commit -m "feat(futures): domestic futures paper auto-trading v1"
  git push -u origin feature/futures-domestic-v1   # 또는 main
  ```
- push 인증 실패 시: 변경은 모두 로컬 커밋으로 남기고, 실행할 push 명령을 §11 보고에 명시.
- `.env`, secret, 계좌번호, API Key는 어떤 브랜치에도 커밋 금지(`.gitignore` 강제).

---

## 10. 절대 금지 사항
1. 사람에게 중간 확인/질문하느라 멈추기(막혀서 불가능한 경우 제외).
2. `ENABLE_FUTURES_LIVE_TRADING` 등 안전 플래그를 `true`로 바꾸기.
3. 선물 LIVE 실거래 경로/실제 KIS 선물 주문 호출 코드 작성·활성화.
4. advisory 모듈에서 broker/OrderExecutor/order_router/외부 HTTP/AI SDK import.
5. 단일 주문 진입점 우회하는 새 주문 경로.
6. secret/계좌번호/API Key를 코드·테스트·문서·.env 예시에 실값으로 넣기.
7. 테스트/린트/빌드/보안스캔 우회(`--no-verify`, force-push, skip).
8. "수익 보장", "자동 실전 전환 승인" 류 문구를 코드·문서에 넣기.

---

## 11. 최종 결과 보고 형식 (작업 끝난 뒤 이 형식으로만 출력)

```
## 국내선물(autotradesun) 1차 빌드 결과

### 한 줄 요약
- (시작 버튼으로 모의 자동매매가 실제 동작하는지 PASS/FAIL)

### 완료한 Phase
- Phase 0 ~ 9: (각 1줄)

### 시작 버튼 사용법
- backend/frontend 실행 명령
- Futures 탭 → "국내선물 모의 자동매매 시작" → 확인 포인트

### 검증 결과
- ruff: / pytest: / eslint: / vitest: / build: / security_scan: / smoke:
- 안전 invariant: (LIVE flag, REJECTED, import 가드, secret 0건)

### 스스로 내린 결정 (모호했던 선택들)
- (한 줄씩)

### 남은 항목 / 후속 PR 후보
- (선물 LIVE 게이트 등 의도적으로 비활성으로 둔 것)

### Git
- 브랜치 / 커밋 수 / push 결과(또는 실행할 push 명령)
```

---

**요약:** 이 문서는 `autotrade`(주식)의 안전 아키텍처를 토대로 `autotradesun`(국내선물)을 **모의/Paper 자동매매가 시작 버튼 하나로 끝까지 도는 상태**까지 자동 완성한다. 선물 LIVE 실거래는 기존 다단계 게이트 뒤에 그대로 둔다. Claude Code는 중간에 멈추지 말고 §6 Phase 0~9를 끝까지 수행한 뒤 §11 형식으로만 보고한다.
