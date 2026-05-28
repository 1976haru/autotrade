# ACUS — Agent Council Universe Selection (V1)

다중 에이전트 교차검증으로 한국 단타 5분봉 universe 에서 *모든 기준을 통과한*
ROBUST 종목 풀을 자동 추출하는 **무인(unattended) 연구 파이프라인** 이다.

> ⚠ **실거래 승인 아님 · 주문은 KIS 모의 한정 · 수익 보장 아님.**
> 본 파이프라인의 결과는 운영자 검토 자료이며 실전 전환 승인이 아니다.
> Paper 자동 진입 0 건 — 명시 승인 후에만 다음 단계로 진행.

## 1. 목적

운영자(하루)가 직장인이라 작업 중간 개입이 불가능하다는 제약 위에서:

1. **무인 자동 운영** — 지시문 1회 입력 후 결과 리포트만 확인
2. **5개 AI 에이전트의 교차 검증** — 각자 다른 기준으로 종목 평가
3. **5중 교집합 = FINAL_ROBUST** — cherry-picking 없이 전체 universe 결과 보고
4. **자동 리포트 생성** — `reports/acus/acus_final_report.md`

## 2. 아키텍처

```
backend/app/acus/
├─ __init__.py           # 버전 + 패키지 docstring
├─ types.py              # 판정 상수 + SymbolEvaluation + 안전 invariant
├─ deps.py               # PipelineDeps (load_bars, council_backtest, analyze_news 주입)
├─ bar_utils.py          # KST 일자 분리 + 일봉 집계 (read-only)
├─ data_collection.py    # Stage 01 — universe 수집 + 분봉 품질
├─ backtest_agent.py     # Stage 02 — WF 40/20 + 거래비용 + 분류 4종
├─ regime_agent.py       # Stage 03 — PIT regime 별 PF (look-ahead 금지)
├─ liquidity_agent.py    # Stage 04 — 거래대금 + spread + CV
├─ news_agent.py         # Stage 05 — Claude 분류 (lazy, 비용 한도, secret 0)
├─ risk_agent.py         # Stage 06 — σ + 상/하한 + MDD
├─ integration.py        # Stage 07 — 5중 교집합 + composite score
├─ final_report.py       # Stage 08 — verdict + markdown
├─ checkpoints.py        # 체크포인트 / progress.md / started·completed_at
└─ pipeline.py           # 오케스트레이터 (resume, 격리, 에러 graceful)

scripts/
└─ run_acus_pipeline_unattended.py    # CLI (exit 0/1/2)
```

기존 sanctioned 모듈만 재사용한다:

- `app.market_data.intraday_ohlcv.load_intraday_csv` — 분봉 적재 + 품질
- `app.backtest.strategy_council_backtest.run_strategy_council_backtest` —
  Council 백테스트(= 기존 `agent_council.run_agent_council` 재사용)
- `app.backtest.walk_forward_validation` — (참고; 본 PR 은 자체 40/20 일자분리)
- `app.backtest.point_in_time_regime` — PIT 분류 + `FORBIDDEN_FEATURES`
- `app.universe.default_universe.FALLBACK_MARKET_CAP_TOP50_NAMES` — 종목명
- `app.ai.client.AiClient` — Claude sonnet wrapper (lazy anthropic import)

## 3. Stage 매트릭스

| Stage | 책임 | 결과 분류 |
|---|---|---|
| 01 collection | 분봉 CSV 수집 + 품질 (`OK`/`WARN`/`FAIL`) | `ready_for_backtest` |
| 02 backtest | WF 40/20 train·validate, 4전략+Council, 비용 26bps | ROBUST / DECAYED / CONSISTENT / REJECTED / INSUFFICIENT |
| 03 regime | 각 후보 종목 PIT regime 별 PF (≥1.1 →robust) | REGIME_ROBUST / REGIME_WEAK / REGIME_UNKNOWN |
| 04 liquidity | 일평균 거래대금 ≥30억 + spread ≤0.5% | LIQUID / ILLIQUID / UNKNOWN |
| 05 news | Claude sonnet 4단 분류 (≥0=stable) | NEWS_STABLE / NEWS_RISKY / NEWS_UNKNOWN |
| 06 risk | σ<5% + 상/하한 ≤3회 + MDD<25% | RISK_OK / RISK_HIGH / UNKNOWN |
| 07 integration | 5중 교집합 → FINAL_ROBUST | (보고) |
| 08 final report | verdict + markdown | STRONG / WEAK / INSUFFICIENT |

