# Data Quality Report (체크리스트 #21)

## 1. 목적

`MarketBar` 테이블에 저장된 OHLCV 데이터의 누락 / 중복 / OHLC 무결성 / volume 이상 / 장외 혼입 / `fetched_at` 이상을 일별로 평가하고 0~100 점수로 등급화한다. **품질이 낮은 날의 데이터는 백테스트와 전략 승격 평가에서 제외**한다.

본 PR은:
- 검사 로직: `app/market/data_quality.py` (순수 함수, 외부 호출 0건)
- CLI: `scripts/check_data_quality.py` (DB 조회만, 시크릿 출력 0건)
- BacktestEngine / 승격 흐름 코드는 변경하지 않는다 — 정책과 helper만 제공.

## 2. 검사 항목

| 카테고리 | 항목 | 감지 |
|---|---|---|
| **누락** | expected vs actual | `expected_count` (정규장 09:00–15:30 KST 기준 단순 시간) vs `actual_count`. 차이가 `missing_count` |
| **중복** | duplicate timestamp | (symbol, interval, timestamp) 같은 행이 여러 번이면 1건씩 카운트 |
| **OHLC 무결성** | high<low / open out of [low,high] / close out of [low,high] | `ohlc_invalid_count` |
| **가격** | 음수 또는 0 가격 (open/high/low/close 중 하나라도) | `nonpositive_price_count` |
| **급등락** | 인접 봉 close 변화율이 ±30% 초과 | `extreme_return_count` (정규장 단봉 기준 보수적 임계) |
| **Volume** | 음수 volume | `negative_volume_count` |
| **Volume spike** | 직전 20개 평균의 10배 초과 | `volume_spike_count` |
| **Volume zero streak** | 0(또는 None) volume 연속 길이 | `zero_volume_streak_max` |
| **장외 혼입** | KRX 정규장 09:00–15:30 KST 범위 밖 | `out_of_session_count`. `--include-out-of-session`이면 점수 감점 안 함 |
| **fetched_at 누락** | NULL | `fetched_at_missing_count` |
| **fetched_at 미래** | now + 60s 초과 | `fetched_at_future_count` |
| **fetched_at 너무 오래됨** | now - 7일 미만 | `fetched_at_stale_count` |

각 검사는 본 모듈의 순수 함수로 분리되어 있어 단독으로 단위 테스트 가능하다 (`backend/tests/test_data_quality.py`).

## 3. 품질 점수 산출

기본 100점에서 항목별 감점 누적. 최종 점수는 `[0, 100]`로 clamp.

| 항목 | 단위 감점 | 최대 감점 |
|---|---|---|
| `missing_rate` | `missing_rate × 40` | 40 |
| 중복 | 5/건 | 20 |
| OHLC 무결성 위반 | 5/건 | 30 |
| 음수/0 가격 | 10/건 | 30 |
| 급등락 (±30% 초과) | 3/건 | 15 |
| 음수 volume | 10/건 | 20 |
| volume spike | 2/건 | 10 |
| zero volume streak (>5) | (streak-5)×1.5 | 15 |
| 장외 혼입 (`--include-out-of-session=False`일 때만) | 0.5/건 | 20 |
| fetched_at 미래 | 5/건 | 15 |
| fetched_at 누락 | 0.5/건 | 10 |
| fetched_at stale (7일 초과) | 1/건 | 10 |

### 등급

| 등급 | 점수 | 의미 |
|---|---|---|
| `GOOD`     | ≥ 90 | 백테스트 / 승격 평가에 사용 가능 |
| `WARNING`  | 75–89 | 사용 가능하되 운영자 검토 권장 |
| `POOR`     | 60–74 | **기본 제외** — 운영자가 명시적으로 사용 결정 시에만 |
| `EXCLUDE`  | < 60 | **백테스트 제외 + 승격 평가 사용 금지** |
| `EMPTY`    | (데이터 없음) | weekend/공휴일이면 정상, weekday면 비정상 (아래 한계) |

## 4. 백테스트 사용 정책

