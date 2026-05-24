# 체크리스트 기능 UI/API 통합 검증 (FINAL-UI-API-01)

> 최종 KIS 모의 장중 리허설(BUILD-02B) **전**, 지금까지 만든 체크리스트 기능들이
> 실제 화면(탭) · API · 카드에서 정상적으로 보이고 연결되는지 확인하는 **검증 자료**.
> 새 기능 추가가 아니다. 실제 KIS API / 모의 주문 / 실전 주문을 발생시키지 않는다.
> **실전 전환 승인이 아니며 수익을 보장하지 않는다.**

- 검증 일자: 2026-05-24 (KST)
- 브랜치: `feature/final-ui-api-checklist-verification`
- 기준 main: `c9d419e` (FINAL-AUDIT-01 / BUILD-01 / 02A / 02B-0 반영됨)

---

## 1. 목적

EXE 실행 후 사용자가 **Dashboard / AISignal / Settings** 화면에서 체크리스트 카드들을
실제로 확인할 수 있는지, 각 카드가 backend **read-only GET API** 에 올바르게 연결돼 있는지를
코드·테스트·스크립트로 검증한다. 단순 단위 테스트 통과가 아니라 *화면 mount + API 연결 + 안전
표시(금지 버튼/secret 부재)* 를 본다.

## 2. 점검 대상 화면 (탭)

| 탭 | 점검 카드 |
|---|---|
| **Dashboard** | PortfolioSourceCard · AutoPaperLoopCard · AgentCouncilVoteCard · KisPaperOneClickTestCard |
| **Settings** | KisPaperEnvStatusCard · BackendSidecarStatusCard · AppVersionCard · PreflightSmokeCard · RuntimeEventLogViewer · UniverseStatusCard · LiveSafetyStatusCard · ProgramIntegrityGateCard · PremarketReadinessGateCard · KisPaperAiAutotradeAuditCard |
| **AISignal** | AgentDecisionExplanationCard · PerformanceMetricsSummary · DecisionQualityScoreCard · PostTradeFeedbackCard · PaperGateReportCard |

## 3. 점검 대상 API (전부 read-only)

| 카드 | client.js method | endpoint | method |
|---|---|---|---|
| PortfolioSourceCard | `portfolioSource` | `/api/auto-paper/portfolio-source` | GET |
| AutoPaperLoopCard | `autoPaperStatus` | `/api/auto-paper/status` | GET |
| AgentCouncilVoteCard | `agentDecisionEpisodes` | `/api/agents/decision-episodes` | GET |
| KisPaperOneClickTestCard / KisPaperEnvStatusCard | `kisPaperReadiness` | `/api/kis-paper/readiness` | GET |
| BackendSidecarStatusCard | `exeStatus` | `/api/system/exe-status` | GET |
| AppVersionCard | `buildInfo` | `/api/system/build-info` | GET |
| PreflightSmokeCard | `preflight` | `/api/system/preflight` | GET |
| RuntimeEventLogViewer | `systemLogs` | `/api/system/logs` | GET |
| UniverseStatusCard | `universeStatus` | `/api/auto-paper/universe-status` | GET |
| LiveSafetyStatusCard | `liveSafetyStatus` | `/api/status/live-safety` | GET |
| ProgramIntegrityGateCard | `programIntegrity` | `/api/system/program-integrity` | GET |
| PremarketReadinessGateCard | `premarketReadiness` | `/api/system/premarket-readiness` | GET |
| KisPaperAiAutotradeAuditCard | `kisPaperAutotradeAudit` | `/api/system/kis-paper-autotrade-audit` | GET |
| AgentDecisionExplanationCard | `agentDecisionExplanation` | `/api/agents/decision-explanation` | POST(read-only) |
| PerformanceMetricsSummary | `agentOrderQualityMetrics` | `/api/agents/order-quality-metrics` | GET |
| DecisionQualityScoreCard | `agentDecisionQuality` | `/api/agents/decision-quality` | POST(read-only) |
| PostTradeFeedbackCard | `agentFeedbackLoop` | `/api/agents/feedback-loop` | GET |
| PaperGateReportCard | `agentPaperGateReport` | `/api/agents/paper-gate-report` | GET |

> POST 2종(`decision-explanation`, `decision-quality`)은 *DB write 0건* read-only 분석
> endpoint 다. http smoke 에서는 GET 만 호출하고, 이 둘은 매니페스트 + backend 테스트로 검증한다.

## 4~10. 화면별 확인 방법

- **Dashboard**: 상단 PortfolioSourceCard 가 source(PAPER_SIMULATED / KIS_PAPER_ACCOUNT /
  UNAVAILABLE)와 현금/총자산/포지션을 표시 — **API 실패는 "확인 불가"로, 실제 0원과 구분**.
  AutoPaperLoopCard 가 KIS 모의 자동주문 루프 상태(카운터)를, AgentCouncilVoteCard 가 4전략 vote +
  Council final_action 을, KisPaperOneClickTestCard 가 준비상태/확인 모달을 표시.
