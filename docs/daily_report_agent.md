# Daily Report Agent (#57)

본 문서는 [`DailyReportAgent`](../backend/app/agents/daily_report_agent.py)의 정책 contract를 정의한다. 장 종료 후 OrderAuditLog / VirtualOrder / FuturesOrderAuditLog / AgentDecisionLog / EmergencyStopEvent / PendingApproval / BacktestRun을 read-only로 분석해 `reports/daily_YYYY-MM-DD.md`를 생성하는 advisory Agent.

## ⚠ 본 리포트는 *투자 조언이 아닙니다*.

본 Agent의 출력은 **자동매매 시스템 운영·검증·개선 자료**입니다 — 종목 추천 / 매수 매도 신호 / 투자 권유가 *아닙니다*. 실제 투자 판단은 사용자 책임이며, 실거래 전 별도 검증(별도 PR / 별도 백테스트 / paper / shadow)이 필요합니다.

## 1. 목적

매일 *개선 루프*를 생성:
- 오늘 운영 데이터를 운영자 친화적 markdown으로 요약
- 손실 / 위험 패턴을 카테고리별로 분류 (data_stale, AI overconfidence, emergency stop 등)
- 내일 주의점 / Action Items / 개선 후보를 *advisory*로 제시
- 모든 변경은 운영자 검토 → 별도 PR → 별도 검증 절차 필요 (자동 적용 X)

단일 Agent가 "분석 + 적용"하지 못하도록 본 Agent는 *순수 분석 + markdown 생성* 계층에 머문다.

## 2. 데이터 소스 (모두 read-only SELECT)

