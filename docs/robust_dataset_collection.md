# KIS-INTRADAY-ROBUST-DATASET-COLLECTION-01 — robust 분봉 데이터셋 수집/품질/메타데이터

> **본 작업은 데이터 *수집/품질검증/분할 메타데이터 구축* 전용이다. 백테스트를
> 실행하지 않는다.** 결과는 검증 자료일 뿐 — 주문 신호 / 실전 전환 승인 / 투자 추천 /
> 수익 보장이 아니다. broker / OrderExecutor / route_order / KIS 주문 API 호출 0건,
> EXE / cargo / tauri 빌드 0건, 안전 flag 변경 0건.

## 1. 배경

기존 6개월 50종목 5분봉 검증은 NOT_RECOMMENDED → 전략/Agent 분해 → 60d/weekly locked
rule holdout RESEARCH_PROMISING 까지 진전했으나, *추가 forward 데이터 부재*로
NEW_DATA_INSUFFICIENT 로 종료되었다. 단순히 종목 수만 늘리는 대신, 정확한 *장기* 검증을
위해 3개 축(시간 분할 / 장세 구분 / 종목 유형 다양화)을 반영한 robust 데이터셋을
*별도 경로*에 새로 수집한다.

## 2. 데이터 / 리포트 경로 (모두 gitignore)

| 종류 | 경로 |
|---|---|
| 5분봉 | `data/market/robust_intraday_5m/{symbol}_5m.csv` |
| 1분봉 정밀 subset | `data/market/robust_intraday_1m_subset/{symbol}_1m.csv` |
| 종목군 manifest | `reports/strategy_validation/robust_symbol_group_manifest.{json,md}` |
| 수집 진행 | `reports/strategy_validation/robust_dataset_collection_progress.json` (+ `robust_dataset_5m_progress.json`) |
| 실패 종목 | `reports/strategy_validation/robust_dataset_failed_symbols.json` |
| 1분봉 availability | `reports/strategy_validation/robust_1m_subset_availability.json` |
| 품질 | `reports/strategy_validation/robust_dataset_quality.{json,md}` |
| 시간 분할 | `reports/strategy_validation/robust_time_split_manifest.{json,md}` |
| 장세 regime | `reports/strategy_validation/robust_market_regime_manifest.{json,md}` |
| dataset manifest | `reports/strategy_validation/robust_dataset_manifest.{json,md}` |
| endpoint status | `reports/strategy_validation/robust_dataset_status.json` |

> **기존 `data/market/intraday_5m_1y*` / `kis_6m` 데이터와 혼동하지 않게 별도 경로를
> 사용한다. 기존 데이터는 절대 삭제하지 않는다.** 새 데이터 내부에서는 중복 timestamp 를
> 제거한다(엔진 재사용).

## 3. 종목군 (LARGE_CAP / MID_CAP / HIGH_VOL_THEME / ETF_PROXY)

`app/market_data/robust_dataset.py::build_robust_symbol_groups()` — 유형별 4그룹(현재 총 35종목).
종목 수를 무작정 늘리지 않고 유형으로 나눠 *추후 group별 성과 분리*가 가능하게 한다.

- LARGE_CAP 10 (삼성전자·SK하이닉스·현대차 등)
- MID_CAP 10 (KB금융·신한지주·HMM 등 업종 분산 + 저변동 대조군 한국전력)
- HIGH_VOL_THEME 10 (한미반도체·에코프로비엠·알테오젠·HLB 등 테마/고변동)
- ETF_PROXY 5 (KODEX 200 / 코스닥150 / TIGER 200 / 레버리지 / 인버스) — KIS 분봉 제공
  여부는 *수집 단계*에서 확정. 미제공 시 manifest 에 unavailable 기록 + 대형/중형
  equal-weight proxy 로 장세 regime 계산 대체.

각 종목에 `group` 라벨 + 선정 이유를 기록한다(`robust_symbol_group_manifest.json`).
가짜/과장 종목명 없음, 우선주/중복/invalid 제외.

## 4. 시간 분할 (Train / Validation / Test/OOS)

`build_time_split(trading_days)` — 거래일 인덱스 기준 **50/25/25**.

| method | 조건 | 분할 |
|---|---|---|
| `TWO_YEAR_50_25_25` | ≥400 거래일 | 첫 50% / 25% / 마지막 25% |
| `ONE_YEAR_6_3_3_MONTHS` | ≥220 거래일 | 동일 비율(≈6/3/3개월) |
| `MIN_240D_50_25_25` | ≥200 거래일 | 동일 비율(짧으면 WARN) |
| `INSUFFICIENT_FOR_SPLIT` | <200 거래일 | 분할 불가(FAIL) |

**절대 원칙**: `test_used_for_selection=False` 불변 — **Test/OOS 구간은 selector / score /
파라미터 선택에 절대 사용하지 않는다.** Validation 은 과최적화 탐지용, Train 은 규칙/
파라미터 개발용. 모든 split 경계 날짜를 명시한다(겹침 없음).

## 5. 장세 regime 라벨 (look-ahead 금지)

