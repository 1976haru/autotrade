# KIS 모의 AI 자동매매 전체 코드 감사 (BUILD-02B-0)

KIS 모의 API 를 활용한 AI 자동 매수/매도가 **판단 → KIS Paper 주문 결정 → 주문 결과
→ 체결 품질 → 포트폴리오 반영 → outcome/review/feedback** 까지 코드 단위로 끊김 없이
연결되는지 offline/fake 로 꼼꼼하게 감사한다.

> ⚠️ 본 작업은 **코드 감사와 offline/fake 검증** 이다. 실제 KIS 모의/실전 API 를
> 호출하지 않으며, 실전/모의 주문을 생성하지 않는다. 주문 결과는 *fake* 로 합성한다.
> **실전 승인이 아니며 수익을 보장하지 않는다.** 장중 실제 KIS 모의 주문/체결 리허설은
> **BUILD-02B** 에서 진행한다.

관련 코드:
- `backend/app/system/kis_paper_ai_autotrade_audit.py` — 18 섹션 감사 오케스트레이터
- endpoint `GET /api/system/kis-paper-autotrade-audit` (read-only)
- CLI `scripts/run_kis_paper_autotrade_audit.py`
- 프론트엔드 `frontend/src/components/common/KisPaperAiAutotradeAuditCard.jsx` (Settings 탭)
- 테스트: `test_kis_paper_ai_autotrade_audit.py`,
  `test_run_kis_paper_autotrade_audit_script.py`, `KisPaperAiAutotradeAuditCard.test.jsx`

감사 대상(기존 검증된) 체인: `kis_paper/auto_executor.py`(route_order_fn 주입 +
assert_paper_broker + dry_run), `auto_permission.py`(권한 게이트), `order_quality.py`,
`auto_paper/capital_state.py`, decision_episode(outcome/review), agent_council,
decision_quality, post_trade_feedback, kis/endpoints, live_trading_off_policy.

## 1. 목적

개별 모듈이 아니라 *AI 자동매매 전 흐름* 이 이어지는지, 그리고 모든 안전 가드가
fake 시나리오에서 정확히 동작하는지 코드 단위로 감사한다.

## 2. AI 자동매매 전체 흐름 (18 섹션)

```
ENV_READINESS(안전 flag) → UNIVERSE(DEFAULT_UNIVERSE_50) → MARKET_DATA_CONTRACT(fake)
  → STRATEGY_VOTES(ORB/Momentum/Gap/VWAP) → AGENT_COUNCIL(final_action)
  → RISK_OFFICER(veto) → EXIT_PLAN(BUY 필수) → QUALITY_GATE(낮으면 HOLD)
  → PAPER_DECISION_BRIDGE(council BUY → KisPaperAutoDecision, HOLD → None)
  → PAPER_ORDER_EXECUTOR(result 계약: broker_order_type=KIS_PAPER, is_live_authorization=False)
  → BUY_SELL_LIMITS(권한 게이트: 모드/LIVE off/자격/confidence/quality/exit_plan/notional/daily)
  → SELL_TRIGGER(보유 청산만 — naked SELL 금지)
  → ORDER_RESULT_QUALITY(fake route_order_fn 합성 KIS_PAPER 체결 → order_quality)
  → PORTFOLIO_APPLICATION(capital_state commit_buy/commit_sell 반영)
  → OUTCOME_REVIEW_FEEDBACK(outcome/review + feedback penalty)
  → UI_CONTRACTS(표시 endpoint manifest) → LIVE_SAFETY(실전 OFF + 분리)
  → FAKE_FLOWS(fake BUY/SELL/HOLD)
```

## 3. 오프라인 감사 vs 장중 리허설 (BUILD-02B-0 vs BUILD-02B)

| 단계 | 범위 | KIS API |
|---|---|---|
| **BUILD-02B-0**(본 문서) | AI→KIS Paper 주문 전 흐름을 fake/offline 로 코드 감사 | 호출 0 (fake 합성) |
| **BUILD-02B**(다음) | 장중 실제 KIS 모의 현재가/주문/체결 polling 리허설 | KIS 모의 호출 O |

## 4. fake BUY / SELL / HOLD flow 의미

- **fake BUY**: 강한 신호 → council BUY → exit_plan/quality 통과 → KisPaperAutoDecision
  (side=BUY, broker_order_type 경로=KIS_PAPER, is_short_entry=False) 생성. fake
  route_order_fn 으로 KIS_PAPER_SUBMITTED 합성 → order_quality → portfolio 반영.
