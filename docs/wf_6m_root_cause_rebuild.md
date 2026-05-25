# KIS-INTRADAY-50-6M-ROOT-CAUSE-REBUILD-01 — 손실 원인분해 + 재설계 실험

## 목적

WF-6M-50 baseline(−19%, NOT_RECOMMENDED)에 대해 "왜 개별 전략 forward-return PF 는
양(+)인데 1000만원 포트폴리오는 음(-)인가"를 전문가 수준으로 원인분해하고, 원인별 해결
실험을 구현한다. **Paper/Backtest/Report only — 실주문 0건.**

## 모듈

- `app/system/wf_6m_signal_extract.py` — per-bar council + 4전략 vote + EOD forward return 을
  한 번 계산해 *캐시*(gitignored). 모든 실험이 재사용.
- `app/system/wf_6m_sim_v2.py` — 실험용 per-timestamp 포트폴리오 시뮬: universe 필터 / 신호 선택
  랭킹(선착순 외 7종) / 비용 민감도 / 진입 시간 컷오프·강제청산 / Agent 역할(진입권 회수) /
  장세 필터 / worst-month 방어. **장세 필터는 *전일* 시장수익만 사용(look-ahead 방지).**
- `app/system/wf_6m_root_cause_analysis.py` — A~H 원인분해(신호품질/비용민감도/시간대/보유시간/
  종목군/전략별/Agent손상/walk-forward·worst-month) + 손실원인 TOP.
- `app/system/wf_6m_rebuild_experiments.py` — 실험 매트릭스 + 개선 verdict.
- CLI: `scripts/run_wf_6m_root_cause_analysis.py`, `scripts/run_wf_6m_rebuild_experiments.py`.
- API: `GET /api/system/wf-6m-root-cause/latest`, `GET /api/system/wf-6m-rebuild/latest`.
- UI(AISignal 탭): `Wf6mRootCauseCard`, `Wf6mRebuildExperimentsCard` — 데이터없음/분석중/완료/실패
  4상태, 새로고침·복사만(주문/실전/적용/자동매매 시작 버튼 0개).

## 개선 verdict 기준

- `BLOCKED`: 데이터 부족/리포트 실패/안전 위반.
- `STILL_NOT_RECOMMENDED`: PF<1.05 또는 MDD>20% 또는 return<0.
- `WATCHLIST_ONLY`: PF≥1.05, MDD≤20%, return≥0 (안정성 부족).
- `PAPER_REHEARSAL_CANDIDATE`: PF≥1.15, MDD≤15%, return≥5%, OOS 양(+), 연속손실≤12, 거래≥100.
- `RESEARCH_PROMISING`: PF≥1.25, MDD≤12%, return≥8%, OOS 양(+), worst-month 방어, Agent hurt 감소.
- 거래<100 → `LOW_CONFIDENCE`(과대평가 금지). MDD>20% → 자동 NOT_RECOMMENDED. OOS 미확인 →
  PAPER_REHEARSAL 불가.

## 실측 결과 요약 (2026, 50종목·120거래일 실제 5분봉)

- baseline(v2): −18.1%, PF 0.85, MDD 30% → STILL_NOT_RECOMMENDED.
- **비용 0 가정**: +19.2% → 거래비용(왕복 ≈ 3.1%)이 엣지를 잠식하는 1차 원인.
- **EXCLUDE 22종목 제거**: +26%p 개선(−18%→+8%). 단 *in-sample 선별*이라 OOS 검증 필요.
- **AGENT_OFF(단일전략 union)**: −13% → Agent 제거가 *더 나쁨*. Agent 는 진입권을 빼더라도
  veto/필터 역할로 *유지*가 타당(AGENT_VETO_ONLY ≈ ENTRY_DECIDER).
- **장세 필터(전일 하락일 차단)**: −0.7%. (같은 날 종가를 쓰면 +35%가 나오지만 그것은
  *look-ahead* 착시 — 본 모듈은 전일 수익만 사용해 현실 수치 −0.7% 보고.)
- 최고 *현실* 조합: `UNI_go_tune_top10`(+13.8%, PF 1.25, MDD 11%) — 그래도 OOS 미확인 →
  최종 **WATCHLIST_ONLY**.

## 안전 불변값

`RootCauseReport` / `RebuildReport` / `SimV2Result` 모두 `is_live_authorization` /
`broker_order_sent` / `order_created`=False · `do_not_auto_apply` / `no_profit_guarantee`=True
불변(dataclass 가드). 네 모듈 모두 broker / OrderExecutor / route_order / KIS 주문 API / httpx /
requests import·호출 0건(정적 grep). 어떤 조합도 자동 적용/실전 전환하지 않는다.
'Paper rehearsal candidate' 도 *조건부 검토 후보* 표현으로만 사용.