| 상황 | 정책 |
|---|---|
| `GOOD` 데이터 | 정상 사용 |
| `WARNING` 데이터 | 사용 가능 — UI / 리포트에 warning 표시 권장 |
| `POOR` 데이터 | **기본 제외**. 운영자가 명시 옵트인 시에만 사용. backtest 결과에 품질 라벨 동행 권장 |
| `EXCLUDE` 데이터 | **사용 금지**. 결과는 전략 승격 평가에 사용 금지 |
| 부분 `EXCLUDE` 구간 | walk-forward fold에서 해당 구간 제외 (향후 fold-level 통합) |

본 PR에서는 BacktestEngine 코드를 변경하지 않는다. 향후 옵트인 PR로 도입 예정:
- `BacktestRequest`에 `min_quality_score` / `min_grade` 옵션
- walk-forward runner의 fold별 데이터 품질 점수 평가
- backtest 결과 row에 `data_quality` 메타 carry

## 5. Promotion Policy 연계

`docs/promotion_policy.md`의 단계별 승격 조건과 lockstep:

| 단계 | 데이터 품질 요구 |
|---|---|
| `SIMULATION` → `PAPER` | GOOD 또는 WARNING 데이터로만 검증 |
| `PAPER` → `LIVE_SHADOW` | GOOD 또는 WARNING 데이터로만 검증 |
| `LIVE_SHADOW` → `LIVE_MANUAL_APPROVAL` | **GOOD만** — POOR/EXCLUDE 구간이 운영 데이터에 포함되면 승격 거부 |
| `LIVE_MANUAL_APPROVAL` → `LIVE_AI_*` | (본 시점 비활성) |

POOR/EXCLUDE 데이터로 만들어진 수익은 **전략 승격 근거로 사용 금지**. 승격 의사결정 운영자는 본 리포트의 일별 등급을 백테스트 결과와 함께 검토한다.

## 6. 한계 (현재 단계)

- **expected_count는 단순 시간 기반** — KRX 정규장 09:00–15:30 KST 평일 기준이며, 휴장일 캘린더는 미반영. 공휴일 평일에 expected가 부풀려져 missing_rate가 과대 추정될 수 있다.
- **장전/장후 단일가는 미지원** — 정규장 외 데이터는 `out_of_session`으로 분류.
- **tick / orderbook 품질 검사는 Phase 2** — 본 모듈은 OHLCV 봉 기준.
- **타임존** — `MarketBar.timestamp`는 SQLite 저장 시 naive (UTC 가정). 본 모듈은 KST로 변환해 정규장 판단.
- **fetched_at 7일 임계는 보수적** — 운영 환경에서 데이터를 7일 이상 갱신하지 않으면 일반적으로 stale로 취급해야 한다는 가정. 운영자가 자주 갱신하지 않는 데이터(과거 백테스트용)에서는 false positive 가능.

## 7. 실행 방법

### CLI

```bash
# 단일 일자 (text)
python scripts/check_data_quality.py --symbol 005930 --interval 1m \
    --date 2026-05-07

# 기간 (JSON 출력)
python scripts/check_data_quality.py --symbol 005930 --interval 1m \
    --start-date 2026-05-01 --end-date 2026-05-07 --format json

# 점수 낮은 일자만 추출
python scripts/check_data_quality.py --symbol 005930 --interval 1d \
    --start-date 2026-05-01 --end-date 2026-05-31 \
    --min-score 75 --format text

# 결과 파일 저장
python scripts/check_data_quality.py --symbol 005930 --interval 1d \
    --start-date 2026-05-01 --end-date 2026-05-31 --format json \
    --output reports/quality.json
```

### 출력 예 (text)

```
[2026-05-18] 005930 1m  score=72.5 grade=POOR  actual=380/391 missing=11 dup=0 ohlc_bad=2 oos=0
    · missing_rate=2.81% (-1.1)
    · ohlc_invalid=2 (-10.0)
    · volume_spike=2 (-4.0)
    · fetched_at_missing=12 (-6.0)

summary: {'report_count': 1, 'by_grade': {'POOR': 1}, 'avg_score': 72.5, 'min_score': 72.5, 'max_score': 72.5}
```

