# 전략 명세 (Strategy Contract)

> 가상 자동매매 시스템 전체 구조는 [`docs/virtual_trading_architecture.md`](virtual_trading_architecture.md) 참조.

각 전략은 단순한 시그널 생산자가 아니라 운영자/감사가 사후 분석할 수 있는 contract를 갖는다.
[`backend/app/strategies/base.py`](../backend/app/strategies/base.py)의 `Strategy` ABC가 다음을 class-level attribute로 강제한다:

| 필드 | 의미 | 예시 |
|---|---|---|
| `entry` | 진입 조건 (사람이 읽는 한 줄 설명) | `"단기 SMA가 장기 SMA를 상향 돌파한 봉의 마감에서 BUY"` |
| `exit` | 청산 조건 — TP / 시그널 반전 / 시간 종료 | `"단기 SMA가 장기 SMA를 하향 돌파한 봉의 마감에서 SELL"` |
| `invalidation` | "이 신호가 더 이상 유효하지 않다" 기준 | `"추세 전환 또는 운영자 수동 해제"` |
| `required_regime` | 시장 체제 hint — `trending` / `trending_up` / `trending_down` / `ranging` / `high_vol` / `any` | `"trending"` |
| `risk_profile` | RiskManager가 직접 적용하는 limit이 아닌 권장 위험 범위 | `{"position_size_pct": 5, "stop_loss_pct": 2, "max_concurrent": 1}` |

미작성 시 base.py의 default(`""` / `"any"` / `{}`)가 그대로 응답에 surface되어 운영자가 "미완성 신호"로 인지한다.

**170 강제**: `build_strategy()` 호출 시 default contract가 그대로면 `StrategyContractError` raise. `enforce_contract=False`로 의도적 우회 가능 (백테스트 / 검증 흐름). 검사 항목: `entry`/`exit`/`invalidation` 비어있음, `required_regime="any"` (구체값 강제), `risk_profile={}` 빈 dict.

`describe_strategy()` / `describe_all_strategies()` (concrete/__init__.py)와 `GET /api/strategies/registry` (routes_live_engine.py) 응답에 그대로 담긴다.

---

## 등록된 전략

### 1. `sma_crossover` — 이동평균 교차

| 필드 | 값 |
|---|---|
| 클래스 | `SmaCrossoverStrategy` |
| 상태 | ✅ 구현 완료 |
| 진입 | 단기 SMA가 장기 SMA를 상향 돌파한 봉의 마감에서 BUY |
| 청산 | 단기 SMA가 장기 SMA를 하향 돌파한 봉의 마감에서 SELL |
| 무효화 | 추세 전환(반대 cross) 또는 운영자 수동 해제 |
| 시장 체제 | `trending` (횡보장 whipsaw 위험) |
| 권장 리스크 | 자본 5% 노출 / 진입가 -2% 손절 / 동시 1종목 |
| 파라미터 | `short=5`, `long=20` |

### 2. `orb_vwap` — Opening Range Breakout + 세션 VWAP

| 필드 | 값 |
|---|---|
| 클래스 | `OrbVwapStrategy` |
| 상태 | ✅ 구현 완료 (142) |
| 진입 | 당일 첫 N봉으로 형성된 ORB 상단을 돌파 + 동시에 세션 VWAP 위에서 마감하는 첫 봉 |
| 청산 | VWAP 하향 이탈 또는 ORB 하단 재진입에서 SELL (세션 종료 시 운영자 청산 권장) |
| 무효화 | VWAP 하향 이탈이 5봉 이상 회복 실패하거나 ORB 하단 재진입 후 추세 무효화 |
| 시장 체제 | `trending_up` |
| 권장 리스크 | 자본 5% / -1.5% 손절 / 동시 2종목 |
| 파라미터 | `orb_bars=6` (기본 — 5분봉 6개 ≈ 30분 ORB) |

