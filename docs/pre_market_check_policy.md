# Pre-market Check Policy — 체크리스트 #80

> 장 시작 전 자동 점검. API / DB / Broker / Data / Watchlist / Strategy /
> Risk / KillSwitch / Agent / Notification / Governance Gates 상태를
> read-only 로 점검 후, 모드별 required FAIL 이 하나라도 있으면
> `start_allowed=False` 반환.

---

## 1. 목적

- 장중 사고를 *시작 전*에 막는다.
- 자동매매 시작 전 11개 카테고리 (API / DB / Broker / Data / Watchlist /
  Strategy / Risk / KillSwitch / Agent / Notification / Governance) 의
  상태를 *한 번에* 점검.
- 운영자가 *놓칠 수 있는 미설정 / 비정상 상태*를 명시 surface.
- 본 게이트는 *자동매매를 실행하지 않는다* — 결과만 반환.

---

## 2. 점검 항목 (11 카테고리)

| 카테고리 | 항목 |
|---|---|
| `api`          | API 서버 응답 (api_reachable) |
| `db`           | DB ping (db_reachable) |
| `broker`       | broker 준비 (broker_paper / broker_live_readonly) + kis_is_paper / credentials |
| `data`         | freshness (data_freshness_ok / stale_symbol_count) |
| `watchlist`    | Watchlist 종목 수 |
| `strategy`     | 활성 전략 수 |
| `risk`         | risk_policy / position_limits / daily_loss_limit |
| `kill_switch`  | emergency_stop + level |
| `agent`        | ai_permission_gate / ai_execution_flag / live_trading_flag / futures_live_flag |
| `notification` | Notification 설정 (optional) |
| `governance`   | Paper Gate / Live Manual Gate / AI Assist Gate / AI Execution Gate 결과 carry |

---

## 3. Mode 별 required checks

| Check | SIM | PAPER | LIVE_SHADOW | LIVE_MANUAL | LIVE_AI_ASSIST | LIVE_AI_EXEC |
|---|---|---|---|---|---|---|
| api               | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| db                | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| watchlist         | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| risk_policy       | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| kill_switch       | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| broker_paper      | — | ✓ | — | — | — | — |
| broker_live_readonly | — | — | ✓ | ✓ | ✓ | ✓ |
| data_freshness    | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| daily_loss_limit  | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| paper_gate        | — | — | — | ✓ | ✓ | ✓ |
| live_manual_gate  | — | — | — | ✓ | ✓ | ✓ |
| ai_permission_gate | — | — | — | — | ✓ | ✓ |
| ai_assist_gate    | — | — | — | — | ✓ | ✓ |
| ai_execution_gate | — | — | — | — | — | ✓ |
| live_trading_flag | — | — | — | ✓ | ✓ | ✓ |
| ai_execution_flag | — | — | — | — | — | ✓ |
| notification      | opt | opt | opt | opt | opt | opt |

옵셔널(`notification` / `strategy` SIM) 항목은 WARN 으로만 surface.

