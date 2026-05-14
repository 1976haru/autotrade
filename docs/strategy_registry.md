# Strategy Registry — 초보자용 메타데이터 (#81)

> 본 문서는 *현재 프로젝트에 실제로 구현된* 6개 매매 전략의 *메타데이터*만
> 정리한다. 새 매매기법을 추가하지 *않으며*, 경쟁 앱의 전략명을 임의로
> 가져오지 *않는다*. 기존 매매 로직 0줄 변경.

---

## 1. 목적

- 코드의 6개 strategy_id (`STRATEGY_REGISTRY`) 에 *초보자용 한글 displayName*
  과 *위험도 / 권장 모드 / 가용 모드* 메타를 추가해 UI/운영자에게 친화 표시.
- 기존 `describe_strategy()` (contract metadata) 위에 *얇은 메타 레이어*.
  기존 `/api/strategies/registry` endpoint 와 호환 유지.
- 운영자 / Agent / 백테스트가 internal id 와 displayName 을 *모두* 알 수 있도록
  표시 — log/audit 매핑 가능.

---

## 2. 실제 확인된 6개 매매기법

`backend/app/strategies/concrete/` 코드의 *실제 구현된* 전략 6개. 다른 것은 없음.

| internal id | 클래스 | UI displayName | beginnerName | 위험도 |
|---|---|---|---|---|
| `sma_crossover` | `SmaCrossoverStrategy` | 단기/장기 이동평균 교차 | 이평선 교차 추세 추종 | 보통 |
| `rsi_reversion` | `RsiReversionStrategy` | RSI 과매도/과매수 회복 | RSI 반등 / 반락 단타 | 보통 |
| `vwap_strategy` | `VWAPStrategy` | VWAP 평균 회귀 | 거래량가중평균 회복 단타 | 보통 |
| `orb_vwap` | `OrbVwapStrategy` | ORB + VWAP 돌파 | 시가 범위(ORB) 돌파 단타 | 높음 |
| `volume_breakout` | `VolumeBreakoutStrategy` | 거래량 급증 돌파 | 거래대금 급증 + 신고가 돌파 단타 | 높음 |
| `pullback_rebreak` | `PullbackRebreakStrategy` | 눌림목 재돌파 | 상승 임펄스 → 거래량 눌림 → 재돌파 단타 | 높음 |

### 가짜 전략명 추가 금지 (영구 invariant)

다음 패턴은 *코드에도 메타에도 없으며 영원히 추가하지 않는다*:
- "골든브릿지" / "트라이앵글 전설" / "다이아 전략" / "퀀텀 점프" / "황금알" /
  "초신성" / "월급쟁이 비밀" / "100% 승률" 등 외부 앱식 자극적 표현.
- 영문 hype 어휘 (`guaranteed` / `magic strategy` / `secret formula` /
  `100% win`).

→ `tests/test_strategy_registry_metadata.py::test_no_competitor_or_fake_strategy_names_present`
로 정적 grep 가드.

---

## 3. 매매기법별 상세 (코드 기준)

본 절은 *코드에 명시된 내용*만 요약. 운영자가 BotControl / LiveEngine 화면에서
사용할 때 참고용.

### 3.1 sma_crossover (이동평균 교차) · 위험도 보통

- **파라미터**: `short=5`, `long=20`
- **매수**: 단기 SMA > 장기 SMA 골든 크로스
- **매도**: 단기 SMA < 장기 SMA 데드 크로스
- **SL/TP**: 코드 명시 없음 (`risk_profile.stop_loss_pct: 2%` 메타만)
- **권장**: 모의투자 검증 후 사용

### 3.2 rsi_reversion (RSI 회복) · 위험도 보통

- **파라미터**: `period=14`, `oversold=30`, `overbought=70`
- **매수**: RSI ≤ 30 → 임계 위 회복 첫 봉
- **매도**: RSI ≥ 70 → 임계 아래 하락 첫 봉
- **SL/TP**: 코드 명시 없음 (메타 2%)
- **권장**: 강한 추세장에서는 신호 품질 저하 — regime 필터 권장

### 3.3 vwap_strategy (VWAP 회귀) · 위험도 보통

- **파라미터**: liquidity / 괴리율 / rolling window 등 10+
- **매수**: VWAP 아래→위 회복 (cross-up edge) + 거래량 증가 + 괴리율 cap 이내
- **매도**: VWAP 하향 cross-down (보유 중일 때) / SL / TP / trailing / time exit
- **SL/TP**: TP **2.5%** / SL **1.5%** / trailing **1%** / time stop **20봉**
- **권장**: 모의 권장. 유동성 부족 / 괴리율 초과 시 자동 차단

### 3.4 orb_vwap (ORB 돌파) · 위험도 높음

- **파라미터**: `orb_bars=6`
- **매수**: 장 시작 후 ORB 형성 → 상단 돌파 + 세션 VWAP 위 마감 (일중 1회)
- **매도**: VWAP 하향 이탈 / ORB 하단 재진입
- **SL/TP**: 코드 명시 없음 (메타 1.5%)
- **권장**: 모의 충분 검증 — 돌파 실패 빈도 높음

