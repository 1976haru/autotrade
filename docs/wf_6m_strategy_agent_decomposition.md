# KIS-INTRADAY-50-6M-STRATEGY-AGENT-DECOMPOSITION-01 — 매매기법 vs Agent 분해

## 목적

WF-6M-50 baseline(−19%)에서 다음을 분리 규명한다: ① 매매기법만으로는? ② Agent를 끄면
손실이 주는가? ③ Agent 7역할 중 어떤 게 돕고 방해하는가? ④ 손실 원인(신호/종목/5슬롯/
비용/청산/Agent) ⑤ Paper 리허설 후보 조합. **Paper/Backtest/Report only — EXE 빌드 0건.**

## 모듈/엔진

- 엔진 재사용·확장: `wf_6m_signal_extract`(캐시) + `wf_6m_sim_v2`(allowed_strategies /
  max_hold·trailing·time_stop 청산 / 7 Agent 역할 / grade veto / size_scale / daily·equity
  손실 stop 추가).
- `app/system/wf_6m_strategy_agent_decomposition.py` — 전 섹션 종합 + verdict + EXE 재빌드 권고.
- CLI `scripts/run_wf_6m_strategy_agent_decomposition.py` (→ `strategy_agent_decomposition_*.{json,md}`
  + immutable `decomposition_baseline_snapshot.{json,md}` + `*_latest.json`, gitignore).
- endpoint `GET /api/system/strategy-agent-decomposition/latest` + UI
  `StrategyAgentDecompositionCard`(AISignal 탭, 4상태, 새로고침·복사만 — 주문/실전/적용/
  자동매매 시작/EXE 빌드 버튼 0개).

## 7 Agent 역할

AGENT_OFF / ENTRY_SELECTOR(council) / RISK_VETO_ONLY(EXCLUDE·저품질 차단) /
POSITION_SIZER_ONLY(등급별 사이즈) / EXIT_ADVISOR_ONLY(trailing·time stop) /
REGIME_FILTER_ONLY(전일 하락일 차단) / REVIEW_ONLY(사후 분석, 매매 무개입).

## EXE 재빌드 권고 (verdict 매핑)

- STILL_NOT_RECOMMENDED 이하 → **EXE 재빌드 보류**.
- WATCHLIST_ONLY → EXE 빌드 가능하나 자동매매 비활성/관찰용만.
- PAPER_REHEARSAL_CANDIDATE 이상 → EXE 재빌드 후 *모의매매 리허설* 가능 (실전 금지).

## 실측 결과 (50종목·120거래일 실제 5분봉)

- **매매기법 only(Agent OFF)는 전부 손실**: GAP −11%, ORB −25%, VWAP −33%, MOMENTUM
  −44%, 모든 조합 −24~−34%. → 전략만으로는 비용 후 생존 불가.
- **Agent를 끄면 더 나쁨**: AGENT_OFF −34% vs council −18%. **RISK_VETO_ONLY −2.8%
  (+31pp), POSITION_SIZER_ONLY −1.4%·MDD 5.7%(+32pp)** — Agent 위험기능이 최대 개선 레버.
  EXIT_ADVISOR(trailing) −80%(회전 비용 폭증, 금지), REVIEW_ONLY = OFF.
- **비용**: baseline −18% → 비용0 +19%(왕복 0.31%/거래, 매도세 18bps가 치명).
- **5슬롯**: council BUY 39,437건 중 676건(1.7%)만 체결, 5후보 초과 2,693시점, 신호 평균
  fwd +0.0015(양호) → 선착순이 좋은 신호를 못 담음.
- **Paper 리허설 후보(검증)**: `DEF_daily_loss_1.5`(+9.2%/PF1.21/MDD7.25%/259거래/OOS+),
  `DEF_equity_dd_10`(+9.0%/PF1.20/MDD7.43%) — GO+TUNE universe + composite + 일손실/Equity-DD
  stop. 3월 −23%→−8.8% 방어. → **PAPER_REHEARSAL_CANDIDATE**. *단 universe 선별이 in-sample
  이라 진짜 forward(미래 표본) 검증은 별도 필요.*

## 안전 불변값

`DecompositionReport`/`SimV2Result` 모두 `is_live_authorization`/`broker_order_sent`/
`order_created`/`exe_build_executed`=False · `do_not_auto_apply`/`no_profit_guarantee`=True ·
`auto_apply_allowed`=False 불변(dataclass 가드). 모듈/스크립트 broker/OrderExecutor/route_order/
KIS 주문 API/httpx/requests import·호출 0건, EXE 빌드 명령(tauri/cargo) 0건(정적 grep), 안전
flag 변경 0건, 수익 보장 문구 0건, 기존 final result 데이터 보존.