- **AISignal**: AgentDecisionExplanationCard(entry/counter/exit/risk_flags/veto/sell 근거),
  DecisionQualityScoreCard(품질 점수 + 낮으면 HOLD), PostTradeFeedbackCard(복기 태그·추천,
  자동 적용 안 됨), PerformanceMetricsSummary(expectancy/실패율/차단 사유), PaperGateReportCard
  (100건 미만 검토 불가) — **판단 기록 없으면 fallback 문구**, 주문 버튼 0개.
- **Settings**: KIS Paper 상태 / Universe 상태 / Live safety / ProgramIntegrity /
  Premarket readiness / KIS Paper AI Autotrade Audit / RuntimeEventLogViewer / Preflight /
  Version 카드. WARN/FAIL/PASS 구분 표시, 실전/매수/매도/승인 버튼 0개, secret/계좌 원문 0건.

## 11. 자동 검증 (테스트 / 스크립트)

### Backend
- 모듈: `backend/app/system/ui_api_checklist.py` (`run_manifest_checks` / `http_targets` /
  `evaluate_http_results`). broker / OrderExecutor / route_order import 0건.
- 테스트: `backend/tests/test_ui_api_checklist_verification.py` (42 PASS) —
  매니페스트 19 항목 전부 PASS + in-process TestClient 로 14개 read-only GET endpoint 가
  실제 200 응답 + secret 미노출 + `is_live_authorization`/`broker_order_sent` 미주장 +
  read-only endpoint POST→405.

### Frontend
- 테스트: `frontend/src/components/tabs/UiApiChecklistVerification.test.jsx` (59 PASS) —
  19개 카드 각각 탭에 import+render, client.js 에 method+url 연결, 카드 단위 runtime
  `.test.jsx` 존재(금지 버튼/fallback/secret 미표시 invariant lock)를 정적 확인.
- 금지 버튼 부재 / API 실패 fallback / secret 미표시의 *런타임* 검증은 각 카드의 개별
  `.test.jsx` 가 담당(이미 존재).

### Smoke 스크립트
`scripts/run_ui_api_checklist_verification.py`:
```
# offline 정적 매니페스트
python scripts/run_ui_api_checklist_verification.py --mode manifest \
    --markdown reports/final_audit/ui_api_check.md
# 실행 중 backend 의 read-only GET 만 호출 (운영자가 EXE/dev 서버에 대해)
python scripts/run_ui_api_checklist_verification.py --mode http \
    --base-url http://127.0.0.1:8000 --markdown reports/final_audit/ui_api_http_check.md
```
- 옵션: `--mode manifest|http` · `--base-url` · `--strict` · `--output` · `--markdown`.
- exit: 0 (PASS/WARN) / 1 (FAIL) / 2 (실행 오류).
- **http mode 는 GET(read-only) endpoint 만** 호출 — POST/주문/start/approve endpoint 0건.
  응답 본문은 secret 패턴 스캔 후 발견 시 FAIL(원문 미출력, 마스킹).

## 11-1. 시나리오별 기대 동작

| 시나리오 | 기대 |
|---|---|
| A. KIS 자격 미설정 | KIS readiness BLOCKED/WARN, portfolio "확인 불가"(0원 아님), 주문 버튼 0개 |
| B. 장 닫힘 + 자격 present | KIS Paper READY, `ready_for_market_open_rehearsal=True`, **여전히 주문 미실행**, BUILD-02B 안내 |
| C. Agent 판단 기록 없음 | AISignal 카드 fallback("표시할 판단 기록이 없습니다"), undefined 오류 없음 |
| D. fake decision episode | entry/counter/exit/risk_flags/quality/feedback/성과 지표 표시 |
| E. live safety | ENABLE_LIVE_TRADING=false, KIS_IS_PAPER=true, live path gated, Paper/Live 분리, 실전/승인 버튼 0개 |

## 12. 수동 확인 체크리스트 (EXE / dev 서버)

공통: secret/계좌 원문 없음 · 실전/매수/매도/승인 버튼 없음 · PASS/WARN/FAIL 구분 명확 ·
장 닫힌 날/KIS 자격 미설정 WARN 이 정상 표시.

- [ ] Dashboard: Portfolio source 카드 표시, "확인 불가"와 0원 구분, 오류 시 화면 안 깨짐
- [ ] AISignal: Council vote / 설명 / 성과 / 품질 / 피드백 표시, 기록 없을 때 fallback
- [ ] Settings: KIS Paper / Universe / Live safety / ProgramIntegrity / Premarket /
      KIS Autotrade Audit / RuntimeEventLogViewer / Preflight / Version 카드 표시

## 결론

- 매니페스트(backend) **19/19 PASS**, frontend mirror **59 PASS**, backend 테스트 **42 PASS**.
- 모든 체크리스트 카드가 화면에 mount + read-only GET API 에 연결됨 — 주문/실전/승인 버튼 0개,
  secret 미노출.
- **READY_WITH_WARNINGS** — 잔여 WARN 은 KIS 자격 미설정(운영자 입력 전 정상) / http smoke 는
  운영자가 실행 중 서버에 대해 수행. **실전 승인 아님, 실제 주문 0건.**