### 3.5 volume_breakout (거래량 급증 돌파) · 위험도 높음

- **파라미터**: volume lookback / 배수 / breakout window 등 11+
- **매수**: 거래대금 lookback 평균 × **2.0** 이상 + 최근 고점 돌파 + VWAP 위 (일중 1회)
- **매도**: VWAP 하향 / SL / TP / trailing / time exit
- **SL/TP**: TP **4%** / SL **2%** / trailing **1.5%** / time stop **30봉**
- **권장**: 거래량 급감 시 신호 강도 하락 — 후속 봉 추가 진입 자제

### 3.6 pullback_rebreak (눌림목 재돌파) · 위험도 높음

- **파라미터**: impulse / pullback lookback / volume fade / VWAP 격차 등 30+
- **매수**: impulse 상승 → 거래량 fade 눌림 → 재돌파 (peak 위, VWAP 위, 일중 1회)
- **매도**: pullback_low 이탈 / VWAP 하향 / trailing / SL / TP / time exit
- **SL/TP**: TP **4%** / SL **2% baseline** (pullback_low 기반 동적) / trailing **1.5%** / time **30봉**
- **권장**: 가장 복잡한 파라미터 — 백테스트 검증 충분 후 사용

---

## 4. 운영 모드 매트릭스 (모든 전략 공통)

