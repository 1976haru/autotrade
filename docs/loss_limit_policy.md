# Loss Limit Policy (#36)

> 코드: [`backend/app/risk/loss_limits.py`](../backend/app/risk/loss_limits.py)
> 헬퍼: [`backend/app/risk/daily_pnl.py`](../backend/app/risk/daily_pnl.py) (`compute_weekly_realized_pnl_kst`, `count_consecutive_losing_trades`, `week_start_kst`)
> RiskManager 연계: [`backend/app/risk/risk_manager.py`](../backend/app/risk/risk_manager.py)
> 테스트: [`backend/tests/test_loss_limits.py`](../backend/tests/test_loss_limits.py)

## 1. 목적

> **감정적 복구매매와 봇 폭주를 방지한다.**

손실이 누적되는 패턴 — 일일 큰 손실, 주간 누적 손실, 연속 손실 — 에서 봇이
"복구하려고" 무리한 신규 BUY를 하지 않도록 자동 차단. 손실한도 도달 시
신규 BUY는 막되 SELL/EXIT은 허용 (리스크 축소 보호).

## 2. 일일 손실한도 (DailyLossLimitRule)

3단계 임계 (모두 옵트인):

| 단계 | 임계 | 결정 | 효과 |
|---|---|---|---|
| WARN | `daily_loss_warn_pct` (e.g., 50%) | `WARN` | warnings에 기록, 운영자 인지 |
| REDUCE_SIZE | `daily_loss_reduce_pct` (e.g., 70%) | `REDUCE_SIZE` | warnings에 사이즈 축소 권고 |
| BLOCK_NEW_BUY | 100% (= `max_daily_loss`) | `BLOCK_NEW_BUY` | 신규 BUY REJECTED |

기존 `RiskManager.evaluate_order`의 `"daily loss limit reached"` hard reject는
그대로 유지 — 본 rule은 *그 위에 soft 단계를 추가*.

## 3. 주간 손실한도 (WeeklyLossLimitRule)

`weekly_loss_limit` (양수)로 설정. 주간 기준:
- **시작**: 월요일 00:00 KST.
- **종료**: 일요일 23:59:59 KST (다음 주 월요일 00:00 직전).

같은 단계 — WARN/REDUCE_SIZE/BLOCK_NEW_BUY (`weekly_loss_warn_pct` /
`weekly_loss_reduce_pct`).

목적: 매일 한도 미만이지만 주간으로 보면 큰 손실 누적인 케이스(감정적
복구매매 패턴)를 잡는다 → 자동운용 pause 권고.

## 4. 연속 손실 중단 (ConsecutiveLossRule)

`consecutive_loss_limit` (양수)로 설정. trailing N건의 *closed* trade가 모두
손실(realized PnL < 0)이면 BLOCK_NEW_BUY.

- soft 단계 의도적으로 도입하지 않음 — 연속 손실은 정성적 신호라 명확히
  멈추는 게 안전.
- closed trade는 SELL이 BUY와 매칭(FIFO)된 경우만 카운트 — naked SELL은 무시.
- 익절(realized PnL >= 0) 등장 시 카운트 리셋.

`Agent / RiskOfficerAgent` 연계: 연속 손실 패턴은 RiskOfficer가 최우선으로
운영자에게 surface해야 한다.

## 5. BUY와 SELL 차이

**핵심 원칙: SELL/EXIT은 한도 초과여도 통과**.

| 한도 도달 | BUY | SELL |
|---|---|---|
| `daily_loss_limit` ≥ 100% | REJECTED | APPROVED + warnings |
| `weekly_loss_limit` ≥ 100% | REJECTED | APPROVED + warnings |
| `consecutive_loss_limit` 도달 | REJECTED | APPROVED + warnings |
| WARN/REDUCE_SIZE 단계 | warnings | warnings |

리스크 축소 주문(SELL/EXIT)을 막으면 손실이 더 커지는 역효과 — CLAUDE.md
'손실 방어 우선' 원칙. 운영자/Agent는 warnings를 보고 인지 가능.

## 6. 실시간 손익 계산 주의

### 6.1 realized vs unrealized
- **본 rule은 realized PnL만 사용**. unrealized(평가손익)는 시장 변동에 따라
  매 tick 변하고 stale price 위험이 있어 신뢰도 낮음.
- realized PnL = 청산된 거래의 실제 손익. `compute_today_realized_pnl` /
  `compute_weekly_realized_pnl_kst`이 OrderAuditLog 기반 FIFO 매칭으로 산출.

