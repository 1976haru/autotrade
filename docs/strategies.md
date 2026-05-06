# 전략 명세 (Strategy Contract)

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

### 3. `rsi_reversion` — RSI 평균회귀

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

- HTTP: `GET /api/strategies/scoreboard` → 위 항목의 list. 정렬: `total_pnl + live_pnl` desc — backtest로는 좋은데 live에서는 손실인 전략이 즉시 발견되도록.
- Frontend: `<ScoreboardCard>` (LiveEngine 탭 하단) — 7-column table (전략 / runs / BT PnL / BT 승률 / live trades / live PnL / live 승률) + 새로고침 + 빈/에러 상태 처리.

페어매칭 알고리즘 (144):
- `(strategy, symbol)` 키별 BUY 잔량 deque로 FIFO 추적.
- BUY 행 → `(qty, fill_price)`를 큐 뒤에 push.
- SELL 행 → 큐 앞에서 BUY 잔량을 차감, 부분 PnL = `(sell_price - buy_price) * 매칭수량` 누적. 매칭이 발생한 SELL은 trade 1건으로 카운트.
- naked SELL(잔량 BUY 없음)은 noise로 무시 — 운영 사고 시 audit log에 그대로 보존되어 별도 분석 가능.
- leftover BUY는 open position으로 unrealized — 집계 X.

117 frontend `BacktestStrategyMiniTable`은 view-time filtered 데이터의 mini 집계, 137/144는 운영자가 신뢰할 수 있는 *서버 단일 진실*. 117은 즉각 reactive, 137/144는 누적.
