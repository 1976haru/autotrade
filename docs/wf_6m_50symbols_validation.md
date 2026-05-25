# WF-6M-50SYMBOLS-01 — 6개월·50종목·1000만원 전략 종합 검증 + 업그레이드 방향

## 목적

"내 전략(ORB/VWAP/Momentum/Gap + Agent Council)이 **실제로 1000만원 기준에서 의미 있는가**,
실제 모의/실전으로 이어질 가능성이 있는가, 어떻게 업그레이드해야 하는가" 를 6개월·50종목·실제
5분봉 기반으로 검증한다. **Paper/Backtest/Simulation only — 실주문 0건.**

## 절대 원칙

- 실제 시장 데이터만 사용 (KIS 5분봉 우선, KIS-INTRADAY-100-VALIDATION-01 collector 재사용).
- 실주문/모의주문/실전주문 금지. `route_order`/`OrderExecutor`/`broker.place_order` 호출 0건.
- 자동 실전 전환 금지, threshold 자동 반영 금지, 결과 과장/수익 보장 문구 금지.
- 결과가 나빠도 그대로 보고 (try/except 에러 은폐 금지, 테스트 삭제/skip 금지).
- `ENABLE_LIVE_TRADING`/`ENABLE_AI_EXECUTION`/`ENABLE_FUTURES_LIVE_TRADING`=false,
  `KIS_IS_PAPER`=true 유지.

## 구성

- `app/backtest/portfolio_capital_sim.py::run_portfolio_capital_sim` — **공유 자본 포트폴리오
  자금곡선 시뮬레이터** (신규). 초기 10,000,000 KRW, 동시 보유 ≤5, 종목당 1~2백만원, 현금
  부족/중복 진입 차단, 위탁수수료+증권거래세(매도)+슬리피지 반영, **오버나이트 없음(장마감
  강제청산)**. Agent Council BUY 를 진입 신호로, exit_plan(stop/target)+EOD 를 청산으로 사용.
  `signals` 주입 시 council 재계산 생략(테스트/재현). 출력: 하루/주/월 평균 수익, MDD,
  자금곡선, 승률, 손익비, 평균 보유시간, 최악 연속손실, 최악 하루, 월별 수익률, 종목별 net_pnl.
- `app/system/wf_6m_50symbols_report.py::build_wf_6m_report` — 포트폴리오 시뮬 +
  종목별 검증(`intraday_strategy_validation`) + 집계 전략 분해(`strategy_council_backtest`)
  종합 → 종목 등급화(GO/WATCH/TUNE/EXCLUDE) + 전략 생존/사망 + 장세/시간대 버킷 +
  실전 가능성 단계 + 강점/약점 TOP3 + Agent 최적 역할 + 업그레이드 방향 + 확장성 평가 +
  최종 판정.
- `scripts/run_wf_6m_50symbols_report.py` — CLI →
  `reports/strategy_validation/wf_6m_50symbols_final_result.{md,json}` +
  `wf_6m_50symbols_latest.json` (gitignore).
- `GET /api/system/wf-6m-50symbols/latest` — latest 리포트 read-only (없으면 empty fallback,
  무거운 시뮬/backtest 미실행).
- frontend `Wf6m50SymbolsCard` (AISignal 탭) — verdict/자금곡선/등급/전략 생존/업그레이드 표시.
  새로고침·복사만, 매수/매도/실전/자동적용/승인 버튼 0개, input/textarea 0개.

## 판정 단계

- `NOT_RECOMMENDED` (현재 상태로는 위험): 비용 후 손실 + PF<1, 또는 MDD≥30% + 수익 미발생.
- `RESEARCH_ONLY`: 데이터/엣지 부족.
- `WORTH_MORE_RESEARCH` (튜닝 필요): 비용 후 양(+)이나 walk-forward<40(과최적화) 또는 Agent 효과 부족.
- `PAPER_REHEARSAL_WORTHY` (Paper 확대 가능): 6개월 history + 비용 후 양(+) + PF≥1.3 + WF≥40 +
  Agent 도움 모두 충족 시에만. **그래도 실전이 아니라 Paper 단계** — 실전 검토는 Paper 100건 +
  28거래일 + 운영자 명시 승인 필요.

## 종목 등급

- `GO`: trades≥8 + PF≥1.3 + expectancy>0 + walk_forward≥40 + Agent 도움.
- `TUNE`: 양의 기대값이나 WF 약함 또는 Agent 방해.
- `WATCH`: 표본 부족/경계.
- `EXCLUDE`: 손실 + 약한 엣지(PF<1 또는 expectancy≤0).

## 비용 모델 (현실 반영)

위탁수수료 1.5bps(편도) + 증권거래세 18bps(매도) + 슬리피지 5bps(편도) — 비용 *후* 성과만
보고한다(비용 전 과장 금지). 운영자가 `SimConfig` 로 조정 가능.

## 안전 불변값

`PortfolioSimResult` / `Wf6mReport` 는 `is_live_authorization=False` / `broker_order_sent=False` /
`order_created=False` / `do_not_auto_apply=True` / `contains_secret=False` /
`no_profit_guarantee=True` 불변(dataclass 가드). 두 모듈 모두 broker / OrderExecutor /
route_order / KIS 주문 API / httpx / requests import 0건(정적 grep 가드).