## 4. 5중 교집합 조건

```python
FINAL_ROBUST = (
    BacktestAgent  ∈ {ROBUST, CONSISTENT}
    AND RegimeAgent  == REGIME_ROBUST
    AND LiquidityAgent == LIQUID
    AND NewsAgent    ∈ {NEWS_STABLE, NEWS_UNKNOWN}   # UNKNOWN 은 제외 아님
    AND RiskAgent    == RISK_OK
)
```

NewsAgent `NEWS_UNKNOWN` 은 *제외* 가 아니다(spec E2): Claude API 미구성 / 비용 한도
도달 / 응답 실패 시 보수적 통과를 허용한다(news 기준이 *작전주 차단* 이 목적이므로
정보 없음 = 자동 차단은 과도).

## 5. 최종 verdict

| `final_robust_count` | verdict | pool | 다음 단계 권고 |
|---|---|---|---|
| N ≥ 30 | `STRONG_CANDIDATE_POOL_FOUND` | STRONG_CANDIDATE_POOL | Paper rehearsal 진입 검토 가치 (운영자 명시 승인 필요) |
| 10 ≤ N < 30 | `WEAK` | DATE_POOL | 추가 데이터 / 다른 timeframe 검토 |
| N < 10 | `INSUFFICIENT` | INSUFFICIENT_POOL | 한국 5분봉 한계 — 일봉/미국/다른 자산군 검토 |

CLI exit code: 0=STRONG, 1=WEAK/INSUFFICIENT, 2=오류.

## 6. 무인 운영 인프라

### 6-1. 체크포인트 (재개 가능)

```
reports/acus/
├─ started_at.txt
├─ completed_at.txt
├─ progress.md                                 # 운영자가 핸드폰/GitHub 에서 확인
├─ acus_final_report.md                        # 최종 markdown
├─ acus_final_report.json                      # 최종 JSON
└─ checkpoints/
   ├─ stage_01_collection_ready.json
   ├─ stage_02_backtest_agent.json
   ├─ stage_03_regime_agent.json
   ├─ stage_04_liquidity_agent.json
   ├─ stage_05_news_agent.json
   ├─ stage_06_risk_agent.json
   ├─ stage_07_integration.json
   └─ stage_08_final_report.json
```

각 stage 완료 시 atomic write(`.tmp` → rename)로 checkpoint 저장. 재실행 시
`--resume`(기본) 이면 마지막 checkpoint 부터 재개.

### 6-2. 오류 격리

- **단일 종목 오류**가 전체 stage 를 중단시키지 않는다 — 종목별 try/except.
- **단일 stage 오류**가 후속 stage 를 모두 중단시키지 않는다 — 가능한 부분만 진행
  후 결과/오류를 `progress.md` + final report `notes` 에 기록.
- 사람 입력 요구 0건 (Read-Host / Get-Credential / input() 0건).

### 6-3. progress.md 자동 갱신

매 stage 시작/완료 시 갱신. 운영자가 GitHub Web/모바일 앱에서 진행률·오류·완료
시점을 확인 가능. 운영자가 보지 않아도 파이프라인은 끝까지 진행.

## 7. 절대 금지 (코드/문서/테스트로 강제)

- 실거래 주문 0건 — broker / OrderExecutor / route_order 호출 0건
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `KIS_IS_PAPER` 변경 0건
- `backend/.env` / `.env.example` default 값 변경 0건
- EXE / tauri / cargo build 0건
- 새 전략 추가 / 전략 파라미터 변경 0건 (Council·4전략 그대로 재사용)
- `auto_apply_allowed=True` / `applied_to_runtime=True` 0건
  (`SymbolEvaluation` / `ACUSFinalReport` 의 `__post_init__` ValueError 가드)
