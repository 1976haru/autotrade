# 실제 분봉 데이터 확보 + KIS read-only collector 준비 (INTRADAY-DATA-02)

> 단타 전략(ORB/VWAP/Momentum/Gap/Agent Council)은 분봉에서만 의미 있게 검증된다. 본 문서는
> **실제 분봉 CSV 를 넣어 바로 검증하는 경로** 와 **KIS 분봉 read-only collector 의 안전 구조**
> 를 설명한다. **자동 적용 / 실전 전환 승인 / 주문 신호가 아니다. 수익을 보장하지 않는다.**

## 1~2. 왜 실제 분봉 데이터가 필요한가 (일봉 total_trades=0)

REAL-DATA-INPUT-01 §0: 실제 KOSPI **일봉** 으로 검증하면 10종목 total_trades=0. ORB(장 시작
N분 돌파)·VWAP(당일 누적)·Gap·Momentum 은 *분(minute)* micro-structure 가 필요하기 때문이다.
→ **분봉** 이 있어야 진입이 발생하고 승률/PF/expectancy/Walk-forward 를 측정할 수 있다.

## 3~6. 분봉 CSV 준비 / 한글 컬럼 매핑 / 저장 위치

표준 입력 경로(통일): **`data/market/intraday_ohlcv/`** (gitignore — 커밋 안 함).
파일명: `005930_5m.csv` / `005930_1m.csv` (bar-size 파일명에서 추론) 또는 단순형 `005930.csv`
(이때 `--bar-size` 옵션).

표준 컬럼: `timestamp,open,high,low,close,volume` (+ 선택 `symbol,vwap,market_regime,
time_phase,source`). 증권사/HTS 한글 컬럼은 자동 매핑된다:

| 한글/변형 | 표준 |
|---|---|
| 일자 / 일시 / 시간 / 체결시간 / 날짜 | timestamp |
| 시가 | open · 고가 → high · 저가 → low |
| 종가 / 현재가 / 체결가 | close |
| 거래량 / 체결량 / 누적거래량 | volume |
| 종목코드 / 종목 | symbol |

숫자의 쉼표(`71,000`)·"원"·"%" 는 자동 제거. timestamp 예: `2024-03-04 09:00:00`.

## 7. normalize_intraday_csv.py 사용법

```
python scripts/normalize_intraday_csv.py --input HTS_raw.csv \
    --output data/market/intraday_ohlcv/005930_5m.csv --symbol 005930
```
한글/쉼표 CSV → 표준 CSV. **값을 만들어내지 않으며**(가짜 데이터 0), 파싱 불가 row 는
드롭(보정 아님)하고 카운트한다. 필수 컬럼 누락 시 exit 1. (검증 파이프라인의 로더도 동일
normalizer 를 내장하므로, 한글 CSV 를 바로 `--input-dir` 에 넣어도 된다.)

## 8. 검증 실행

```
python scripts/run_intraday_strategy_validation.py \
    --input-dir data/market/intraday_ohlcv --bar-size 5m \
    --min-bars 100 --min-days 5 --write-latest \
    --markdown reports/strategy_validation/intraday_strategy.md \
    --json reports/strategy_validation/intraday_strategy.json
```
PASS 종목만 backtest + walk-forward + Agent 비교. **품질 FAIL(일봉 포함) 종목은 제외.**

## 9~11. KIS 분봉 collector 상태 / 공식 endpoint 확인 전 호출 안 함 / 현재가 vs historical

`app/market_data/kis_intraday_collector.py` 는 **안전 placeholder** 다:

- 기본 상태 **`NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION`** — KIS 분봉 historical quotation 의 공식
  endpoint / TR ID 가 확인되기 전까지 **실제 네트워크 요청을 보내지 않는다.**
- KIS Client 는 *현재가*(get_price) / 잔고 / 당일체결만 제공 — *과거 분봉(historical)* 은 별개
  endpoint 가 필요하며 본 프로젝트에 미구현. 현재가만으로는 과거 백테스트 불가.
- 환경변수(기본 안전값): `KIS_INTRADAY_ENABLED=false` · `KIS_INTRADAY_ENDPOINT=` ·
  `KIS_INTRADAY_TR_ID=` · `KIS_INTRADAY_READ_ONLY=true`.
- 상태 전이: endpoint/TR 미확인 → `NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION`; read_only=false →
  `BLOCKED_NOT_READ_ONLY`; 비활성 → `DISABLED`; 모두 충족 → `READY_READ_ONLY`(구조상 가능, 단
  실제 fetch 는 운영자 검토 후 별도 PR).
- **본 모듈은 httpx/requests/place_order/OrderExecutor/route_order 를 import 하지 않는다** —
  구조적으로 주문/임의 호출이 불가능. 상태에는 endpoint/secret 원문 없이 present 여부 bool 만.
- 상태 조회: `GET /api/system/intraday-data-source/status` (read-only).

## 12~13. 데이터 품질검증 / 품질 FAIL 데이터 미사용

`check_intraday_quality`: OHLC 무결성 / timestamp 파싱 / 장중(KST 09:00~15:30) / 일중 bar 수
(<5 면 일봉으로 간주 **FAIL**) / bar_size / 중복 / 표본(≥100 bar·≥5거래일 권장 20). 잘못된 row 는
드롭(과다 5%↑면 FAIL). **품질 FAIL 분봉은 백테스트에 사용하지 않는다.**

## 14~16. 자동 적용 아님 / 실전 승인 아님 / 수익 보장 아님

모든 결과 객체 `do_not_auto_apply=True` / `is_live_authorization=False` /
`is_order_signal=False` / `contains_secret=False` 불변(dataclass 가드). broker / OrderExecutor /
route_order / KIS 주문 API 호출 0건. 실전 전환은 Paper 100건/28일 + 운영자 명시 승인 + 별도
옵트인 PR 필요.

## 운영자 빠른 시작

1. HTS/증권사에서 분봉(예: 5분봉) CSV 다운로드 → `scripts/normalize_intraday_csv.py` 로 표준화
   하여 `data/market/intraday_ohlcv/005930_5m.csv` 로 저장(또는 한글 CSV 를 그대로 넣어도 로더가
   정규화).
2. `python scripts/run_intraday_strategy_validation.py --write-latest` 실행.
3. AISignal 탭 `IntradayStrategyValidationCard` 새로고침 → 데이터 경로 / 입력 모드 / KIS collector
   상태 / 종목별·집계 결과 확인. (매수/매도/실전/승인 버튼 없음.)
