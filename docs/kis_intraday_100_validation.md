# KIS-INTRADAY-100-VALIDATION-01 — 실제 KIS 분봉 100종목 대규모 전략검증

## 목적

KIS 연결 여부 확인이 아니라, **신뢰성 있는 실제 KIS 분봉 데이터**로 현재 프로그램의
결합 전략(ORB / Momentum / Gap / VWAP + Agent Council + RiskOfficer + exit_plan +
quality_score gate)이 *가능성 있는지* 종합 판정한다. 100종목 내외의 실제 분봉을
read-only 로 수집 → 품질검증 → 전략검증(backtest + walk-forward + stress + Agent vs
단일전략) → 사용자 최종 판단을 산출한다.

## 절대 원칙 (read-only)

- **KIS 주문 API 호출 0건** — `inquire-time-dailychartprice`(시세 조회) 만 사용.
- `broker.place_order` / `route_order` / `OrderExecutor` 호출 0건.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 변경 0건.
- appkey / appsecret / access token / 계좌번호 **원문 출력 0건** (present 여부 bool 만).
- 결과가 좋아도 **자동 적용 / 실전 전환 / threshold 자동 반영 0건**. 수익 보장 문구 0건.
- 수집 실패를 성공처럼 보고 0건, 품질 FAIL 데이터를 백테스트에 사용 0건, 일부 좋은 종목만
  골라 과장 0건.

## 사용 API

| 항목 | 값 |
|---|---|
| 이름 | 주식일별분봉조회 [국내주식-213] |
| Method | GET (read-only) |
| Endpoint | `/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice` |
| TR ID | `FHKST03010230` |
| 성격 | 시세 조회 API, **주문 API 아님** |

응답 `output2` 분봉 row 필드: `stck_bsop_date`(영업일) / `stck_cntg_hour`(체결시간) /
`stck_oprc`(시가) / `stck_hgpr`(고가) / `stck_lwpr`(저가) / `stck_prpr`(종가) /
`cntg_vol`(거래량). 한 호출에 1분봉 120 bar 를 일자 경계를 넘어 과거로 반환
(`FID_PW_DATA_INCU_YN=Y`). 수집기는 1분봉을 5분봉으로 resample 한다.

## 구성 요소

- `app/market_data/kis_intraday_universe.py` — 검증용 100종목 universe 구성
  (top50 + 보강 후보, 우선주/중복/invalid 제외). 순수 데이터, 주문/HTTP import 0건.
- `app/brokers/kis_client.py::KisClient.inquire_time_dailychartprice` — read-only 분봉
  시세 메서드 (TR `FHKST03010230`). 토큰 관리는 기존 KisClient 재사용.
- `app/market_data/kis_intraday_fetch.py` — KIS 응답 파싱 / 1m→5m resample (순수 함수,
  httpx/requests import 0건).
- `scripts/collect_kis_intraday_ohlcv.py` — 단계적 수집 CLI. read-only 시세만 호출,
  토큰은 gitignored 로컬 캐시 재사용(`EGW00133` 1분당 1회 제한 회피), 초당 건수 초과
  (`EGW00201`)는 backoff 재시도. CSV → `data/market/intraday_ohlcv/kis/{symbol}_5m.csv`
  (gitignore).
- `app/system/kis_intraday_100_final_result.py` — 품질 PASS-only 전략검증 +
  Agent/WF/stress 종합 → 개발자 verdict + 사용자 최종 판단 6단계. 기존
  `intraday_strategy_validation` / `real_intraday_final_result` /
  `agent_stress_test` 재사용.
- `scripts/run_kis_intraday_100_validation.py` — 최종 판정 CLI →
  `reports/strategy_validation/kis_intraday_100_final_result.{md,json}` +
  `kis_intraday_100_latest.json`(gitignore).
- `GET /api/system/kis-intraday-100-validation/latest` — latest 리포트 read-only
  (없으면 empty fallback, 무거운 수집/backtest 미실행).
- `frontend KisIntraday100ValidationCard` (AISignal 탭) — verdict / 수집 / 품질 / 성과 /
  Agent / 상위 종목 / 다음 단계 표시. 새로고침·복사만 — 매수/매도/실전/자동적용/승인 버튼
  0개, input/textarea 0개.

## 단계적 수집

처음부터 100종목 전체를 호출하지 않고 1종목 → 5종목 → 20종목 → 100종목 순으로 안정성을
확인한다. 각 단계에서 HTTP 200 / OHLCV 매핑 / CSV 저장 / 품질 PASS 를 검증한다.

```
python scripts/collect_kis_intraday_ohlcv.py --from-universe --max-symbols 100 \
  --num-days 15 --bar-size 5m --sleep-seconds 0.45 \
  --output-dir data/market/intraday_ohlcv/kis \
  --json reports/strategy_validation/kis_intraday_collect_100.json
python scripts/run_kis_intraday_100_validation.py \
  --input-dir data/market/intraday_ohlcv/kis \
  --collect-json reports/strategy_validation/kis_intraday_collect_100.json --write-latest
```

## 최종 판정 cap (KIS 대규모 전용 보수)

- 품질 PASS < 30 → `DATA_NOT_RELIABLE` (데이터 신뢰 불가).
- PROMISING 은 실데이터 + 품질 PASS ≥ 70 + total_trades ≥ 500 + median PF ≥ 1.2 +
  expectancy > 0 + median walk_forward_score ≥ 40 + Agent 순효과 positive + stress FAIL 0
  을 *모두* 충족할 때만. 하나라도 미달이면 최대 `WORTH_MORE_RESEARCH`.
- PROMISING 이어도 *실전이 아니라* Paper 모의 리허설 단계 — 실전 검토는 Paper 100건 +
  28거래일 + 운영자 명시 승인 필요.

## 안전 불변값

`KisIntraday100Result` 는 `is_live_authorization=False` / `broker_order_sent=False` /
`order_created=False` / `is_order_signal=False` / `kis_order_api_called=False` /
`contains_secret=False` / `do_not_auto_apply=True` / `no_profit_guarantee=True` 불변
(dataclass `__post_init__` ValueError 가드). 정적 grep 가드로 `route_order(` /
`.place_order(` / `OrderExecutor(` / 주문 endpoint 호출 0건을 검증한다.
