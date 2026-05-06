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

### 2. `orb_vwap` — Opening Range Breakout + VWAP

| 필드 | 값 |
|---|---|
| 클래스 | `OrbVwapStrategy` |
| 상태 | 🔧 stub (on_bar = HOLD, TODO 표시) |
| 진입 | 오프닝 ORB 윈도우(기본 30분) 형성 후, 마감이 ORB 상단 돌파 + VWAP 위 |
| 청산 | 익일 종가 / ORB 중간선 회귀 / VWAP 하향 이탈 중 가장 빠른 시점 |
| 무효화 | VWAP 하향 이탈 후 5분 내 회복 실패, ORB 하단 재진입 |
| 시장 체제 | `trending_up` |
| 권장 리스크 | 자본 5% / -1.5% 손절 / 동시 2종목 |
| 파라미터 | `orb_minutes=30`, `vwap_window=60` |

> ⚠️ 현재 stub. 실제 ORB/VWAP 계산 + 돌파 판정은 미구현 — `on_bar`는 항상 HOLD를 반환하여 자동매매 안전성에 영향 없음. 별도 PR에서 구현 예정 (`TODO(131-followup)` 주석 참조).

### 3. `rsi_reversion` — RSI 평균회귀

| 필드 | 값 |
|---|---|
| 클래스 | `RsiReversionStrategy` |
| 상태 | 🔧 stub (on_bar = HOLD, TODO 표시) |
| 진입 | RSI(14)가 oversold(≤30) → 임계 위로 회복하는 첫 봉에서 BUY |
| 청산 | RSI 50 회복 또는 overbought(≥70)에서 임계 아래로 하락 |
| 무효화 | 강한 추세 형성으로 RSI가 임계 영역을 5봉 이상 유지 (mean-reversion 가설 깨짐) |
| 시장 체제 | `ranging` |
| 권장 리스크 | 자본 3% / -2% 손절 / 동시 2종목 (추세장 휘말림 보수적) |
| 파라미터 | `period=14`, `oversold=30`, `overbought=70` |

> ⚠️ 현재 stub. 실제 RSI 계산은 미구현 — `on_bar`는 항상 HOLD. 별도 PR에서 구현 예정.

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