**구현 노트:**
- ORB는 *bar count* 단위로 받는다 — `Strategy.on_bar(bars)` 인터페이스가 봉 단위라 분 단위는 봉 간격을 가정해야 한다. 운영자가 데이터 봉 간격을 알고 직접 환산해서 넘긴다.
- VWAP은 *세션 누적* (typical price = (h+l+c)/3, 거래량 가중) — 거래일이 바뀌면 reset.
- 거래일은 `bar.timestamp.date()`로 구분 (timezone-aware/naive 모두 동작).
- 일중 진입 신호는 한 번만 — 같은 날 두 번째 cross-up은 발화하지 않는다.
- 거래량 0 세션은 VWAP 정의 불가 → 안전 측 HOLD.

### 3. `volume_breakout` — 거래대금 돌파 (#29)

| 필드 | 값 |
|---|---|
| 클래스 | `VolumeBreakoutStrategy` |
| 상태 | ✅ 구현 완료 (#29) |
| 진입 | 거래대금이 lookback 평균 × N배 이상 + 최근 N봉 종가 고점 돌파 + 세션 VWAP 상단 정렬 |
| 청산 | TP 4% / SL 2% / trailing 1.5% / 30봉 시간 청산 또는 VWAP 하향 이탈 |
| 무효화 | stale data, blocked regime, VWAP 격차 과다, 세션 시가 대비 runup 과다 |
| 시장 체제 | `trending_up` (NEWS_DRIVEN / GAP_DAY 권장) — 차단: `trending_down`, `high_vol`, `blocked` |
| 권장 리스크 | 자본 4% / -2% 손절 / +4% 익절 / trailing 1.5% / 동시 1종목 |
| 파라미터 | `min_bars_required=25`, `volume_lookback_bars=20`, `volume_multiplier=2.0`, `breakout_lookback_bars=20`, `max_vwap_distance_pct=3.0`, `max_intraday_runup_pct=8.0`, `open_cooldown_bars=5`, `stale_max_age_seconds=60` |

**구현 노트** (자세한 내역: [`docs/strategies/volume_breakout.md`](strategies/volume_breakout.md)):
- 거래대금(turnover) = close × volume — 종목 간/시점 간 비교 안정화.
- lookback window는 *현재 봉 제외* — 자기 자신을 baseline으로 삼지 않음.
- VWAP은 *세션 누적* — 거래일이 바뀌면 reset (ORB와 동일 정의).
- 추격 가드 2종 — VWAP 격차 + 세션 시가 대비 runup. 임계의 70% 초과 영역에서는 BUY로 가더라도 `risk_notes`와 sizing 축소가 적용된다.
- REJECT vs NO_SIGNAL — `SignalAction`에 REJECT enum이 없으므로 `action=NO_SIGNAL` + `indicators.decision_kind="REJECT"`로 표시. 안전 차단(stale/blocked regime/runup/추격)과 단순 무신호를 운영자/감사가 구분 가능.
- 일중 진입 신호는 한 번만 — 같은 날 두 번째 합성 충족은 `_fired_today`로 차단.
- broker / risk / permission / execution 어떤 모듈도 import하지 않음 (테스트 `test_strategy_does_not_import_broker_or_risk` 가드).

### 4. `pullback_rebreak` — 눌림목 재돌파 (#30, 2차 전략)

| 필드 | 값 |
|---|---|
| 클래스 | `PullbackRebreakStrategy` |
| 상태 | ✅ 구현 완료 (#30) |
| 진입 | 1차 상승 impulse + 거래량 fade 눌림 + 현재 봉이 impulse peak를 재돌파 + 재돌파 거래량 증가 |
| 청산 | pullback_low 이탈 / VWAP 이탈 / TP 4% / SL 동적 (pullback_low 기반) / trailing 1.5% / 30봉 시간 청산 |
| 무효화 | 깊은 눌림(pullback_max_pct 초과), 거래량 급증 하락, VWAP 이탈, stale data, blocked regime |
| 시장 체제 | `trending_up` (NEWS_DRIVEN / GAP_DAY 권장) — 차단: `trending_down`, `high_vol`, `blocked` |
| 권장 리스크 | 자본 5% / -2% baseline 손절(동적) / +4% 익절 / trailing 1.5% / 동시 1종목 |
| 파라미터 | `min_bars_required=30`, `impulse_lookback_bars=12`, `pullback_lookback_bars=10`, `min_impulse_pct=1.5`, `max_impulse_pct=12.0`, `pullback_min_pct=0.3`, `pullback_max_pct=4.0`, `pullback_volume_fade_ratio=0.85`, `rebreak_volume_min_ratio=1.2`, `max_vwap_distance_pct=4.0`, `max_intraday_runup_pct=12.0`, `open_cooldown_bars=5`, `stop_loss_below_pullback_low_pct=1.0` |

**구현 노트** (자세한 내역: [`docs/strategies/pullback_rebreak.md`](strategies/pullback_rebreak.md)):
- 구조: `impulse_low → peak → pullback_low → 현재(rebreak)`. 각 인덱스는 현재 봉을 *제외한* lookback 윈도우에서 argmax/argmin으로 결정.
- 거래량 비교는 `volume`이 아니라 `close × volume`(turnover) — 종목/시점 간 비교 안정화.
- VWAP은 *세션 누적* — 거래일이 바뀌면 reset.
- 추격 가드 4종 — `max_impulse_pct` (impulse 강도), `pullback_max_pct` (눌림 깊이), `max_vwap_distance_pct` (VWAP 격차), `max_intraday_runup_pct` (당일 누적 상승). 임계의 70% 초과 영역은 BUY로 가더라도 `risk_notes` + sizing 축소 자동.
- 손절은 *동적* — `position_context.pullback_low_close + current_close`가 주어지면 pullback_low 기반 stop_price를 산출하고 entry 대비 % 산출. 미제공 시 baseline 사용.
- 일중 1회 진입은 패턴 검출 *전*에 가드 — 같은 날 새 구조가 형성돼도 무의미한 계산을 피하고 운영자 동선에서 "이미 한 번 발화" 사실을 우선 surface.
- VolumeBreakoutStrategy(#29)와의 보완 — VB는 1차 첫 돌파, PullbackRebreak는 그 다음 안전한 진입 후보. 동일 종목 중복 신호는 operator UI에서 dedup 권장.
- broker / risk / permission / execution / governance 어떤 모듈도 import하지 않음 (테스트 `test_strategy_does_not_import_broker_or_risk` 가드).

### 5. `vwap_strategy` — VWAP 회귀/이탈 (#31, 보조 전략)

| 필드 | 값 |
|---|---|
| 클래스 | `VWAPStrategy` |
| 상태 | ✅ 구현 완료 (#31) |
| 진입 | 직전 봉 ≤ VWAP, 현재 봉 > VWAP (cross-up reclaim) + 거래량 ≥ prior 평균 × 1.2 + 거래량/거래대금 임계 통과 + 괴리율 entry cap 이내 |
| 청산 | EXIT 신호 (cross-down VWAP 이탈, 보유 중일 때) + TP 2.5% / SL 1.5% / trailing 1% / 20봉 시간 청산 |
| 무효화 | LOW_LIQUIDITY, blocked regime, stale data, 과도한 VWAP 이격(`overextension_deviation_pct` 초과) |
| 시장 체제 | `trending_up` / `ranging` 권장 — 차단: `trending_down`, `high_vol`, `blocked` |
| 권장 리스크 | 자본 3% / -1.5% 손절 / +2.5% 익절 / trailing 1% / 동시 1종목 (보조 전략 보수적) |
| 파라미터 | `min_bars_required=25`, `rolling_vwap_window=20`, `liquidity_window=20`, `min_avg_volume=100`, `min_avg_turnover=0`, `max_deviation_pct_for_entry=1.5`, `overextension_deviation_pct=3.0`, `reclaim_volume_min_ratio=1.2`, `open_cooldown_bars=5`, `stale_max_age_seconds=60` |

**구현 노트** (자세한 명세: [`docs/strategies/vwap_strategy.md`](strategies/vwap_strategy.md)):
- VWAP 계산 유틸은 [`backend/app/strategies/vwap.py`](../backend/app/strategies/vwap.py)에 분리 — `typical_price`, `vwap_of`, `extract_session_bars`, `session_vwap`, `rolling_vwap`, `vwap_deviation_pct`, `average_volume`, `average_turnover`, `check_liquidity`. 본 전략 + 향후 다른 전략/Agent가 재사용.
- session_vwap = 1차 기준선, rolling_vwap(20봉) = 보조 — 둘 다 indicators에 carry.
- `check_liquidity`로 거래량 적은 종목의 VWAP 왜곡 방어 — `min_avg_volume`/`min_avg_turnover` 임계 미만이면 LOW_LIQUIDITY REJECT.
- EXIT 신호는 *보유 중일 때만* 발화 — `position_context.has_open_position=True`를 context.extra에 넘겨야 함. 보유 정보 없으면 EXIT 미발화.
- 추격 가드 2-tier — `max_deviation_pct_for_entry=1.5%` (reclaim 후 BUY 보류 cap), `overextension_deviation_pct=3%` (REJECT cap).
- 일중 1회 진입 — `_fired_today` invariant.
- 기존 `OrbVwapStrategy`(orb_vwap.py)는 자체 VWAP 누적을 인라인으로 가지고 있고 본 모듈을 import하지 않음 — 기존 동작 보존이 우선이라 추후 통합 PR에서 정리.
- broker / risk / permission / execution / governance 어떤 모듈도 import하지 않음 (테스트 가드).

### 6. `rsi_reversion` — RSI 평균회귀

| 필드 | 값 |
|---|---|
| 클래스 | `RsiReversionStrategy` |
| 상태 | ✅ 구현 완료 (142) |
| 진입 | RSI(14)가 oversold(≤30) 영역에서 임계 위로 회복되는 첫 봉에서 BUY |
| 청산 | RSI가 overbought(≥70)에서 임계 아래로 하락하는 첫 봉에서 SELL |
| 무효화 | 강한 추세 형성으로 RSI가 임계 영역을 5봉 이상 유지 (mean-reversion 가설 깨짐) |
| 시장 체제 | `ranging` |
| 권장 리스크 | 자본 3% / -2% 손절 / 동시 2종목 (추세장 휘말림 보수적) |
| 파라미터 | `period=14`, `oversold=30`, `overbought=70` |

**구현 노트:**
- RSI는 표준 정의 — 직전 `period`개 봉의 평균 상승폭 / 평균 하락폭. Wilder의 지수 평활화 대신 단순 평균을 사용 (결정적이고 테스트 가능).
- avg_loss = 0이면 RSI = 100 (overbought 진입). 그 상태에서 하락이 시작돼야 SELL이 나온다.
- 첫 RSI 산출에 `period + 1`개의 봉이 필요 — 그 전까지는 HOLD.
- cross-back 감지 (직전 RSI ≤ oversold AND 현재 RSI > oversold → BUY) — `_prev_rsi`로 상태 추적.

---

## 운영자 사용 가이드

### 신규 전략 추가

1. `backend/app/strategies/concrete/<name>.py`에 `Strategy` 상속 클래스 작성
2. **반드시** 다음 metadata를 class-level로 선언:
   - `entry` / `exit` / `invalidation` (모두 비어 있지 않은 문자열)
   - `required_regime` (any가 아닌 구체값 권장)
   - `risk_profile` (최소 `position_size_pct`)
3. `concrete/__init__.py`의 `STRATEGY_REGISTRY`에 등록
4. `backend/tests/test_strategy_registry.py`에 contract 검증 테스트 추가
5. 본 문서에 한 섹션 추가

### contract 미작성 시 처리

`describe_strategy()`는 base.py의 default를 그대로 반환한다. 운영자는 frontend Strategies 탭 또는 `/api/strategies/registry` JSON에서 빈 `entry`/`exit`/`invalidation`을 보고 "이 전략은 contract가 미작성"임을 즉시 인지할 수 있다.

CLAUDE.md의 "수익률보다 손실 방어와 감사 로그"와 같은 맥락 — 전략 자체도 **운영자가 읽을 수 있는 형태로 둔다**가 원칙.

### Risk profile vs RiskManager

`risk_profile`은 **권장값**이지 RiskManager가 강제하는 limit이 아니다. RiskManager는 [`docs/risk_policy.md`](risk_policy.md)에 따라 `max_order_notional`, `max_daily_loss`, `max_positions`, `max_symbol_exposure`를 강제하며, 전략이 권장하는 `position_size_pct`는 운영자가 주문 수량을 결정할 때의 hint로 사용된다.

향후 Strategy Scoreboard 또는 자동 사이즈 결정 모듈이 도입되면 이 metadata를 직접 소비하도록 확장 예정.

---

## Market Regime advisory (135)

[`backend/app/market/regime.py`](../backend/app/market/regime.py)의 `classify_regime(bars)` 휴리스틱이 현재 누적 봉을 기반으로 시장 체제를 추정한다. 분류 결과는 `Strategy.required_regime`과 동일한 어휘를 사용:

| 분류값 | 의미 | 매칭되는 `required_regime` |
|---|---|---|
| `any` | 데이터 부족(<20봉) | `any` 또는 빈 값 |
| `trending_up` | 단기 SMA(20)가 장기 SMA(60)보다 +0.5% 이상 + 변동성 보통 | `trending_up`, `trending`, `any` |
| `trending_down` | 단기 SMA가 장기 SMA보다 -0.5% 이하 + 변동성 보통 | `trending_down`, `trending`, `any` |
| `ranging` | SMA gap < 0.5% + 변동성 보통 | `ranging`, `any` |
| `high_vol` | 표준편차/평균 비율(CV) ≥ 1.5% | `high_vol`, `any` |

`LiveStrategyEngine.current_regime` / `regime_matches_strategy` 속성으로 접근하며, `GET /api/strategies/status` 응답에 두 필드 포함. Frontend `<RegimeIndicator>`(LiveEngine 탭)가 매칭 시 청록, 불일치 시 amber + ⚠ 경고로 surface.

**Advisory only** — 신호를 자동 차단하지 **않는다**. 운영자가 final decision-maker라는 CLAUDE.md 원칙과 일치. 분류기가 오판해도 전략 신호는 그대로 흐르며 운영자가 확인할 수 있도록 UI에 신호 등을 함께 노출한다.

임계값(`_TRENDING_GAP_PCT = 0.5`, `_HIGH_VOL_CV_PCT = 1.5`)은 한국 분봉 KOSPI 종목 가정 — 운영 환경에서 백테스트로 튜닝 권장.

---

## Signal Quality (136)

[`backend/app/strategies/quality.py`](../backend/app/strategies/quality.py)의 `signal_quality(bars, signal, regime_matches)`가 BUY/SELL 신호의 강도/신뢰도를 0-100 두 축으로 점수화한다. AI confluence score와는 별개 — 시스템적 신호의 자체 평가.

| 축 | 의미 | 계산 |
|---|---|---|
| `strength` | 신호의 강도 (cross의 폭, 추세 가파름) | SMA gap percent × 50 (1% gap → 50, 2%+ → 100). HOLD는 0 |
| `confidence` | 신호 신뢰 컨텍스트 | 봉수(60% 비중) + regime 매칭(25%) + 변동성 안정(15%) |

`LiveStrategyEngine.run_tick`이 매 tick마다 계산해 `TickResult.quality` (`{strength, confidence}`)에 포함. `POST /api/strategies/tick` 응답에 노출되며, frontend `<SignalQualityBadge>`(LiveEngine 탭 ResultCard)가 두 mini-bar로 표시 — 70+ 초록, 40-69 amber, < 40 red.

**Advisory only** — 점수가 낮아도 신호 자체는 그대로 흐른다. 운영자가 진입 결정 시 추가 컨텍스트로 사용한다 (CLAUDE.md '손실 방어와 감사 로그 우선').

**139 진행**: `OrderAuditLog`에 `signal_strength` / `signal_confidence` 두 정수 컬럼(0-100)이 추가되어 quality가 영구화. `LiveStrategyEngine.run_tick`은 같은 quality 값을 (a) `TickResult.quality`에 노출하고 (b) `OrderRequest.signal_strength/signal_confidence`로 carry — view-time에 보이는 점수가 audit row의 점수와 일치. `/api/audit/orders` 응답에도 두 필드 노출, frontend `<OrderAuditRow>`가 `quality 80/60` 형태로 inline 표시.

향후 확장: Strategy Scoreboard에서 quality와 실제 PnL 상관관계 추적, AI confluence score(004)와 통합 비교.

---

## Strategy Scoreboard (137 + 144 + 147)

[`backend/app/strategies/scoreboard.py`](../backend/app/strategies/scoreboard.py)의 `compute_strategy_scoreboard(db)`가 두 출처를 strategy별로 누적 집계:

1. **Backtest** (137): `BacktestRun` 행. 운영자가 검증용으로 돌린 backtest의 누적.
2. **Live** (144): `OrderAuditLog`의 `executed=True` + `strategy is not None` 행을 (strategy, symbol)별 BUY/SELL FIFO 페어매칭하여 realized PnL로 환산. open position(잔여 BUY)은 unrealized라 집계 X.

| 응답 필드 | 출처 | 의미 |
|---|---|---|
| `strategy` | — | 전략 이름 (빈 strategy는 `(unknown)`) |
| `runs` | backtest | 누적 backtest run 수 |
| `total_pnl` | backtest | 누적 손익 |
| `avg_pnl` | backtest | run당 평균 |
| `best_pnl` / `worst_pnl` | backtest | 최대/최소 단일 run pnl |
| `wins` / `losses` | backtest | 누적 승/패 거래 수 |
| `win_rate` | backtest | wins / (wins + losses), trades=0이면 0.0 |
| `live_trades` | live | 페어매칭으로 청산된 거래 수 (open position 제외) |
| `live_pnl` | live | 청산 거래의 realized PnL 합 |
| `live_wins` / `live_losses` | live | PnL > 0이면 win, ≤ 0이면 loss (본전은 loss로 분류 — backtest와 동일) |
| `live_win_rate` | live | live_wins / live_trades |
| `expectancy` | backtest | (gross_win - gross_loss) / num_trades, 거래당 평균 PnL (147) |
| `profit_factor` | backtest | gross_win / gross_loss. gross_loss=0이면 None (147) |
| `avg_hold_time_seconds` | backtest | trades_json의 (exit_ts - entry_ts) 평균 초 (147) |
| `max_consecutive_loss` | backtest | 모든 run에서의 최대 연속 손실 거래 수 (147) |
| `approved_orders` / `rejected_orders` / `pending_orders` | audit | strategy 단위 decision 분포 (147) |
| `approval_rate` / `rejection_rate` | audit | approved/rejected를 (approved+rejected) 분모로 (NEEDS_APPROVAL 제외) (147) |
| `runs_by_data_source` | backtest | `{"market": int, "bars": int, ...}` data_source 분포 — LIVE 승격 결정 시 'market' 비율 인지 (173) |

- HTTP: `GET /api/strategies/scoreboard` → 위 항목의 list. 정렬: `total_pnl + live_pnl` desc — backtest로는 좋은데 live에서는 손실인 전략이 즉시 발견되도록.
- Frontend: `<ScoreboardCard>` (LiveEngine 탭 하단) — 7-column table (전략 / runs / BT PnL / BT 승률 / live trades / live PnL / live 승률) + 새로고침 + 빈/에러 상태 처리.

페어매칭 알고리즘 (144):
- `(strategy, symbol)` 키별 BUY 잔량 deque로 FIFO 추적.
- BUY 행 → `(qty, fill_price)`를 큐 뒤에 push.
- SELL 행 → 큐 앞에서 BUY 잔량을 차감, 부분 PnL = `(sell_price - buy_price) * 매칭수량` 누적. 매칭이 발생한 SELL은 trade 1건으로 카운트.
- naked SELL(잔량 BUY 없음)은 noise로 무시 — 운영 사고 시 audit log에 그대로 보존되어 별도 분석 가능.
- leftover BUY는 open position으로 unrealized — 집계 X.

117 frontend `BacktestStrategyMiniTable`은 view-time filtered 데이터의 mini 집계, 137/144는 운영자가 신뢰할 수 있는 *서버 단일 진실*. 117은 즉각 reactive, 137/144는 누적.