- look-ahead feature 사용 0건 — `FORBIDDEN_FEATURES`(same_day_close /
  post_entry_high 등) 그대로 유지, regime_agent 가 참조하지 않음(정적 grep 테스트)
- "수익 보장" / "실전 가능" / "실전 전환 승인" 문구 0건
- 자동으로 Paper 진입 / 자동 매매 시작 0건
- 종목 cherry-picking 0건 — 전체 universe 결과 + funnel 동시 보고

## 8. 안전 invariant (모든 stage payload + final report)

```yaml
is_order_signal:       false
auto_apply_allowed:    false
applied_to_runtime:    false
is_live_authorization: false
contains_secret:       false
no_profit_guarantee:   true
```

`SymbolEvaluation` 과 `ACUSFinalReport` 의 `__post_init__` 가 위 값 위반 시
`ValueError` 로 즉시 차단한다(테스트로 lock).

## 9. NewsAgent 안전

- **API key 는 `.env` 에서만** 읽고 (settings.anthropic_api_key) 출력/체크포인트에
  원문 0건. `app.ai.client.AiClient` 가 `AsyncAnthropic(api_key=...)` 으로만 사용.
- 모듈 top-level 에 `anthropic` import 0건 — `AiClient` 자체가 lazy.
- 비용 한도(기본 $5) 초과 시 자동 중단, 잔여 종목은 `NEWS_UNKNOWN(cost_capped)`.
- 응답 실패 / parse 실패 / API 미구성 시 `NEWS_UNKNOWN` (제외 아님).
- Claude 응답은 **모델 학습 시점 지식 기반** — 실시간 뉴스 검색 아님(웹 검색 도구
  미연결). 카드/리포트에 caveat 노출.

## 10. 사용 예

```bash
# 기본: kis_6m (50종목 6개월) 분봉, Claude 비활성 (=모두 NEWS_UNKNOWN)
python scripts/run_acus_pipeline_unattended.py

# 다른 universe + Claude 활성 (ANTHROPIC_API_KEY 필요)
python scripts/run_acus_pipeline_unattended.py \
    --input-dir data/market/intraday_ohlcv/kis_6m \
    --enable-claude \
    --news-cost-cap-usd 5.0

# 처음부터 재실행
python scripts/run_acus_pipeline_unattended.py --no-resume

# 결과
cat reports/acus/acus_final_report.md
cat reports/acus/progress.md
```

## 11. 테스트

- `backend/tests/test_acus_pipeline.py` — 5개 에이전트 mocked, 체크포인트 resume,
  단일 종목 오류 격리, FORBIDDEN_FEATURES 미참조, 5중 교집합, 안전 invariant.
- `backend/tests/test_acus_news_agent.py` — Claude mocked, secret 0건, 비용 한도,
  parse 실패 → UNKNOWN, top-level anthropic import 0건.

## 12. 다음 단계 (운영자 결정)

| verdict | 의미 | 권고 |
|---|---|---|
| `STRONG_CANDIDATE_POOL_FOUND` | FINAL_ROBUST ≥ 30 | Paper rehearsal 진입 검토 가치. **운영자 명시 승인 필요.** Paper 100건 / 28거래일 표본 + Paper Gate(#72) 통과 후에만 다음 게이트(Live Manual Approval #73) 진입 가능. |
| `WEAK` | 10 ≤ N < 30 | 추가 데이터(더 긴 기간 / 더 많은 종목) 또는 다른 timeframe. |
| `INSUFFICIENT` | N < 10 | 한국 단타 5분봉 한계 가능성 — 일봉 / 미국 주식 / 다른 자산군 방향 전환 검토. |

본 verdict 는 어떤 경우에도 **실거래 활성화 / 자동 promotion / 주문 신호** 가
아니다.