### 6.2 stale price 주의
- `RiskPolicy.stale_price_max_age_seconds` (기본 60초) — broker quote이
  오래되면 RiskManager가 hard-reject (#143).
- 본 rule은 broker quote을 직접 사용하지 않으나, 호출자(route_order)가 stale
  reject을 우선 처리 → 본 rule은 stale 데이터로 손실 추정하지 않는다.

### 6.3 virtual / paper / live 분리
- audit row의 `mode` 컬럼이 SIMULATION / PAPER / LIVE_*를 구분.
- 본 rule은 *기본적으로 모든 mode 통합 집계* — 운영자가 mode별 분리가 필요하면
  outer query에서 mode 필터 후 helper 호출.
- VIRTUAL_AI_EXECUTION에서의 가상 PnL이 LIVE PnL과 섞이면 안 되므로 운영
  단계에서 mode 분리 권장 (backlog).

### 6.4 timezone (KST)
- 일일 경계 = KST date. KST 자정(=15:00 UTC, 장 종료 후) 리셋.
- 주간 경계 = 월요일 00:00 KST (`week_start_kst`).
- UTC 기준이 아닌 이유: 한국 시장 운영자 직관과 일치 (장 시작/종료 기준).

### 6.5 수수료 / 세금 미반영
- 본 rule의 PnL 계산은 `(sell_price - buy_price) × qty` — 수수료/세금
  제외.
- 실제 LIVE에서는 broker statement과 reconciliation 필수 (#212 참고).
- 향후 backtest cost model(#23) 동일 방식으로 수수료/세금 반영 옵트인 가능.

## 7. Agent와의 관계

- **`RiskOfficerAgent`** (Agent Council #185) — 손실한도 상태를 *최우선*
  참고. WARN/REDUCE_SIZE/BLOCK_NEW_BUY를 자기 결정에 carry.
- **AI 추천이 있어도 손실한도 초과 시 차단** — `RiskManager.evaluate_order`
  가 BUY를 REJECT하므로 AI confidence 100%여도 broker로 가지 않음.
- **`PostTradeReviewAgent`** — 연속 손실 트리거 후 사후 분석에서 손실 원인
  학습. 본 PR에는 자동 통합 없음 (backlog).

## 8. 실제 LIVE 전 확인사항

LIVE_AI_EXECUTION 활성화 전에 다음 검증 필수:

- [ ] **broker PnL과 내부 PnL reconciliation** — `compute_today_realized_pnl`
      결과와 broker statement (예: KIS 일일 결제 내역) 일치 검증.
- [ ] **수수료 / 세금 반영** — 한국 거래세 0.23% (코스피) / 0.18% (코스닥) +
      증권사 수수료 약 0.015%.
- [ ] **장중 업데이트 주기 검증** — `route_order`이 매 평가 직전
      `compute_today_realized_pnl`을 호출하므로 freshness 보장. 단,
      대량 주문 동시 발생 시 audit insert 순서와 PnL 산출 순서 일관성 검증.
- [ ] **timezone 확인** — broker가 UTC / KST 중 어느 기준으로 fill timestamp
      를 보내는지 audit row 저장 형식 확인.
- [ ] **VIRTUAL_AI_EXECUTION의 가상 PnL이 LIVE 카운터에 섞이지 않는지** —
      mode 필터 적용 검토.

## 9. 향후 과제 (Loss Limit backlog)

- **REDUCE_SIZE의 실제 사이즈 축소** — 현재는 warnings로만 surface.
  RiskCheckResult에 `normalized_order` 채우거나 PositionSizingAgent 자동
  통합. (#34 RiskDecision.REDUCED와 같은 방향).
- **mode 분리 PnL** — VIRTUAL/PAPER/LIVE 별 카운터 분리.
- **수수료/세금 반영** — 본 rule + scoreboard 모두 cost model 통합.
- **broker statement reconciliation** — #212 reconciliation과 같이 일일
  PnL drift 감지.
- **RiskOfficerAgent 자동 통합** — Agent가 본 rule 결과를 자기 결정에
  최우선 carry.
- **per-strategy 손실한도** — 전략별 독립 limit (현재는 계좌 전체).

## 10. 안전 invariant

- broker / RiskManager (자체) / PermissionGate / OrderExecutor / route_order
  어떤 함수도 본 rule이 직접 호출하지 않음 (read-only — 테스트 가드).
- DB write 0건 — 본 rule + 헬퍼 모두 SELECT만.
- 기존 RiskCheckResult 응답 호환성 유지 — `result.decision` / `result.reasons`
  / `result.passed` / `result.warnings` 모두 기존 필드 그대로 사용.
- 기존 `max_daily_loss` hard reject 동작 그대로 (테스트로 가드).
- LIVE flag / API Key / Secret / 계좌번호 변경 0건.