| Mode | 지원 |
|---|---|
| `SIMULATION` | ✅ |
| `PAPER` | ✅ |
| `LIVE_SHADOW` | ✅ |
| `LIVE_MANUAL_APPROVAL` | ✅ |
| `LIVE_AI_ASSIST` | (별도 ai.assist 흐름 — strategy 가 후보 *생성*만, 실행은 AI Assist 게이트 #74 통과) |
| `LIVE_AI_EXECUTION` | 🛑 영구 차단 (#75) |

**`live_trading_available` 는 모든 전략에 대해 `false` 영구** — 이유:
`KisBrokerAdapter.place_order(is_paper=False)` 가 `NotImplementedError`
(`docs/live_activation_blockers.md` §2 참조). 실주문 라우팅 활성화는 별도
옵트인 PR (#73 + #74 + #75) 통과 필요.

---

## 5. API

### 기존 (변경 없음)
- `GET /api/strategies/registry` — contract metadata (entry / exit /
  invalidation / required_regime / risk_profile / params). 본 PR로 *변경 0건*.

### 신규 (#81)
- `GET /api/strategies/beginner-registry` — 위 contract metadata + 초보자용
  displayName / beginnerName / description / risk_level / recommended_mode /
  supported_modes / backtest_available / paper_trading_available /
  live_trading_available + notes.

응답 invariant (모든 entry):
- `is_order_signal=false`
- `auto_apply_allowed=false`
- `is_investment_advice=false`

---

## 6. UI

`frontend/src/components/tabs/StrategyRegistryCard.jsx`:

- 6개 전략 카드 — displayName + `(internal_id)` *항상 함께 노출*.
- 위험도 배지 (보통/높음, 색상별).
- 가용 칩: 백테스트 / 모의투자 / 실전투자 (실전투자는 모두 비활성 표시).
- 권장 모드 + 일반 보유 시간 가이드.
- 세부 정보 (펼치기): 매수 / 매도 규칙 / 무효화 / 요구 regime / 파라미터 /
  운영 노트 / 모드 매트릭스.
- *"전략 활성화 / 비활성화 / 파라미터 적용 / 주문 실행" 라벨 버튼 0개*.
- 본 카드 disclaimer: "메타데이터 표시만 합니다. 운영은 BotControl /
  LiveEngine 에서 진행합니다."

---

## 7. 절대 원칙 — 본 모듈 강제

`tests/test_strategy_registry_metadata.py` 의 정적 grep + runtime 가드:

1. STRATEGY_REGISTRY 와 beginner_metadata 가 1:1 — 가짜 strategy_id 추가 시
   `validate_metadata()` 가 즉시 검출.
2. broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` /
   `app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` import 0건.
3. `broker.place_order(` / `route_order(` / `OrderExecutor(` /
   `submit_candidate(` / `AiClient(` 호출 0건.
4. `STRATEGY_REGISTRY[ ... ] = ...` / `.pop(` / `.update(` mutation 0건 — 본
   모듈은 *읽기만*.
5. `settings.enable_*_trading =` mutate 0건.
6. DB write 0건.
7. UI 카드 "전략 활성화" / "Apply Parameters" / "전략 시작" / "주문 실행" /
   "ENABLE_*" 라벨 버튼 0개 (frontend 테스트로 lock).
8. UI / 응답 / 메타 텍스트에 가짜 전략명 (`골든브릿지` / `트라이앵글 전설` /
   `다이아 전략` / `퀀텀 점프` / `황금알` / `100% 승률` / `guaranteed` /
   `magic strategy` 등) 0건 (테스트로 lock).
9. live_trading_available 모든 전략 false (KIS 실주문 미구현 상태와 일관).

---

## 8. UI 적용 현황 (#82, #83)

#81 메타데이터가 다음 UI 컴포넌트에 *적용*되었습니다. 모든 위치에서
`displayName + (internal_id)` *함께* 노출 — 운영자 / 로그 / audit 매핑 보존.

### 8.1 #82 (1차)

| 컴포넌트 | 위치 | 변환 전 | 변환 후 |
|---|---|---|---|
| `OrderAuditRow` (감사 로그 행 strategy 배지) | `AuditLog.jsx:130` | `sma_crossover` | **단기/장기 이동평균 교차** `(sma_crossover)` |
| `BacktestStrategyMiniTable` 전략 셀 | `AuditLog.jsx:1354` | `sma_crossover` | **단기/장기 이동평균 교차** `(sma_crossover)` |
| `BacktestExtremesSummary` best/worst | `AuditLog.jsx:1300` | `sma_crossover` | **단기/장기 이동평균 교차** `(sma_crossover)` |
| `ScoreboardCard` 누적 성과 행 | `LiveEngine.jsx:130` | `sma_crossover` | **단기/장기 이동평균 교차** `(sma_crossover)` |
| `StatusCard` 엔진 상태 "전략" 필드 | `LiveEngine.jsx:47` | `sma_crossover` | **단기/장기 이동평균 교차** `(sma_crossover)` |
| `AgentStatsCard` per-strategy 행 | `AgentStatsCard.jsx:169` | `sma_crossover` | **단기/장기 이동평균 교차** `(sma_crossover)` |

### 8.2 #83 (2차 — 결재 / Agent Memory / Execution Recommender)

| 컴포넌트 | 위치 | 변환 전 | 변환 후 |
|---|---|---|---|
| `_OrderSummary` AI hero 줄 | `Approvals.jsx:329` | `· sma_crossover` | `· `**`단기/장기 이동평균 교차`**` (sma_crossover)` |
| `ApprovalProposalSummary` strategy chip | `ApprovalQueue.jsx:124` | `sma_crossover` | **단기/장기 이동평균 교차** `(sma_crossover)` |
| `ApproveConfirmSummary` strategy 줄 | `ApprovalQueue.jsx:409` | `· sma_crossover` | `· `**`단기/장기 이동평균 교차`**` (sma_crossover)` |
| `_MemoryRow` / `_MemoryDetail` strategy 배지 | `AgentMemoryCard.jsx:56, 120` | `orb_vwap` | **ORB + VWAP 돌파** `(orb_vwap)` |
| `_ProposalRow` 전략 필드 | `ExecutionRecommenderCard.jsx:51` | `rsi_reversion` | **RSI 과매도/과매수 회복** `(rsi_reversion)` |

### 8.3 공통 helper / hook

공통 helper: `frontend/src/utils/strategyNames.js`
- `formatStrategyName(id, lookup)` — `"displayName (internal_id)"` 반환
- `strategyDisplayShort(id, lookup)` — `"displayName"` 만
- `useStrategyDisplayNames()` hook — module-level 캐시 (한 번 fetch, 모든 컴포넌트
  공유). state 1개(`lookup`)만 노출 — fetch 실패는 graceful fallback(internal id)
  로 처리, error/loading 은 컴포넌트 re-render 를 유발하지 않음 (#83 — 200+
  pending row 동시 mount 환경에서 re-render 폭발 방지).
- 모두 *graceful fallback* — lookup 부재 시 internal id 그대로

각 컴포넌트 행/배지에 `data-internal-id="<strategy_id>"` 속성을 carry — 테스트
selector / audit 자동화에서 internal id 로 매핑 가능.

## 9. 후속 backlog

- 운영자가 직접 UI 에서 beginner metadata 편집 (현재는 코드에 hard-coded)
- displayName 다국어 (영문 fallback)
- 전략별 backtest 결과 미니 카드 통합
- 운영 노트 영구화 (DB 테이블)
- displayName 변경 이력 audit
- `recommended_mode` 가 실제 운영 결과에 따라 자동 조정 (Strategy Researcher
  #55 연계)
- 본 메타 + Alpha Decay (#77) 결합 — DISABLE_CANDIDATE 전략의 displayName
  옆에 *비활성 후보* 배지

---

## 9. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 9개 절대 원칙 (특히 5: AI는 broker 직접 호출 X)
- [`docs/strategy_contract.md`](strategy_contract.md) — #28 StrategyBase contract
- [`docs/strategies.md`](strategies.md) — 전략 카탈로그
- [`docs/strategy_promotion_gate.md`](strategy_promotion_gate.md) — #27 Promotion
- [`docs/backtest_policy.md`](backtest_policy.md) — 백테스트 기준
- [`docs/paper_mode.md`](paper_mode.md) / [`docs/shadow_mode.md`](shadow_mode.md)
- [`docs/live_activation_blockers.md`](live_activation_blockers.md) — KIS 실주문 미구현 매트릭스
- [`docs/alpha_decay_monitor.md`](alpha_decay_monitor.md) — #77 전략 알파 감쇠