| 모델 | 사용 컬럼 | 용도 |
|---|---|---|
| `OrderAuditLog` (#13) | created_at, decision, reasons, symbol, side, quantity, requested_by_ai, signal_confidence, strategy, archived | 주문 통계, 거부 사유, AI 신뢰도, 전략별 성과 |
| `VirtualOrder` (#193) | status, side, symbol, quantity, filled_quantity, avg_fill_price, filled_at, strategy | 가상 주문 카운트, 체결 통계 |
| `FuturesOrderAuditLog` (#194) | decision, contract, side, forced_liquidation, reasons | 선물 audit, 강제 청산 카운트 |
| `AgentDecisionLog` (#187+) | agent_name, decision, confidence, reasons | Agent별 결정 통계, 평균 confidence |
| `EmergencyStopEvent` (#37, #46) | enabled, reason_code, level | Kill Switch 토글 카운트, 사유 |
| `PendingApproval` (#41) | status, attempts | 승인 큐 상태, 재검증 실패 카운트 |
| `BacktestRun` (#23+) | id, strategy, total_pnl | 오늘 실행된 백테스트 목록 |

### DB read-only helpers
```python
load_audit_rows_for_date(db, report_date) -> list[OrderAuditLog]
load_virtual_orders_for_date(db, report_date) -> list[VirtualOrder]
load_futures_audit_for_date(db, report_date) -> list[FuturesOrderAuditLog]
load_agent_decisions_for_date(db, report_date) -> list[AgentDecisionLog]
load_emergency_events_for_date(db, report_date) -> list[EmergencyStopEvent]
load_pending_approvals_for_date(db, report_date) -> list[PendingApproval]
load_backtest_runs_for_date(db, report_date) -> list[BacktestRun]
```
모두 read-only SELECT, KST 일자 → UTC datetime 범위 변환. INSERT / UPDATE / DELETE 0건 (정적 grep 가드).

## 3. 리포트 내용 (markdown 12개 섹션)

`reports/daily_YYYY-MM-DD.md` 구조:

1. **중요 고지** — 투자 조언 아님 / 시스템 개선 자료 disclaimer
2. **오늘 요약** — 운용 모드 / 주문 수 / 승인·거부·승인필요 / 가상·선물 카운트 / 승률·기대값·PF (가상 추정)
3. **손익 요약** — realized / unrealized / total / 가상 PnL 추정 (broker realized와 다를 수 있음 disclaimer)
4. **시간대별 성과** — UTC hour별 PnL 표 + 장초반/장마감 advisory
5. **전략별 성과** — strategy 별 주문 수 / 승인 / 거부
6. **Agent 판단 요약** — Agent별 결정 수 / WARN / REJECT / 평균 confidence
7. **리스크 이벤트** — risk_event_breakdown (stale / duplicate / cooldown / loss_limit / margin / liquidation / emergency_stop)
8. **승인 큐 요약** — PENDING / APPROVED / REJECTED / CANCELLED / EXPIRED / 재검증 실패
9. **손실 원인 분석 (advisory)** — `LossCauseCategory`별 finding (severity + count + summary)
10. **내일 주의점 (advisory)** — *시스템 운영* 관점만 (종목 추천 X)
11. **개선 후보 (advisory)** — 자동 적용 X, 운영자 검토 + 별도 PR 필요 명시
12. **Action Items** — 운영자 *수동* 체크리스트 (`[ ]` checkbox)
13. **부록** — 백테스트 run_id 목록 / 생성 시각

## 4. `LossCauseCategory` (15종, BUY/SELL/HOLD 0개)

| 값 | 트리거 |
|---|---|
| `data_stale` | reasons에 "stale" / "old quote" |
| `order_rejected` | 분류되지 않은 REJECTED ≥ 5건 (fallback) |
| `emergency_stop` | EmergencyStopEvent 발생 |
| `ai_overconfidence` | requested_by_ai=True + signal_confidence ≥ 80 + REJECTED ≥ 3건 |
| `ai_low_confidence` | (예약, 향후 PR) |
| `duplicate_burst` | reasons에 "duplicate" |
| `cooldown_block` | reasons에 "cooldown" |
| `loss_limit_breach` | reasons에 "daily loss" / "loss limit" |
| `margin_risk` | reasons에 "margin" |
| `liquidation_risk` | reasons에 "liquidation" 또는 forced_liquidation=True |
| `volume_liquidity` | (예약) |
| `strategy_condition` | (예약) |
| `high_volatility` | (예약) |
| `broker_error` | reasons에 "broker" + REJECTED |
| `unknown` | (fallback) |

각 finding은 `severity` (INFO/WARN/HIGH/CRITICAL) carry — 운영자가 우선순위 파악.

## 5. 안전 원칙 (절대 invariant)

| 원칙 | 가드 |
|---|---|
| **broker / OrderExecutor / route_order 호출 0건** | 정적 grep 가드 |
| **주문 생성 0건** — `OrderRequest` import / 생성 / annotation 0건 | 정적 grep 가드 |
| **approval queue 등록 0건** — `submit_candidate(` / `route_order(` 호출 0건 | 정적 grep 가드 |
| **DB write 0건** — agent 모듈은 read-only SELECT만 | 정적 grep 가드 (`db.add(` / `.commit(` / `.insert(` 등 0건) |
| **외부 AI / HTTP 호출 0건** | anthropic / openai / httpx / requests / urllib3 import 0건 |
| **자동 주문 0건** | `is_order_signal=False` / `auto_apply_allowed=False` 불변 (`__post_init__` ValueError) |
| **종목 추천 / 매수 매도 조언 금지** | `LossCauseCategory` enum에 BUY/SELL/HOLD 0개 + markdown 본문에 "매수 추천" / "매도 추천" / "지금 매수" / "지금 매도" / "추천 종목" 문구 0건 (정적 grep 가드) |
| **disclaimer 필수** | markdown에 "투자 조언이 아니라" / "시스템 운영" / "별도 검증" 문구 포함 강제 (테스트로 lock) |

## 6. 실행 방법

### CLI (권장 — 가장 안전)

```bash
# 어제 (KST) 리포트 생성
cd backend
python scripts/generate_daily_report.py --date 2026-05-09 --output-dir reports

# 가상 + 선물 audit 모두 포함 (default)
python scripts/generate_daily_report.py --date 2026-05-09 \
    --include-virtual --include-futures

# 미리보기 (파일 작성 X — stdout만)
python scripts/generate_daily_report.py --date 2026-05-09 --dry-run
```

CLI 인자:
- `--date YYYY-MM-DD` — 대상 날짜 (KST). 기본: 오늘 (UTC).
- `--output-dir reports` — 출력 디렉토리. 기본: `reports/`.
- `--include-virtual` — VirtualOrder 포함 (기본 True).
- `--include-futures` — FuturesOrderAuditLog 포함 (기본 True).
- `--format markdown` — 현재 markdown만.
- `--dry-run` — 파일 작성 X, stdout만.

### API (optional)

```
GET  /api/agents/daily-report/preview?date=YYYY-MM-DD
POST /api/agents/daily-report/generate
     body: {date, output_dir, include_virtual, include_futures}
```

- `/preview`: markdown 미리보기만 — 파일 작성 X, DB write 0건.
- `/generate`: `output_dir/daily_YYYY-MM-DD.md` 작성 — DB write 0건.

운영 환경에서 임의 path 작성을 방지하고 싶으면 본 endpoint를 비활성화하고 CLI만 사용 권고.

## 7. 저장 위치

`reports/daily_YYYY-MM-DD.md` (gitignore에 등록됨 — 운영 로그는 git에 지속 커밋하지 않음).

자세한 저장 / 보존 / 공유 정책: [`daily_report_policy.md`](daily_report_policy.md).

## 8. 한계 / 다음 단계

| 한계 | 영향 |
|---|---|
| 가상 PnL 추정 ≠ 실 broker realized | reconciliation 모듈로 별도 검증 필요 |
| 손실 원인 분류는 *패턴 매칭* 기반 | 표현이 다른 reason은 unknown으로 떨어질 수 있음 |
| AI overconfidence 임계 (≥80) 고정 | 운영자가 calibration 확인 후 조정 필요 |
| 시간대별 PnL은 추정 (VirtualOrder 기반) | 실 broker realized PnL을 시간대별로 분해하지 *않음* |
| 종목별 / 분단위 분해 미지원 | 추후 PR — 현재는 일 단위 + 시간대 (UTC h) |

**다음 단계 backlog (별도 PR)**:
- 자동 스케줄러 — 장 마감 시각 trigger (cron / APScheduler)
- 이메일 / 텔레그램 전송 — 운영자 옵트인
- PDF / HTML 리포트
- 주간 / 월간 리포트
- 실 broker reconciliation 통합 → 정확한 realized PnL
- AI 자연어 요약 (anthropic SDK 통합 — 별도 옵트인)
- AgentDecisionLog 통합 — 본 Agent 자체 출력을 #51 audit trail에 기록

## 9. 변경 시 동기화

- 새 `LossCauseCategory` 추가 → 본 문서 §4 + `classify_findings` + 테스트
- 새 데이터 소스 → `DailyReportInput` + DB helper + 본 문서 §2
- markdown 섹션 추가 → 본 문서 §3 + 테스트 boundary
- **disclaimer 문구 변경 금지** — "투자 조언이 아니라" 표현은 invariant. 변경 시 별도 정책 PR.

## 관련 문서

- [`agent_architecture.md`](agent_architecture.md) — 6개 표준 Agent 역할 contract (#51)
- [`agent_design.md`](agent_design.md) — Agent 분리 정책
- [`daily_report_policy.md`](daily_report_policy.md) — 저장 / 보존 / 공유 정책
- [`risk_auditor_agent.md`](risk_auditor_agent.md) — 장중 실시간 위험 감독 (#54) — 본 Agent와 상보
- `app/agents/daily_report_agent.py` — 본 Agent 구현
- `scripts/generate_daily_report.py` — CLI 진입점
- `CLAUDE.md` — 절대 원칙 1번 (AI 직접 호출 금지) + 본 Agent의 *투자 조언 아님* 정책
