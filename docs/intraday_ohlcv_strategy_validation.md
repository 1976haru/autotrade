# 분봉(intraday) 데이터 기반 단타 전략 검증 (INTRADAY-DATA-01)

> ORB / VWAP / Momentum / Gap / Agent Council 은 **장중 단타** 전략이므로 *일봉이 아니라
> 분봉* 으로 검증해야 한다. **자동 적용 / 실전 전환 승인 / 주문 신호가 아니다. 수익을
> 보장하지 않는다.**

## 1. 왜 일봉으로 ORB/VWAP 단타 전략을 평가할 수 없는가

REAL-DATA-INPUT-01 §0 에서 확인된 사실: 실제 KOSPI **일봉** 244거래일로 검증하면 10종목
모두 **total_trades = 0** 이 나온다. 이유는 전략의 **시간프레임**:

- **ORB(Opening Range Breakout)**: 장 시작 후 *N분* 의 고/저 범위를 돌파할 때 진입 →
  *분(minute)* 단위 구조가 필요. 일봉에서는 "첫 3봉=첫 3일" 이 되어 의미가 없다.
- **VWAP**: *당일 누적* 거래량가중평균 기준 이탈/회귀 → 하루 안의 분봉이 있어야 계산.
- **Gap / Momentum**: 장중 급변 micro-structure 필요.

→ 일봉은 단타 전략의 *올바른 입력이 아니다*. **분봉이 필요**하다.

## 2. 분봉 데이터가 필요한 이유 (검증)

동일 전략을 분봉(5분봉, KST 09:00~15:30)으로 검증하면 진입이 **정상 발생**한다.
저장소의 clean 분봉 fixture(5종목 × 12거래일 × 78봉/일) 기준 council BUY 가
수백 건 발생 → 비로소 승률/PF/expectancy/Walk-forward 를 측정할 수 있다.

## 3. 분봉 CSV 스키마

```
symbol,timestamp,open,high,low,close,volume        # timestamp 는 분 단위 KST datetime
005930,2024-03-04T09:00:00+09:00,71000,71090,70950,71050,250000
005930,2024-03-04T09:05:00+09:00,71050,71140,71010,71100,240000
```
- `timestamp`: ISO datetime(분 단위, KST `+09:00` 권장), 오름차순.
- 저장: 실데이터 `data/market/intraday/{symbol}.csv` (**gitignore**), 테스트 fixture
  `backend/tests/fixtures/intraday_clean/`.

## 4. 분봉 품질검증 기준 (`check_intraday_quality`)

- **FAIL**: OHLC 무결성 위반 / timestamp 파싱 과다 실패 / **일중 평균 bar < 5
  (= 분봉이 아니라 일봉)**.
- **WARN**: 중복 timestamp / 장중(09:00~15:30) 밖 bar / bar < 100 / 거래일 < 5.
- **PASS**: 무결 + 분봉 감지 + 표본 충분.
- `bar_size_minutes`(연속 bar 간 중앙값) / `bars_per_day` / `intraday_detected` carry.
- 잘못된 row 는 `sanitize_ohlcv_bars` 로 *드롭*(값 보정 아님), 과다(>5%)면 FAIL.

## 5. KIS 분봉 API

`kis_intraday_supported() == False` — KIS Client 는 현재가/잔고/당일체결만 제공하며 분봉
시세 collector 는 미구현. 본 작업은 **분봉 CSV 입력** 으로 검증하며 KIS 주문 API 는 호출하지
않는다. (KIS 분봉 read-only 수집은 후속 과제.)

## 6~9. 분봉 Backtest / Walk-forward / Stress / Agent vs 단일전략

```
python scripts/run_intraday_strategy_validation.py \
    --input-dir data/market/intraday --write-latest \
    --markdown reports/strategy_validation/intraday_strategy.md
```
PASS 종목만 backtest + walk-forward 실행, 종목별(`per_symbol`: trades/PF/expectancy/WF/
agent/verdict) + 전체 집계(median win_rate/PF/expectancy/MDD/WF, total_trades) +
**Agent vs 단일전략**(`agent_value_summary`: AGENT_ADDS_VALUE / AGENT_RISK_REDUCTION_VALUE /
AGENT_UNDERPERFORMS / AGENT_TOO_CONSERVATIVE / AGENT_VALUE_INSUFFICIENT_SAMPLE / AGENT_MIXED).

## 10~18. 단타 전용 판정 기준 + caps

표본(분봉): PASS 종목 ≥ 3 / total_trades ≥ 30(초기 관찰) / ≥ 100(1차 평가) /
거래일 ≥ 5(권장 20). 성과: win_rate ≥ 50% / PF ≥ 1.2 / expectancy > 0 / MDD ≤ 10% /
연속손실 ≤ 5.

**verdict caps**:
- PASS 종목 0 → **BLOCKED**, 품질 FAIL 과반 → NOT_READY.
- total_trades 0 또는 < 30 → 최대 RESEARCH_ONLY.
- total_trades < 100 → 최대 CAUTIOUS_CANDIDATE.
- Agent 무진입 종목 ≥ 50% → 최대 RESEARCH_ONLY.
- median walk_forward_score < 40 → 최대 RESEARCH_ONLY.
- **Paper sample 0건 → 실전 검토 불가**(STRONG 도달 불가) — 본 파이프라인은 Paper 미투입.

## 19~22. 강점 / 약점 / 전략 가능성 / 다음 단계

- **강점**: 분봉에서는 전략이 정상 진입(일봉 0 trade 와 대조) → 비로소 측정 가능.
- **약점**: clean *합성* 분봉 fixture 는 PF/win_rate 가 비현실적으로 높게 나올 수 있음
  (overfit 신호) — caps(WF/agent/Paper)가 RESEARCH_ONLY 로 보수적으로 묶는다.
- **전략 가능성 판단**: *실제 분봉 데이터* + Paper 표본 전까지는 RESEARCH_ONLY 가 상한.
- **다음 단계**: 실제 분봉 OHLCV(운영자 분봉 CSV 또는 KIS 분봉 read-only 수집) 확보 →
  재실행 → Paper 100건/28일 축적 → 그 후 실전 검토(별도 옵트인 + 운영자 승인).

## 23~25. 자동 적용 아님 / 실전 승인 아님 / 수익 보장 아님

`IntradayStrategyReport.do_not_auto_apply=True` / `auto_apply_allowed=False` /
`is_live_authorization=False` / `is_order_signal=False` / `contains_secret=False` /
`kis_intraday_available=False` 불변(dataclass 가드). broker / OrderExecutor / route_order /
KIS 주문 API 호출 0건.

## UI / API

AISignal 탭 `IntradayStrategyValidationCard` — intraday_data_used / bar_size / PASS·BLOCKED
종목 / total_trades / win_rate·PF·expectancy·WF / Agent 효과·무진입 종목 / verdict. 새로고침·
복사 버튼만, 매수/매도/실전/자동적용/승인 버튼 0개. API:
`GET /api/system/intraday-strategy-validation/latest` — **무거운 실행 금지(파일 없으면 empty
fallback)**, 실행은 CLI `--write-latest` 전용.