### 출력 예 (json)

```json
{
  "reports": [
    {
      "symbol": "005930", "interval": "1m", "date": "2026-05-18",
      "score": 72.5, "grade": "POOR",
      "missing_rate": 0.0281, "coverage_score": 97.18,
      "include_out_of_session": false,
      "issues": {
        "expected_count": 391, "actual_count": 380,
        "missing_count": 11, "duplicate_count": 0,
        "ohlc_invalid_count": 2, "nonpositive_price_count": 0,
        "extreme_return_count": 0, "negative_volume_count": 0,
        "volume_spike_count": 2, "zero_volume_streak_max": 0,
        "out_of_session_count": 0,
        "fetched_at_missing_count": 12, "fetched_at_future_count": 0,
        "fetched_at_stale_count": 0
      },
      "notes": ["missing_rate=2.81% (-1.1)", "ohlc_invalid=2 (-10.0)", "..."]
    }
  ],
  "summary": {
    "report_count": 1, "by_grade": {"POOR": 1},
    "avg_score": 72.5, "min_score": 72.5, "max_score": 72.5
  }
}
```

### CLI 옵션

| 옵션 | 의미 |
|---|---|
| `--symbol` | 종목 코드 (필수) |
| `--interval` | 봉 간격 — `1m / 5m / 1h / 1d` (필수) |
| `--date` | 단일 일자 (`--start-date`/`--end-date`와 상호 배타) |
| `--start-date` / `--end-date` | 기간 (양쪽 KST 기준, end inclusive) |
| `--min-score` | 이 점수 미만 일자만 출력 |
| `--format` | `text` (기본) 또는 `json` |
| `--include-out-of-session` | 장외 데이터를 점수 감점 대상에서 제외 |
| `--output` | 결과 파일 경로 (없으면 stdout) |

## 8. 후속 작업 (Backlog)

| 항목 | 트리거 |
|---|---|
| KRX 휴장일 캘린더 통합 (정확한 expected_count) | LIVE 활성화 PR 전 |
| `BacktestRequest`에 `min_quality_score` / `min_grade` 옵션 + 결과에 품질 메타 carry | 23번 (Backtest Engine 강화) PR |
| Walk-forward runner의 fold별 데이터 품질 평가 + EXCLUDE fold 자동 제외 | 동일 |
| `data_quality_report` DB 테이블 (배치 실행 결과 영구화) | 운영자가 시계열 품질 모니터링이 필요해진 시점 |
| Frontend 데이터 품질 카드 (Dashboard 또는 Backtest 탭) | UI 요청 시 |
| Tick / orderbook 품질 검사 | tick 테이블 도입 (Phase 2) 후 |
| Volume spike의 종목별 baseline 학습 | 운영 데이터 누적 후 |
| 장중 일시 정지(거래 정지/sidecar) 식별 | 별도 신호 소스 통합 후 |

## 9. 안전 invariant (본 PR이 지키는 것)

- `MarketBar` 스키마 변경 0건.
- 새 alembic migration 0건.
- 주문 / 리스크 / `PermissionGate` / `OrderExecutor` / `route_order` 분기 변경 0건.
- 외부 네트워크 호출 0건 (CLI도 DB만 조회).
- 시크릿 출력 0건 (CLI는 `--symbol` / `--interval` 등만 표시).
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 변경 0건.
- API Key / Secret / 계좌번호 변경 0건.
- BacktestEngine 코드 변경 0건 — 정책만 문서화.

## 관련 문서

- [`market_data_collector.md`](market_data_collector.md) — OHLCV 수집 + 누락률 (`expected_bar_count` 재사용)
- [`data_freshness_policy.md`](data_freshness_policy.md) — fetched_at 기반 stale 가드 (#171 / #20)
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 + 품질 요구
- [`database_schema.md`](database_schema.md) — `MarketBar` 컬럼/인덱스
- [`backlog.md`](backlog.md) — 후속 과제