`futures_live_flag` 는 *모든 모드*에서 `true` 면 즉시 BLOCK (선물 LIVE는
본 게이트 미허용 — #76 / #75 와 일관).

---

## 4. `start_allowed` 정책

| 결과 | start_allowed | verdict |
|---|---|---|
| required FAIL 1건 이상 | **False** | DO_NOT_START |
| required PASS but WARN 있음 | True | WARN_BUT_START_ALLOWED |
| 모두 PASS | True | READY_TO_START |

### Strict 모드
`strict=True` 일 때 required UNKNOWN(데이터 입력 없음)도 FAIL 로 취급.
운영자가 "데이터가 없어 보수적으로 차단" 을 원할 때 사용.

### 정책 invariant
- required FAIL 이 있으면 `start_allowed=False` *영구*.
- `manual_ack=True` 라도 required FAIL 우회 불가.
- 본 게이트는 `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / mode 를
  *변경하지 않는다*.

---

## 5. 수동 확인 ("확인했습니다") 정책

- UI 의 "확인했습니다" 버튼은 *UI 상태 표시*만.
- `manual_ack=True` + `manual_ack_by` / `manual_ack_note` 가 응답에 carry
  되지만, **required FAIL 우회 불가**.
- ack 가 기록되어도 `start_allowed=False` 인 경우 BotControl 탭의 시작
  버튼은 *비활성*이어야 한다 (별도 흐름).
- 본 ack 는 단순 *operator awareness* 표시 — RiskManager / PermissionGate /
  OrderExecutor 결정을 변경하지 않는다.

---

## 6. CLI 사용법

```bash
# SIMULATION dry-run (default).
python scripts/pre_market_check.py --mode SIMULATION --format markdown

# PAPER + 운영자 입력.
python scripts/pre_market_check.py --mode PAPER \
  --broker-ready --kis-is-paper --kis-credentials-present \
  --data-freshness-ok --watchlist 5 --strategies 2 \
  --daily-loss-limit-configured \
  --format json

# LIVE_MANUAL_APPROVAL + manual ack + strict.
python scripts/pre_market_check.py --mode LIVE_MANUAL_APPROVAL \
  --broker-ready --kis-credentials-present \
  --no-kis-is-paper \
  --data-freshness-ok --watchlist 5 --strategies 1 \
  --enable-live-trading \
  --paper-gate-pass 1 --live-manual-gate-pass 1 \
  --strict --manual-ack --manual-ack-by "operator"
```

CLI exit code:
- `0` : READY_TO_START / WARN_BUT_START_ALLOWED (`start_allowed=True`)
- `1` : DO_NOT_START (`start_allowed=False`)
- `2` : 실행 오류 (import 실패 등)

**Secret / `.env` 전체 출력 0건** — CLI는 입력으로 받은 값만 출력.

---

## 7. API 사용법

```http
GET /api/governance/pre-market-check?mode=PAPER&strict=true

POST /api/governance/pre-market-check
Content-Type: application/json

{
  "mode": "PAPER",
  "broker_ready": true,
  "kis_is_paper": true,
  "kis_credentials_present": true,
  "data_freshness_ok": true,
  "watchlist_item_count": 5,
  "active_strategy_count": 2,
  "daily_loss_limit_configured": true
}
```

응답 invariant:
- `is_order_signal=false`
- `live_flag_changed=false`
- `mode_changed=false`

---

## 8. UI

`frontend/src/components/tabs/PreMarketCheckCard.jsx`:

- 모바일 헤드라인 (큰 배너):
  - "오늘 자동운용 가능" (READY)
  - "주의 필요 — 운영자 검토 후 시작" (WARN)
  - "시작 금지 — required FAIL 해결 필요" (DO_NOT_START)
- `start_allowed = true/false` 명시 표시.
- 실패 / 경고 / 필요 조치 + 세부 항목 (펼치기).
- 버튼: "다시 점검" / "확인했습니다" — *오직 이 둘만*.
- **자동매매 시작 / mode 변경 / flag 토글 / Place Order 라벨 버튼 0개**
  (테스트로 lock).
- BUY/SELL/HOLD/긴급정지 토글 문구 0건.
- Secret 패턴 0건.
- disclaimer *항상* 노출: "본 카드는 *자동매매 시작 전 안전 점검*입니다.
  주문 / 모드 / 안전 플래그를 변경하지 않습니다. '확인했습니다' 버튼은 UI
  상태 기록일 뿐이며, **required FAIL 을 우회하지 않습니다.**"

---

## 9. 절대 원칙 — 본 모듈 강제

`tests/test_pre_market_check.py`의 정적 grep 가드:

1. broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` /
   `app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` import 0건.
2. `broker.place_order(` / `route_order(` / `OrderExecutor(` /
   `submit_candidate(` / `AiClient(` 호출 0건.
3. `from app.core.config import` / `get_settings(` 호출 0건 (evaluator는 입력 DTO만).
4. `settings.enable_*_trading =` / `os.environ["ENABLE_*"] =` mutate 0건.
5. DB write (INSERT/UPDATE/DELETE/.add/.commit/.flush) 0건.
6. `PreMarketCheckResult.is_order_signal=True` 생성 불가 (ValueError).
7. `PreMarketCheckResult.live_flag_changed=True` 생성 불가.
8. `PreMarketCheckResult.mode_changed=True` 생성 불가.
9. UI 카드 "자동매매 시작" / "지금 시작" / "Start Bot" / "Start Trading" /
   "mode 변경" / "활성화 토글" / "ENABLE_*" / "Place Order" / "실거래 활성화"
   라벨 버튼 0개.
10. CLI 가 외부 API / `.env` / `load_dotenv` import 0건.

---

## 10. 실거래 시작 분리

본 점검 결과는 *권고*이며, **실제 자동매매 시작은 BotControl 탭의 별도
흐름**이다. BotControl 측 코드 또는 운영자가:

1. 본 점검 결과의 `start_allowed` 를 확인.
2. False 이면 시작 버튼 비활성.
3. True + WARN 이면 운영자가 명시 확인 후 시작.
4. True + READY 이면 시작 가능.

본 정책은 BotControl 코드 변경 없이 *권고 표시*만 제공한다 — 운영자 실수
방지가 1차 목적.

---

## 11. 후속 backlog

- **자동 collector** — `/api/status` + `/api/monitoring/health` + `/api/risk/policy`
  결과를 자동으로 PreMarketCheckInput 으로 매핑 (현재는 운영자 / Bot 측이 입력).
- **시간 윈도우 검증** — 장 시작 *직전* (예: 09:00~09:30 KST) 자동 실행 cron.
- **결과 영구화** — PreMarketCheckLog 테이블 (현재는 ephemeral).
- **Notification 연계** — DO_NOT_START 시 자동 알림.
- **BotControl 통합** — 시작 버튼이 본 게이트 결과를 *직접* 참조하도록.
- **Strategy Researcher 연계** — 반복 FAIL 패턴을 학습 자료로.

---

## 12. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙
- [`docs/monitoring_policy.md`](monitoring_policy.md) — #70 시스템 안정성
- [`docs/risk_manager_contract.md`](risk_manager_contract.md) — #34 RiskManager 단일 진입점
- [`docs/emergency_stop_policy.md`](emergency_stop_policy.md) — #37 3-Level Kill Switch
- [`docs/order_guard_policy.md`](order_guard_policy.md) — #38 OrderGuard
- [`docs/ai_permission_gate.md`](ai_permission_gate.md) — #39 AI Permission Gate
- [`docs/data_freshness_policy.md`](data_freshness_policy.md) — #20 Data freshness
- [`docs/paper_gate_policy.md`](paper_gate_policy.md) — #72 Paper Gate
- [`docs/live_manual_gate.md`](live_manual_gate.md) — #73 Live Manual Gate
- [`docs/ai_assist_gate.md`](ai_assist_gate.md) — #74 AI Assist Gate
- [`docs/ai_execution_gate.md`](ai_execution_gate.md) — #75 AI Execution Activation Gate
- [`docs/futures_promotion_policy.md`](futures_promotion_policy.md) — #76 선물 정책
- [`docs/notification_policy.md`](notification_policy.md) — #64 Notification
