# Step 3-08 — 운영자(비개발자)용 전략 최적화 리포트

> 본 문서는 *연구 / 검증* 파이프라인 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 리포트는 *운영자 검토 자료* — 자동 paper trader 시작 / 자동 실거래
> 활성화 / 자동 promotion 변경을 의미하지 **않습니다**.

## 1. 목적

3-02 (real data backtest) / 3-03 (parameter optimization) / 3-04 (walk-forward)
/ 3-05 (stress test) / 3-06 (성과 지표 표준화) / 3-07 (paper 후보 통합) 의 *모든
산출물* 을 종합해 **비개발자가 이해할 수 있는** Markdown 리포트 2종을
자동 생성합니다.

핵심 의도:
- 운영자(비개발자) 가 "지금 어느 전략을 모의투자에서 써도 되는가" 를 *5 분
  안에* 판단할 수 있게.
- "어느 단계에서 탈락했는가" 를 *명시* 해서 운영자가 보완 행동을 즉시
  선택할 수 있게.
- AI Agent 가 의사결정 컨텍스트로 *참고* 할 수 있는 위험 신호 carry
  (`ai_agent_risk_signals` — 자동 차단 트리거 아님).

## 2. 생성 파일 (3종)

| 경로 | 의미 | git 커밋 |
|---|---|---|
| `reports/strategy_optimization/strategy_optimization_report.md` | 12 섹션 상세 리포트 | ❌ (`reports/*` gitignore) |
| `reports/strategy_optimization/operator_summary.md` | 한 페이지 운영자 요약 | ❌ (`reports/*` gitignore) |
| `docs/strategy_optimization_report.md` | 본 정책 / 사용 가이드 (현재 문서) | ✅ |

`reports/*` 은 `.gitignore` 에 등록되어 있습니다 — *운영 로그는 git에 커밋되지
않습니다*. 테스트는 `tmp_path` 에서만 생성을 확인합니다.

## 3. 입력

```bash
python scripts/run_strategy_optimization_report.py \
    --from-paper-candidate reports/strategy_optimization/paper_candidate_config.json \
    --from-backtest        reports/backtest_real/real_data_backtest_summary.json \
    --from-optimization    reports/parameter_optimization/parameter_optimization_summary.json \
    --from-walk-forward    reports/walk_forward/walk_forward_summary.json \
    --from-stress-test     reports/stress_test/stress_test_summary.json
```

모든 입력은 *optional* — 없으면 해당 단계 스킵하고, 리포트는 "데이터 없음"
표시로 계속 생성됩니다. 후보 0건도 *반드시* 파일 생성합니다.

### Dry-run

```bash
python scripts/run_strategy_optimization_report.py --dry-run \
    --from-backtest      ... \
    --from-optimization  ... \
    --from-walk-forward  ... \
    --from-stress-test   ...
```

`--dry-run` 은 stdout 으로 요약과 `operator_summary.md` 미리보기를 출력하고
파일 작성은 **수행하지 않습니다**.

## 4. 12 필수 섹션

`strategy_optimization_report.md` 는 다음 12 섹션을 *반드시* 포함합니다
(테스트 lock):

| § | 섹션 | 내용 |
|---|---|---|
| 1 | 전체 결론 | 6 상태 중 하나 ("Paper 시작 가능" / "사용 불가") |
| 2 | 전략별 순위 | (전략, 종목, 기대값, PF, MDD, 승률, 거래수) 표 |
| 3 | Paper 후보 전략 | 3-07 통과한 후보 — 파라미터 + 통과 단계 + 점수 |
| 4 | 후보가 없는 경우 사유 | 0 건일 때 `reasons_no_candidate` carry |
| 5 | 제외된 전략과 사유 | 탈락 후보 + 한국어 사유 (어느 단계에서 떨어졌는지) |
| 6 | 수수료·슬리피지 반영 결과 | raw / fee-adjusted / slippage-adjusted return 비교 |
| 7 | Walk-forward 결과 | 과최적화 점검 — verdict + fold 수 |
| 8 | Stress Test 결과 | 시나리오 PASS/FAIL — 견고성 |
| 9 | 핵심 성과 지표 | 3-06 표준 14 키 (MDD/PF/기대값/승률/거래수/loss_streak 등) |
| 10 | AI Agent 가 참고할 위험 신호 | `profit_factor_below_1` 등 — **자동 차단 아님** |
| 11 | 다음 Paper 모의운용 가능 여부 | "수동 시작" 명시 — 자동 시작 ❌ |
| 12 | 사용자가 해야 할 다음 행동 | 비개발자 친화 행동 리스트 |

`operator_summary.md` 는 한 페이지 — 결론 한 줄 + 핵심 숫자 + 위험 신호
top 5 + 다음 행동.

## 5. 6 상태 (`ReportStatus`)

비개발자가 *즉시* 판단 가능한 4 라벨 + 위험 라벨 2종:

| 상태 | 의미 | 비개발자 한 줄 |
|---|---|---|
| `READY_FOR_PAPER` | 모든 단계 통과 | "모의투자(Paper)에서 시작 검토 가능" |
| `NEED_MORE_DATA` | 거래 횟수 / fold 부족 / 단계 누락 | "데이터 부족 — 더 모은 뒤 재평가" |
| `REJECTED_BY_RISK` | 3-02 / 3-03 위험 한도 위반 | "위험 한도 위반 — 아직 사용 안 됨" |
| `OVERFIT_RISK` | 3-04 walk-forward 가 OVERFIT_RISK | "과최적화 의심 — 아직 사용 안 됨" |
| `STRESS_FAILED` | 3-05 시나리오 하나 이상 FAIL/WARN | "스트레스 테스트 불합격 — 아직 사용 안 됨" |
| `NO_CANDIDATE` | 종합 판정 — 후보 자격 없음 | "현재 후보 자격 없음" |