`label_market_regimes(daily)` — ETF proxy(069500) 우선, 없으면 대형+중형 equal-weight proxy.
8종 라벨(`UPTREND` / `DOWNTREND` / `SIDEWAYS` / `HIGH_VOLATILITY` / `GAP_UP_DAY` /
`GAP_DOWN_DAY` / `CRASH_LIKE_DAY` / `LOW_LIQUIDITY_DAY`) + `regime_primary` + `confidence`.

기준은 단순/설명 가능: lookback 20거래일의 ret20 / ma20 기울기 / vol20 / gap / day_ret /
vol_ratio (formula 는 manifest 에 명시). **미래 데이터로 과거를 라벨링하지 않는다** —
각 날짜는 해당 날짜까지의 정보만 사용(`no_look_ahead=True` 불변, 테스트로 lock).

## 6. 5분봉 / 1분봉 subset 수집기

`scripts/collect_robust_intraday_dataset.py` — 기존 `collect_kis_intraday_ohlcv` 의
read-only KIS 분봉 엔진(주식일별분봉조회 [국내주식-213], TR `FHKST03010230`)을 *그대로
재사용*하며 별도 경로에 저장. resume / skip / 원자 저장(.tmp→replace) / 중복 제거 /
EGW00201 backoff / 토큰 캐시(EGW00133 회피) / progress·failed JSON 을 엔진에서 승계.

```bash
# 5분봉 (2y 우선 → KIS 제공 범위만; robust50≈35종목)
python scripts/collect_robust_intraday_dataset.py --stage 5m --symbol-set robust50 --period 2y --resume
# 1분봉 정밀 subset (대표 10종목, 최근 60거래일)
python scripts/collect_robust_intraday_dataset.py --stage 1m_subset --period 60d --resume
```

- KIS 분봉은 ~1년 제공 — `--period 2y` 요청 시 *가능한 만큼만* 수집(과장 금지).
- 1분봉이 제공되지 않거나 실패하면 **FAIL 이 아니라 `UNAVAILABLE`** 로 기록하고 5분봉
  수집은 계속 진행한다. 1분봉 목적: target/stop 선후·ORB/GAP 장초반 체결순서 정밀 검증 *준비*.

## 7. 품질검증 + manifest

`scripts/validate_robust_intraday_dataset.py` — 수집된 5분봉을 read-only 적재 →
종목별/그룹별 품질 + 시간 분할 + 장세 regime + dataset manifest 생성. **백테스트 0건.**

```bash
python scripts/validate_robust_intraday_dataset.py --write-latest
```

`ready_for_robust_backtest` 기준(모두 충족 시 True): 5분봉 품질 PASS/WARN + split 생성
가능 + ≥30종목 + ≥200거래일 + regime 생성 가능. **준비 완료여도 실전 아님** — 후속
*별도 작업*에서 train/validation/test 로 고정 룰을 검증한다.

## 8. API / UI

- `GET /api/system/robust-dataset/status` (read-only) — `robust_dataset_status.json` 이 있으면
  그 snapshot, 없으면 종목군 manifest 기반 empty fallback. 무거운 적재/검증/KIS 호출 0건.
- `RobustDatasetStatusCard` (AISignal 탭) — 수집/품질 status, 종목군 분포, 기간/거래일,
  시간분할/regime/1분봉, ready 여부, warnings, 다음 작업. **주문/실전/자동매매 시작/EXE 빌드/
  적용 버튼 0개**(새로고침·복사만), input/textarea 0개.

## 9. 안전 invariant (dataclass `__post_init__` + 정적 grep + 테스트 lock)

`SymbolGroupManifest` / `TimeSplitManifest`(`test_used_for_selection=False`) /
`RegimeManifest`(`no_look_ahead=True`) / `DatasetQualityReport` / `DatasetManifest` 모두:
`is_live_authorization` / `real_order_allowed` / `live_trading_recommendation` /
`is_order_signal` / `kis_order_api_called` / `broker_order_sent` / `exe_build_executed` /
`contains_secret` = **False**, `do_not_auto_apply` / `no_profit_guarantee` = **True**.

모듈/스크립트는 `app.brokers` / `app.execution` / `order_router` / `anthropic` / `openai` /
`httpx` / `requests` / `app.ai.client` import 0건, `place_order(` / `route_order(` /
`OrderExecutor(` / `cargo build` / `tauri build` 0건(정적 grep 테스트).

## 10. 실행 상태 / 다음 단계

- **인프라 / 메타데이터 파이프라인**: 완료 (모듈 + 2개 CLI + endpoint + UI + 테스트).
- **소규모 실측 검증(read-only)**: representative10 · 5분봉 · 3일 수집 성공(10/10 종목,
  980 bars) → validate 가 honest 부분 manifest(quality WARN / split FAIL(2일) / 10/35종목 /
  ready=False) 생성 — 파이프라인이 실데이터로 동작함을 확인.
- **다음(운영자 장시간 작업)**: `--symbol-set robust50 --period 2y`(KIS ~1년) 전량 수집 +
  `--stage 1m_subset` → `validate_robust_intraday_dataset.py --write-latest`. 수집은 KIS
  read-only 시세만 사용(주문 API 0건). 충분(≥30종목·≥200거래일) 시 `ready_for_robust_backtest`
  True → **그래도 백테스트/실전은 별도 작업**(본 작업 범위 밖).
