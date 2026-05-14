# Release Readiness Report Policy (#92)

> **본 게이트는 advisory 입니다. READY_TO_TAG 라벨이 실거래 활성화 / 자동
> promotion 을 의미하지 않습니다.** 운영자가 본 리포트를 *직접 확인*하고,
> 별도 PR / git tag / GitHub Release 생성으로 진행합니다.

## 1. 목적

운영자가 *"지금 새 릴리스 태그를 찍어도 되는가 / 다음 promotion 단계로 검토
가능한가"* 를 판단할 수 있는 **단일 advisory 리포트**. 기존 governance gates +
pre-market check + alpha decay + desktop build + system hygiene + recent
activity metrics 를 *read-only 로 carry* 받아 종합 verdict 를 만든다.

## 2. 절대 원칙 invariant (코드 단 + 정적 grep 가드로 강제)

| 원칙 | 강제 위치 |
|---|---|
| READY_TO_TAG 라벨이 *실거래 활성화 / 자동 promotion 이 아님* | `ReleaseReadinessResult.__post_init__` ValueError |
| `.env` / settings 자동 수정 불가 | `auto_apply_allowed=False` 불변 + 정적 grep 가드 |
| 주문 신호 0건 | `is_order_signal=False` 불변 |
| 안전 flag mutate 0건 | 정적 grep 가드 (settings 속성 대입 / setattr / os.environ) |
| DB write 0건 | 정적 grep 가드 |
| broker / OrderExecutor / route_order 호출 0건 | 정적 grep 가드 |
| 다른 governance gate evaluator 직접 호출 0건 | 정적 grep 가드 (carry 만, *연쇄 평가 금지*) |
| 외부 HTTP / AI SDK import 0건 | 정적 grep 가드 |
| `app.core.config.get_settings` 호출 0건 | 정적 grep 가드 (입력은 payload 로) |
| frontend secret 입력 form 0개 | frontend 테스트로 lock |
| 실거래 시작 / 릴리스 자동 태깅 / Place Order 라벨 button 0개 | frontend 테스트로 lock |

## 3. verdict 4단계

| verdict | 의미 | 운영자 action |
|---|---|---|
| `READY_TO_TAG` | 모든 required PASS + WARN 0건 | 별도 PR 로 release tag 검토 가능 |
| `READY_WITH_CAVEATS` | 모든 required PASS + WARN 1건 이상 | warnings 검토 후 진행 |
| `DO_NOT_TAG` | required FAIL 1건 이상 | 실패 항목 해결 후 재평가 |
| `INSUFFICIENT_DATA` | required UNKNOWN 만 (FAIL 0건) | 입력 데이터 보강 후 재평가, 또는 `strict=true` 로 보수적 DO_NOT_TAG |

## 4. 10 카테고리 점검 항목

| 카테고리 | 항목 |
|---|---|
| `safety_flags` | `kis_is_paper_safety` / `enable_live_trading_safety` / `enable_ai_execution_safety` / `enable_futures_safety` |
| `governance_gates` | `paper_gate` / `live_manual_gate` / `ai_assist_gate` / `ai_execution_gate` |
| `pre_market` | `pre_market_check` |
| `strategy_health` | `alpha_decay` |
| `desktop_build` | `desktop_sidecar_build` / `desktop_installer_build` |
| `system_hygiene` | `system_audit_recency` / `repository_hygiene` |
| `documentation` | `documentation` |
| `data_freshness` | `data_freshness` |
| `recent_activity` | `recent_loss_limit` / `recent_audit_missing` / `recent_emergency_stop` / `test_pass_rate` |
| `operator` | `operator_opt_in` |

## 5. release_kind 별 required 매트릭스

| 항목 | BETA | RC | STABLE |
|---|---|---|---|
| 안전 flag 4종 | ✅ required | ✅ | ✅ |
| `pre_market_check` | ✅ | ✅ | ✅ |
| `system_audit_recency` | ✅ | ✅ | ✅ |
| `repository_hygiene` | ✅ | ✅ | ✅ |
| `documentation` | ✅ | ✅ | ✅ |
| `recent_loss_limit` | ✅ | ✅ | ✅ |
| `recent_audit_missing` | ✅ | ✅ | ✅ |
| `paper_gate` | optional | ✅ | ✅ |
| `alpha_decay` | optional | ✅ | ✅ |
| `desktop_sidecar_build` | optional (WARN) | ✅ | ✅ |
| `operator_opt_in` | optional | ✅ | ✅ |
| `test_pass_rate` | optional | ✅ | ✅ |
| `live_manual_gate` | optional | optional | ✅ |
| `desktop_installer_build` | optional (WARN) | optional (WARN) | ✅ |
| `data_freshness` | optional | optional | ✅ |
| `recent_emergency_stop` | optional | optional | ✅ |
| `ai_assist_gate` | optional | optional | optional |
| `ai_execution_gate` | optional | optional | optional |

## 6. 입력 DTO 설계 원칙

