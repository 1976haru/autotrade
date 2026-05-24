# 실매매 기본 OFF + KIS Paper/Live 분리 + Live Capital Review (#70/#71/#72 · 9-01/02/03)

실전매매는 **별도 승인 전 절대 자동 시작되지 않는다.** 본 문서는 (70) 실매매 기본
OFF 정책, (71) KIS Paper/Live endpoint·TR·account 완전 분리, (72) Live Capital
Review 구조를 정책·코드·테스트로 고정한다.

> ⚠️ 본 작업은 **실전매매를 켜는 작업이 아니다.** 실전 주문을 보내지 않으며, KIS
> live endpoint 를 실제 호출하지 않고, 실전 자동매매를 활성화하지 않는다. 실전 사고를
> 막기 위한 *정책 / 분리 / 승인 Gate* 작업이다. **수익을 보장하지 않는다.**

관련 코드:
- `backend/app/permission/live_trading_off_policy.py` (#70)
- `backend/app/kis/endpoints.py` (#71)
- `backend/app/permission/live_capital_review.py` (#72, #42 gate 재사용)
- endpoint `GET /api/status/live-safety` (read-only)
- 프론트엔드 `frontend/src/components/common/LiveSafetyStatusCard.jsx`
- 테스트: `test_live_trading_off_policy.py`, `test_kis_paper_live_separation.py`,
  `test_live_capital_review.py`

기존(재사용): #41 `live_capital_guard.py`, #42 `live_manual_approval_gate.py`(→[`docs/live_manual_approval_gate.md`](live_manual_approval_gate.md)),
#43 `live_canary_gate.py`, #45 `live_transition_audit.py`, [`docs/capital_allocation_policy.md`](capital_allocation_policy.md).

## 1. (#70) 실매매 기본 OFF

기본값에서 실전매매는 **OFF** 다:

| 안전 플래그 | 기본값 |
|---|---|
| `ENABLE_LIVE_TRADING` | `false` |
| `ENABLE_AI_EXECUTION` | `false` |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` |
| `KIS_IS_PAPER` | `true` |
| `DEFAULT_MODE` | `SIMULATION`(config) / `PAPER`(.env.example) |

- **기본 EXE 실행 시 실전 주문은 불가능하다.**
- 어떤 환경변수 하나만으로도 실전 주문이 허용되지 않는다.
- 실전 경로는 별도 Gate 없이는 항상 `BLOCKED`(`live_path_gated=True`).
- 실전은 Live Capital Review(#41/#72) + Manual Approval(#42) + Canary(#43) +
  Audit(#45) 를 *모두* 통과해야 검토 가능하다(검토 ≠ 주문).

`evaluate_live_off_policy(...)` 는 현재 안전 플래그를 *입력 DTO* 로 받아(실제값↔입력값
혼선 방지) 다음을 반환:
- `live_path_gated=True`, `live_order_blocked=True` (불변).
- `is_live_authorization=False` / `broker_order_sent=False` / `order_created=False` (불변).
- reason_codes: `LIVE_ORDER_BLOCKED_BY_DEFAULT` / `LIVE_PATH_GATED` /
  `LIVE_REQUIRES_EXPLICIT_APPROVAL` / `LIVE_TRADING_DISABLED_BY_DEFAULT` /
  `LIVE_AI_EXECUTION_DISABLED_BY_DEFAULT` / `LIVE_NOT_AVAILABLE_IN_DEFAULT_EXE`.

## 2. (#71) KIS Paper / KIS Live endpoint·TR·account 완전 분리

모의 설정이 실전으로 새는 사고를 방지한다. host/TR/account 가 *별도 값* 이며 어느
한쪽이 다른 쪽의 fallback 이 되지 않는다.

| 구분 | Paper(모의) | Live(실전) |
|---|---|---|
| host | `openapivts.koreainvestment.com:29443` | `openapi.koreainvestment.com:9443` |
| TR prefix | `V` (예: VTTC0802U) | `T` (예: TTTC0802U) |
| account mode | `PAPER` | `LIVE` |

`resolve_kis_endpoint(kis_is_paper, explicit_live_gate_passed)`:
- `kis_is_paper=true` → **PAPER** (host=Paper, TR=V, account=PAPER). live host/TR 미사용.
- `kis_is_paper=false` + **explicit live gate 없음** → **BLOCKED** (host=None) — *Paper
  로 fallback 하지 않는다*. `reason_code=KIS_LIVE_GATE_REQUIRED`.
- `kis_is_paper=false` + explicit live gate 통과 → **LIVE** 경로 *선택* — 단, **실제
  주문은 여전히 차단**(place_order 가드 + 승인 Gate). `broker_order_sent=False`.

host 상수는 `app/brokers/kis_client.py` 의 **단일 진실** 을 재사용(테스트로 일치 검증).
`KisBrokerAdapter.place_order(is_paper=False)` 는 `NotImplementedError` 로 실전 주문을
하드 차단한다(기존 가드, 회귀 테스트로 확인).

## 3. (#72) Live Capital Review

실전 주문한도는 Paper 자금과 **분리** 되며, 다음이 *모두* 없으면 live order 가 차단된다:
- **operator approval** (운영자 이름 + 사유 + 시각 + manual_approval_present)
- **max_order_notional** 설정
- **daily_live_limit** 설정
- **symbol whitelist** (≥1)
- (+ live capital review approved, manual approval)

`build_live_capital_review(inp)` 는 #42 `evaluate_live_manual_approval_gate` 를 *그대로
재사용* 하고, 결과를 review 상태로 요약:
- `MISSING` — 실전 요청 아님 / 검토 입력 없음.
- `INCOMPLETE` — 실전 요청이나 필수 조건 미충족.
- `READY` — 모든 필수 조건 충족 (**검토 readiness 일 뿐 — 주문 아님**).

**Live Capital Review 는 주문 승인이 아니다.** READY 여도 `order_created=False` /
`broker_order_sent=False` / `is_live_authorization=False`. Paper 자금은 live 한도 계산에
사용되지 않는다(분리).

## 4. 통합 상태 API

`GET /api/status/live-safety` (read-only) — `live_policy` / `kis_endpoint` /
`live_capital_review` 3블록 반환. broker / route_order 호출 0건, DB write 0건,
secret/계좌번호 원문 0건, `is_live_authorization=False`.

## 5. UI

Settings 탭 `LiveSafetyStatusCard` — 3블록 표시 + 안내 문구:
- "실전매매 기본 OFF"
- "KIS Paper 와 KIS Live 경로는 분리되어 있습니다."
- "KIS_IS_PAPER=false 여도 explicit live gate 없이는 차단됩니다."
- "Live Capital Review 는 주문 승인이 아닙니다."
- "현재 실전 주문은 차단 상태입니다."

**버튼 금지**: 실전 켜기 / live order / approve live / 매수 / 매도. 입력 form 0개.

## 6. 안전 가드 (정적 grep + dataclass 불변)

- 세 모듈 모두: broker / OrderExecutor / 단일 주문 라우터 / KIS 어댑터(엔드포인트
  정책 모듈은 host 상수만 재사용) import·호출 0건(주문/취소/route), 외부 HTTP / AI SDK
  호출 0건, DB write 0건, secret/계좌번호 출력 0건.
- 불변: `is_live_authorization` / `broker_order_sent` / `order_created` 항상 False.
- 안전 flag default 변경 0건, `.env` 변경 0건.
- 본 PR 은 실전을 켜지 않으며, 실전 전환 *승인* 을 수행하지 않는다(검토/표시/차단만).
