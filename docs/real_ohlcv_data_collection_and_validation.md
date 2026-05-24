# 실제 OHLCV 데이터 확보 + 다종목 Backtest/Walk-forward (REAL-DATA-INPUT-01)

> 사용자 핵심 질문 — **"내 매매기법 + Agent 결합 전략이 실제 데이터 기준 가능성 있는가?"**
> 의 1순위(깨끗한 실제 OHLCV 확보) + 2순위(실데이터 Backtest/Walk-forward)를 수행한다.
> **자동 적용 / 실전 전환 승인 / 주문 신호가 아니다. 수익을 보장하지 않는다.**

## 1. 깨끗한 OHLCV 데이터가 필요한 이유

백테스트/Walk-forward 의 신뢰도는 *입력 데이터 품질* 에 좌우된다. 깨진 OHLC(예:
`open > high`), 중복 timestamp, 표본 부족은 잘못된 결론을 만든다. 따라서 **품질검증을
통과한 CSV 만** 백테스트에 사용하고, 깨진 데이터는 자동 보정하지 않고 BLOCKED 로 둔다.

## 2. KIS 현재가 API 와 과거 OHLCV 의 차이

KIS Client 는 `get_price`(현재가) / `inquire_balance` / `inquire_daily_ccld` /
`place_order` 중심이며 **과거 일봉/분봉 collector 는 미구현** 이다. 백테스트는 과거
시계열이 필요하므로 KIS 현재가만으로는 불가능 → **CSV(권장) / yfinance(준실제)** 사용.
KIS `get_price` 는 *장중 리허설(BUILD-02B)* 용으로만 문서화하며, 본 작업에서 KIS 주문
API 는 **절대 호출하지 않는다.**

## 3~4. CSV 준비 + 스키마

```
timestamp,open,high,low,close,volume        # 필수
# 선택: symbol,vwap,market_regime,time_phase,source
2024-01-02,70000,70800,69800,70600,8500000
```
- `timestamp`: YYYY-MM-DD 또는 ISO datetime, 오름차순.
- `open/high/low/close > 0`, `volume >= 0`, `low ≤ open,close ≤ high`.
- 저장: 실데이터는 `data/market/real_ohlcv/{symbol}.csv` (**gitignore — 커밋 안 함**).
  테스트용 소형 fixture 만 `backend/tests/fixtures/real_data_clean/` 에 커밋(추적).

## 5. yfinance 수집

```
python scripts/collect_real_ohlcv_data.py --source yfinance \
    --symbols 005930,000660,... --start 2024-01-01 --end today \
    --output-dir data/market/real_ohlcv \
    --json reports/strategy_validation/real_ohlcv_quality.json
```
- 한국 종목 Yahoo ticker: KOSPI `005930.KS`, KOSDAQ `091990.KQ` (로더가 자동 변환).
- **yfinance 미설치/네트워크 실패 → 명확한 WARN/FAIL** (sample/mock 으로 몰래 대체 0건,
  수집 실패를 성공으로 표시 0건). 네트워크 환경에서만 사용.
- `--source existing --source-dir <dir>` 로 사용자가 직접 받은 CSV 검증/수집도 가능
  (기본 source-dir = clean fixture).

## 6. 권장 종목 / 기간

10종목: 005930 삼성전자 · 000660 SK하이닉스 · 035420 NAVER · 035720 카카오 ·
005380 현대차 · 000270 기아 · 006400 삼성SDI · 373220 LG에너지솔루션 ·
005490 POSCO홀딩스 · 068270 셀트리온. 기간: 6개월+ (100거래일+ 권장), 상승/하락/횡보 포함.

## 7~9. 데이터 품질검증 기준 (FAIL / WARN / PASS)

- **FAIL**: required column 누락 / `OHLC ≤ 0` / `volume < 0` / `high < open|close|low` /
  `low > open|close`. → 백테스트 제외, BLOCKED.
- **WARN**: 중복 timestamp / 거래일 < 28(또는 < 100) / volume 0 row / yfinance fallback /
  단일 종목. → 백테스트 가능하나 표본 부족.
- **PASS**: OHLC 무결 + 최소 28거래일(권장 100) + 백테스트 입력 가능.
- **BLOCKED 예시**: 저장소 fixture `005930.csv` 는 일부 행에서 `open > high` 인 잘못된
  OHLC 가 있어 **반드시 계속 BLOCKED** 로 검출된다 (자동 보정 금지, 테스트로 lock).

## 10~12. 실데이터 Backtest / Walk-forward / Agent vs 단일전략

```
python scripts/run_real_ohlcv_backtest_walkforward.py \
    --input-dir data/market/real_ohlcv --write-latest \
    --markdown reports/strategy_validation/real_ohlcv_bt_wf.md
```
- PASS 종목만 backtest + walk-forward + stress 실행, FAIL 종목 제외.
- 종목별 결과(`per_symbol`: PF/expectancy/MDD/WF/agent/verdict) + 전체 aggregate
  (median PF/expectancy/MDD/WF, total_trades) 산출.
- **Agent vs 단일전략**: `agent_value_summary`(AGENT_ADDS_VALUE / AGENT_UNDERPERFORMS /
  AGENT_VALUE_INSUFFICIENT_SAMPLE / AGENT_MIXED) + 도움/방해/무진입 종목 리스트.

## 13. overall_verdict 해석 + caps

5단계(STRONG / CAUTIOUS / RESEARCH_ONLY / NOT_READY / BLOCKED). 보수 cap:
- PASS 종목 0 → **BLOCKED**, 품질 FAIL 과반 → 최대 NOT_READY.
- sample fixture only → 최대 RESEARCH_ONLY.
- total_trades < 100 → 최대 RESEARCH_ONLY.
- median walk_forward_score < 40 → 최대 RESEARCH_ONLY.
- Agent underperform 종목이 PASS 의 50% 이상 → 최대 RESEARCH_ONLY.
- **Paper sample 0건 → 실전 검토 불가** (실전 전환은 Paper 100건/28일 + 운영자 승인 필요).

## 14. Paper 100건 / 28거래일 기준

실데이터 백테스트가 좋아도 Paper 모의 100건 + 28거래일 표본이 없으면 실전 검토 불가
(`paper_sample_class=PAPER_NO_TRADES_YET`).

## 15~17. 자동 적용 아님 / 실전 승인 아님 / 수익 보장 아님

`RealOhlcvDatasetReport.do_not_auto_apply=True` / `auto_apply_allowed=False` /
`is_live_authorization=False` / `is_order_signal=False` / `contains_secret=False` /
`kis_historical_available=False` 불변(dataclass 가드). 결과가 좋아도 자동 설정 변경 /
live promotion / threshold 자동 적용 0건. broker / OrderExecutor / route_order / KIS 주문
API 호출 0건.

## UI

AISignal 탭 `RealDataStrategyValidationCard` — dataset 리포트가 있으면 PASS 종목 /
품질 BLOCKED 종목 / total_trades·median PF·median WF·Agent 효과 요약을 추가 표시.
새로고침·복사 버튼만, 매수/매도/실전/자동적용/승인 버튼 0개. API:
`GET /api/system/real-data-strategy-validation/latest` (read-only; `--write-latest` 로 갱신).