- **fake HOLD**: 약한 신호/risk_veto/exit_plan invalid/quality 낮음 → council HOLD →
  bridge 가 **None 반환(주문 decision 미생성)**. "주문 0건" 을 코드로 확인.
- **fake SELL**: 보유 포지션 있을 때만 SELL 허용(청산). 보유 없으면 council 이 SELL 을
  HOLD 로 강등(naked SELL 금지), is_short_entry=False(숏 진입 아님).

## 5. 핵심 감사 판정 (FAIL 조건)

- KIS 실제 API / 실전 endpoint 호출이 있으면 FAIL (구조적으로 0건).
- LIVE broker_order_type 이 생성되면 FAIL (KisPaperAutoResult 가 LIVE 를 ValueError 로 차단).
- is_live_authorization=true 면 FAIL (모든 결과 불변 False).
- risk_veto / exit_plan invalid / quality low 인데 주문 decision 이 생성되면 FAIL
  (council HOLD → bridge None).
- SELL 이 보유 없이 생성되면 FAIL (naked SELL → HOLD).
- BUY 에 exit_plan 없는데 주문 decision 생성되면 FAIL (council exit_plan 게이트 → HOLD).
- secret/account 노출이면 FAIL (KIS 자격은 present 여부만).

## 6. KIS 자격 present 점검

권한 게이트는 `credentials_present: bool` 라벨만 본다(App Key/Secret/계좌번호 원문 0건).
미설정/미상 → offline 감사는 가능(코드 흐름 검증)하지만 장중 리허설
(`ready_for_market_open_rehearsal`)에는 자격이 필요하므로 False.

## 7. 최종 판정

- `paper_autotrade_ready` = 필수 섹션 FAIL 0건(WARN 허용).
- `ready_for_market_open_rehearsal` = paper_autotrade_ready AND KIS 자격 present.

## 8. 실제 KIS API 호출이 없는 이유 (코드 근거)

- 감사 모듈은 broker / route_order / OrderExecutor / KIS 어댑터를 import·호출하지 않는다.
- order_quality 는 *fake 결과 객체* 로 합성한다(`fake route_order_fn`).
- 실제 executor(`execute_kis_paper_auto_order`)는 **테스트** 에서 fake route_order_fn +
  MockBrokerAdapter + dry_run=True 로만 실행 → route_order 미호출, KIS API 0건.
- 운영 경로에서도 dry_run / 권한 게이트 / `assert_paper_broker` /
  `KisBrokerAdapter.place_order(is_paper=False)` NotImplementedError 가 다층 차단.

## 9. 실행

```bash
python scripts/run_kis_paper_autotrade_audit.py --markdown reports/prebuild/kis_audit.md
python scripts/run_kis_paper_autotrade_audit.py --kis-credentials-present
# API (read-only)
GET /api/system/kis-paper-autotrade-audit
```

산출물(`reports/prebuild/`, gitignore): `kis_paper_autotrade_audit_*.json` + `.md`.
exit code: 0 paper_autotrade_ready / 1 FAIL / 2 오류.

UI: Settings 탭 `KisPaperAiAutotradeAuditCard` — 전체/섹션 PASS·WARN·FAIL +
paper_autotrade_ready + rehearsal + BUILD-02B 안내. 버튼은 점검 새로고침 / 결과 복사만
(매수/매도/주문/실전/승인 버튼 0개).

## 10. 장중 BUILD-02B 에서 확인할 항목

KIS 모의 자격 입력 후, 장중에 실제 KIS 모의 현재가 조회 → AI 판단 → KIS 모의 주문 →
체결 polling → portfolio 반영을 실제 API 로 1건 리허설(KIS_IS_PAPER=true, 실거래 OFF,
dry_run=false, 극소액·시간창·한도 내). 본 감사는 그 전 코드 정합성 확인이다.

## 11. 안전 가드 (정적 grep + dataclass 불변)

- `kis_paper_ai_autotrade_audit.py` / CLI: broker / OrderExecutor / 단일 주문 라우터 /
  KIS 어댑터 / KIS 실제 client / anthropic / openai / httpx / requests import 0건,
  broker 주문/취소/route 호출 0건, DB write 0건, secret/계좌 출력 0건.
- `KisPaperAuditReport.is_live_authorization=False` / `broker_order_sent=False` /
  `order_created=False` / `contains_secret=False` 불변.
- 안전 flag default · `.env` 변경 0건. "수익 보장" / "실전 전환 승인" 문구 0건.
