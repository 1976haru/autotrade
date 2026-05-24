# 최종 빌드 전 전체 프로그램 정합성 점검 (BUILD-01)

최종 EXE 빌드 전에, 지금까지 만든 기능들이 **전체 흐름에서 끊기지 않고 연결**되는지
하나의 read-only/모의 리포트로 검증한다.

> ⚠️ 본 작업은 빌드 전 통합 *검증* 이다. 실전 주문을 보내지 않으며, KIS 실전/모의
> *실제 API* 를 호출하지 않는다. 주문 결과는 **fake route_order(합성)** 로 만들며
> broker / OrderExecutor / route_order 를 호출하지 않는다. **실전 승인이 아니고
> 수익을 보장하지 않는다.** 장중 실제 KIS 모의 API 테스트는 **BUILD-02** 에서 진행.

관련 코드:
- `backend/app/system/program_integrity_gate.py` — 16 섹션 오케스트레이터
- endpoint `GET /api/system/program-integrity` (read-only)
- CLI `scripts/run_program_integrity_gate.py`
- 프론트엔드 `frontend/src/components/common/ProgramIntegrityGateCard.jsx` (Settings 탭)
- 테스트: `test_program_integrity_gate.py`, `test_run_program_integrity_gate_script.py`,
  `ProgramIntegrityGateCard.test.jsx`

## 1. 목적

빌드 직전, 각 모듈이 *개별* 로는 통과해도 *이어붙였을 때* 흐름이 끊기지 않는지
확인한다. 한 번의 호출로 전체 파이프라인을 모의 실행해 섹션별 PASS/WARN/FAIL 과
`build_ready` 를 산출한다.

## 2. 점검 흐름도 (16 섹션)

```
Universe/관심종목
  → KIS 모의 readiness
  → 4전략 vote(ORB/Momentum/Gap/VWAP)
  → Agent Council(final_action/confidence/quality/selected)
  → RiskOfficer veto
  → exit_plan(BUY 필수)
  → quality_score gate(낮으면 HOLD)
  → BUY/SELL/HOLD 결정
  → KIS Paper decision(broker_order_type=KIS_PAPER, HOLD→없음)
  → (fake) KIS 모의 주문 결과(PAPER-SIM, 실전 아님)
  → order_quality(슬리피지/부분체결/체결)
  → portfolio 반영(PAPER_SIMULATED source)
  → outcome/review
  → feedback/quality 다음 판단 반영(auto_apply_allowed=false)
  → UI/API 표시 manifest
  → Live safety(실전 OFF + path gated + Paper/Live 분리)
```

각 섹션은 **기존 모듈을 재사용** 해 read-only 로 점검한다(재구현 0):
universe_status / kis_paper.readiness / agent_council(evaluate_*, run_agent_council) /
decision_quality / kis_paper.order_quality / portfolio_snapshot / post_trade_outcome /
post_trade_review / post_trade_feedback / live_trading_off_policy / kis.endpoints.

## 3. 판정 규칙

- **PASS** — 해당 단계가 정상 연결.
- **WARN** — 빌드 환경에서 정상인 경고(예: KIS 자격 미설정 → mock 모드 가능; 장 닫힘).
- **FAIL** — 흐름이 끊김(정책 위반/누락).
- **build_ready** = 필수 섹션 FAIL 0건 AND Live safety PASS. (WARN 은 빌드 허용.)
- `overall_verdict` = FAIL 있으면 FAIL, 아니면 WARN 있으면 WARN, 아니면 PASS.

필수 섹션(FAIL 시 빌드 차단): Universe / Strategy votes / Agent Council / RiskOfficer /
exit_plan / quality gate / decision / KIS Paper decision / order result / order_quality /
portfolio / outcome·review / feedback·quality / Live safety. (KIS readiness · UI/API 는 WARN 허용.)

## 4. 섹션별 점검 요약

| 섹션 | 점검 내용 |
|---|---|
| Universe | watchlist 없으면 기본 50 fallback, count>0, invalid 제거 |
| KIS readiness | KIS_IS_PAPER=true, 자격 present(없으면 WARN), live flag off |
| Strategy votes | 4전략 vote 생성 + signal/score/confidence/reason 필드 |
| Agent Council | final_action/confidence/quality/selected_strategies/score 산출 |
| RiskOfficer | veto 구조 존재 (적용/미적용 모두 PASS) |
| exit_plan | BUY 는 valid exit_plan 필수 |
| quality gate | enhanced quality + should_hold + pre_quality_action |
| decision | BUY/SELL/HOLD, is_order_signal=False |
| KIS Paper decision | BUY/SELL→broker_order_type=KIS_PAPER, HOLD→없음 (LIVE 아님) |
| order result | fake route_order 합성 체결(PAPER-SIM, broker_order_sent=false) |
| order_quality | 슬리피지 bps / 부분체결 / 체결 상태 |
| portfolio | PAPER_SIMULATED source, cash/total_asset/positions |
| outcome/review | outcome label + review grade/tags |
| feedback/quality | 피드백 태그 + threshold 추천(auto_apply=false) + 다음 quality penalty 반영 |
| UI/API | 표시 대상 endpoint manifest 존재 (실 route 주입 시 검증) |
| Live safety | 실전 OFF + live path gated + Paper/Live 분리 + 실전 주문 0건 |

## 5. 실행

```bash
# CLI
python scripts/run_program_integrity_gate.py --markdown reports/build/integrity.md
# (exit 0=build_ready / 1=FAIL 있음 / 2=실행 오류)

# API (read-only)
GET /api/system/program-integrity
```

산출물(`--output` 미지정 시 `reports/build/`): `program_integrity_*.json` + `.md`.
`reports/` 는 `.gitignore` 등록 — 리포트는 커밋하지 않는다.

UI: Settings 탭 `ProgramIntegrityGateCard` — 전체 판정 + build_ready + 섹션별
PASS/WARN/FAIL + fail/warn reason. 버튼은 "점검 새로고침" / "리포트 복사" 만(주문/
실전/승인 버튼 0개).

## 6. KIS 모의 주문 경로 분리 (BUILD-01 vs BUILD-02)

- **BUILD-01(본 작업)**: fake route_order 기반 *오프라인* 통합 정합성 — 실제 KIS API 호출 0건.
- **BUILD-02(다음 단계)**: 장중 *실제 KIS 모의 API* 테스트(KIS_IS_PAPER=true, 실거래 OFF).

## 7. 안전 고지

- 본 점검은 **실전 전환 승인이 아니다.** `is_live_authorization=False` /
  `broker_order_sent=False` / `order_created_live=False` 불변.
- 실제 주문 / KIS 실거래 호출 0건(주문 결과 fake 합성). **수익을 보장하지 않는다.**
- 안전 flag default(`ENABLE_LIVE_TRADING`/`ENABLE_AI_EXECUTION`/
  `ENABLE_FUTURES_LIVE_TRADING`/`KIS_IS_PAPER`) 변경 0건, `.env` 변경 0건.

## 8. 안전 가드 (정적 grep + dataclass 불변)

- `program_integrity_gate.py` / CLI: broker / OrderExecutor / 단일 주문 라우터 /
  paper_trader / KIS 어댑터 / anthropic / openai / httpx / requests import 0건,
  broker 주문/취소/route 호출 0건, DB write 0건, secret/계좌 출력 0건.
- 리포트/스크립트에 "수익 보장" / "실전 전환 승인" 문구 0건(테스트로 lock).