선정 우선순위 (가장 *나쁜* 신호 우선):
1. **NEED_MORE_DATA** — 단계 누락 / `INSUFFICIENT_DATA` verdict 즉시 적용.
2. **OVERFIT_RISK** — 3-04 OVERFIT_RISK verdict.
3. **STRESS_FAILED** — 3-05 FAIL / WARN.
4. **REJECTED_BY_RISK** — 3-02 / 3-03 통과 못함.
5. **READY_FOR_PAPER** — 모든 단계 통과 (필수 4 단계: 3-02, 3-03, 3-04, 3-05).
6. **NO_CANDIDATE** — 위 어디에도 분류 안 됨 (fallback).

## 6. AI Agent 위험 신호 (`ai_agent_risk_signals`)

10 카테고리 — risk_metrics 기반 *advisory* 만:

- `profit_factor_below_1` — PF < 1 (수익보다 손실이 큼).
- `high_max_drawdown` — MDD ≥ 15%.
- `low_win_rate` — 승률 < 40%.
- `non_positive_expectancy` — 기대값 ≤ 0.
- `long_loss_streak` — 최대 연속 손실 ≥ 5.
- `low_trade_count` — 거래 횟수 < 30.
- `fees_slippage_eliminate_profit` — 수수료/슬리피지 차감 후 적자.
- `walk_forward_overfit_risk` — 3-04 OVERFIT_RISK.
- `stress_test_scenario_fail` — 3-05 시나리오 FAIL.
- `stress_test_scenario_warn` — 3-05 시나리오 WARN.

**중요**: 본 신호는 AI Agent / Strategy Researcher 의 *학습 / 의사결정 컨텍스트
참고용* — RiskManager / OrderGuard 의 자동 차단 트리거로 사용하지
**않습니다**. 자동 차단은 별도 RiskRule (후속 PR + 운영자 명시 옵트인) 에서.

## 7. 절대 invariant (테스트 lock)

| 항목 | 강제 |
|---|---|
| `OperatorReport.is_order_signal=False` | `__post_init__` ValueError |
| `OperatorReport.auto_apply_allowed=False` | `__post_init__` ValueError |
| `OperatorReport.is_live_authorization=False` | `__post_init__` ValueError |
| `OperatorReport.is_investment_advice=False` | `__post_init__` ValueError |
| broker / OrderExecutor / route_order import 0건 | 정적 grep 테스트 |
| 외부 HTTP / AI SDK import 0건 (httpx/requests/anthropic/openai/yfinance) | 정적 grep |
| `app.core.config.get_settings` import 0건 | 정적 grep |
| `settings.enable_*_trading =` mutate 0건 | 정적 grep |
| Markdown 에 "Place Order" / "지금 매수" / "지금 매도" / "실거래 시작" / "ENABLE_LIVE_TRADING 토글" 라벨 0건 | 정적 grep |
| Secret 패턴 0건 (`sk-` / `ghp_` / `Bearer <token>` / `PST<token>`) | 정적 grep |
| `reports/*` gitignore | 별도 테스트 |

## 8. CLAUDE.md 절대 원칙 준수

- ✅ `RiskManager → PermissionGate → OrderExecutor` 흐름 *변경 0건*.
- ✅ 본 모듈은 *read-only* — broker / DB / 외부 API 호출 0건.
- ✅ `is_order_signal=False` 영구 — 본 리포트 결과로 직접 주문 생성 *불가능*.
- ✅ 운영 모드 / 안전 flag default 변경 0건.
- ✅ 모의투자 *시작* 은 운영자가 BotControl / LiveEngine 흐름에서 *명시 수행*.
- ✅ 실거래 활성화는 별도 게이트 (#73 Live Manual Gate 등) + 사용자 명시 옵트인.

## 9. 운영자 검토 흐름 (3-08 이후)

1. `strategy_optimization_report.md` 와 `operator_summary.md` 검토.
2. `overall_status == READY_FOR_PAPER` 이면 `paper_candidates` 목록 검토.
   - 각 후보의 위험 신호 (`risk_signals`) 와 수수료/슬리피지 영향 (§6) 확인.
3. 모의투자(Paper) 에 *수동* 입력 — Paper Auto Loop (#2-01 ~ #2-08).
4. Paper 운용 4 주+ 후 #72 Paper Gate 평가 → #73 Live Manual Gate (별도 단계).
5. `overall_status != READY_FOR_PAPER` 이면 §4·§5 사유 검토 후:
   - 데이터 부족이면 백테스트 기간 확장.
   - 과최적화이면 파라미터 단순화 + Strategy Researcher Agent (#55) 리포트 참조.
   - 스트레스 실패이면 슬리피지 / 거래량 / 변동성 가정 재조정.

## 10. 테스트

```bash
python -m pytest backend/tests/test_strategy_optimization_report.py -q
python -m pytest backend/tests/test_repository_hygiene.py -q
python scripts/security_scan.py
```

테스트 정책:
- 후보 1건 / 0 건 / stress 실패 / overfit / 단계 누락 시나리오 모두 커버.
- 12 필수 섹션 정적 grep — 후보 없는 리포트에도 모든 섹션 존재.
- 금지 라벨 / forbidden import / secret 패턴 / `reports/*` gitignore 검증.
- 파일 작성은 `tmp_path` 에서만 (`reports/` 실제 디렉토리 미작성).