- **carry-only**: 본 모듈은 다른 gate evaluator (`Paper Gate`, `Live Manual
  Gate`, `AI Assist Gate`, `AI Execution Gate`, `Pre-market Check`, `Alpha
  Decay`)를 *직접 호출하지 않는다*. 호출자(API endpoint, CLI, dashboard)가 각
  gate 결과를 *라벨 / boolean 으로 요약*해서 전달.
- **`get_settings()` 호출 0건**: 안전 flag 도 입력 DTO 로만 받음. 운영자가
  *현재 .env 상태*를 명시 입력 → 실제값 ↔ 입력값 혼선 시 운영자가 즉시 인지.
- **Secret 원문 0건**: API key / 계좌번호 / Anthropic Key / Telegram Bot Token
  필드 없음. `operator_note` 만 plaintext 허용 (max 500 chars).

## 7. API

| Method | Path | 입력 | 출력 |
|---|---|---|---|
| POST | `/api/governance/release-readiness/evaluate` | `ReleaseReadinessPayload` | `ReleaseReadinessResultPayload` (items[], verdict, invariants) |
| POST | `/api/governance/release-readiness/markdown` | 동일 | `{ markdown: str, verdict: str }` |

### 응답 invariant (모든 응답)

```json
{
  "is_live_authorization": false,
  "auto_apply_allowed":    false,
  "is_order_signal":       false,
  "live_flag_changed":     false,
  "mode_changed":          false
}
```

## 8. Frontend UI (`ReleaseReadinessCard.jsx`)

| 노출 | testid |
|---|---|
| verdict 헤드라인 | `release-readiness-headline` |
| 4 invariant 영구 배지 | `release-readiness-invariant-{live,auto,tag,order}` |
| disclaimer (영구) | `release-readiness-disclaimer` |
| 대상 릴리스 정보 (`tag` + `release_kind`) | `release-readiness-target` |
| 실패 / 경고 / 필요 조치 리스트 | `release-readiness-{failed,warnings,actions}` |
| 세부 항목 표 (toggle) | `release-readiness-items` |
| markdown 미리보기 (toggle) | `release-readiness-markdown` |

### 버튼

| 라벨 | 동작 |
|---|---|
| **다시 평가** | `/release-readiness/evaluate` POST 호출 → 결과 갱신 |
| **markdown 미리보기 / 접기** | `/release-readiness/markdown` POST → markdown 표시 |
| **세부 항목 펼치기 / 접기** | UI 상태만 |

### 금지 라벨 button 0개 (테스트로 lock)

`릴리스 자동 태깅` / `git tag 자동 생성` / `GitHub Release publish` /
`자동 promotion` / `실거래 활성화` / `Place Order` / `ENABLE_LIVE_TRADING 토글` /
`.env 자동 수정` / `settings 자동 변경` / BUY|SELL|HOLD signal 라벨 button — 모두 0개.

## 9. 사용 흐름

```text
1. CI 또는 release manager 가 ReleaseReadinessPayload 채움:
   - 각 gate 의 최근 verdict label
   - .env 현재 안전 flag 상태
   - desktop build 산출물 존재 여부
   - 마지막 system hygiene audit 일시
   - 최근 7일 metrics
2. POST /api/governance/release-readiness/evaluate
3. result.verdict 확인:
   - DO_NOT_TAG          → failed_required 항목 해결, GOTO 1
   - INSUFFICIENT_DATA   → 입력 보강, GOTO 1
   - READY_WITH_CAVEATS  → warnings 검토 후 4 진행
   - READY_TO_TAG        → 4 진행
4. 운영자가 markdown 리포트 확인
5. 운영자가 별도 PR 로:
   - git tag <version>
   - GitHub Release create
   - 베타테스터 공지
```

## 10. 후속 backlog

- **자동 collector** — 각 gate evaluator + filesystem 검사를 자동 실행해
  ReleaseReadinessInput 채우는 collector (`release_readiness_collector.py`).
- **CLI** — `scripts/release_readiness_report.py --release-kind BETA --output
  reports/release_readiness_YYYY-MM-DD.md`.
- **GitHub Action** — release branch push 시 자동 평가 + PR comment 첨부.
- **알림 연계** — `DO_NOT_TAG` 발생 시 운영자에게 자동 알림.
- **history** — 시계열로 verdict 변화 추적 (`ReleaseReadinessLog` 테이블).

## 11. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙
- [`docs/paper_gate_policy.md`](paper_gate_policy.md) — #72
- [`docs/live_manual_gate.md`](live_manual_gate.md) — #73
- [`docs/ai_assist_gate.md`](ai_assist_gate.md) — #74
- [`docs/ai_execution_gate.md`](ai_execution_gate.md) — #75
- [`docs/futures_promotion_policy.md`](futures_promotion_policy.md) — #76
- [`docs/alpha_decay_monitor.md`](alpha_decay_monitor.md) — #77
- [`docs/pre_market_check_policy.md`](pre_market_check_policy.md) — #80 / #91
- [`docs/system_hygiene_report.md`](system_hygiene_report.md) — #88
- [`docs/desktop_exe_status.md`](desktop_exe_status.md) — #90 / 90-A
