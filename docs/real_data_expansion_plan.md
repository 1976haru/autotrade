# Step 3-09 — 실데이터 백테스트 universe 확장 계획

> 본 문서는 *연구 / 검증* 파이프라인 정의입니다. **투자 조언이 아닙니다.**
> 본 universe 확장은 *advisory* 백테스트 후보 검증용이며 **실거래 신호가
> 아닙니다.** 안전 flag default (`KIS_IS_PAPER=true` /
> `ENABLE_LIVE_TRADING=false` / `ENABLE_AI_EXECUTION=false` /
> `ENABLE_FUTURES_LIVE_TRADING=false`) 가 유지됩니다.

---

## 0. 결론 — 4 핵심 원칙

1. **현재 10종은 *1차 기능 검증용 sample***. 백테스트 / 최적화 / Walk-forward
   / Stress test 파이프라인이 *동작하는지* 확인하기 위한 고정 카탈로그
   (`REPRESENTATIVE_SYMBOLS`).
2. **최종 검증은 *전체 종목이 아니라 필터 통과 종목* 대상**. 거래정지 / 관리종목
   / ETF/ETN/SPAC / 신규상장 / 데이터 결측 과다 / 거래량/거래대금 부족 종목은
   *모두 제외*.
3. **전체 종목 무조건 실행은 *왜곡 위험***. 저유동성 / 상장폐지 / 데이터 결측
   종목이 결과를 오염시킬 수 있다 — 옵트인 + 필터 통과 강제.
4. **유동성 상위 50 → 100 → 300 *단계적* 확장**. 50종 운용 결과를 운영자가
   검토한 뒤 100/300 로 옵트인.

본 PR (#3-09) 시점은 **확장 가능한 구조 + CLI 옵션 + 필터 정책**만 추가.
실제 LIQUIDITY_TOP* 데이터 source 주입은 *별도 옵트인 PR* 필요.

---

## 1. universe 종류 (`UniverseKind`)

`backend/app/backtest/real_data/universe.py` 의 `UniverseKind` enum:

| Kind | 의미 | 본 PR 시점 사용 가능 |
|---|---|---|
| `sample10` | 1차 검증용 10종 (`REPRESENTATIVE_SYMBOLS`) | ✅ default — 즉시 사용 |
| `liquidity_top50` | 거래대금 상위 50종 | ⏳ 별도 데이터 source 주입 PR 필요 |
| `liquidity_top100` | 거래대금 상위 100종 | ⏳ 위 |
| `liquidity_top300` | 거래대금 상위 300종 | ⏳ 위 |
| `custom` | `--symbols` 로 운영자 명시 지정 | ✅ 즉시 사용 (6-digit 검증) |

**CLI 사용 예시**:

```bash
# default (10종 sample) — 본 PR 시점 즉시 사용 가능.
python scripts/run_backtest_real_data.py

# 명시 sample10 — default 와 동일.
python scripts/run_backtest_real_data.py --universe sample10

# custom 6-digit 명시.
python scripts/run_backtest_real_data.py --universe custom \
    --symbols 005930 000660 035420

# liquidity_top50 — 본 PR 시점 데이터 source 미주입 → 에러 + opt-in 안내.
python scripts/run_backtest_real_data.py --universe liquidity_top50
# [ERR] liquidity_top50: liquidity_source callable required. ...
```

---

## 2. 종목 필터 정책 (`SymbolFilterPolicy`)

`SymbolFilterPolicy` dataclass 의 8 필드 (모두 CLI 로 조정 가능):

| 필드 | 기본값 | CLI 옵션 | 의미 |
|---|---|---|---|
| `min_avg_volume` | `0` (비활성) | `--min-avg-volume N` | 최소 평균 거래량 (주식 수) |
| `min_avg_trading_value` | `0` (비활성) | `--min-avg-trading-value N` | 최소 평균 거래대금 (KRW) |
| `exclude_suspended` | `True` | `--exclude-suspended` | 거래정지 종목 제외 |
| `exclude_managed` | `True` | `--exclude-managed` | 관리종목 제외 |
| `exclude_etf_etn` | `True` | `--exclude-etf-etn` | ETF/ETN 제외 |
| `exclude_spac` | `True` | `--exclude-spac` | SPAC 제외 |
| `min_listed_days` | `180` | `--min-listed-days N` | 상장 minimum days (default 6개월) |
| `max_missing_ratio` | `0.05` | `--max-missing-ratio R` | 데이터 결측 max ratio (default 5%) |

**필터 적용 범위**:
- `kind=sample10` → 필터 *적용 안 함* (10종은 이미 검증된 sample).
- `kind=custom` → 6-digit 형식 검증만 (filter policy 는 운영자 책임).
- `kind=liquidity_top*` → **모든 필터 적용**. 통과 종목만 `symbols` 에 포함.

---

## 3. resolve_universe() 흐름

```
입력 (kind + policy + custom_symbols + liquidity_source)
   │
   ├─ kind=sample10
   │    → REPRESENTATIVE_SYMBOLS 10종 그대로 반환 (필터 미적용)
   │
   ├─ kind=custom
   │    → custom_symbols 검증 (6-digit) → 통과만 반환
   │
   └─ kind=liquidity_top*
        ├─ liquidity_source=None → UniverseDataNotAvailableError
        │   (별도 opt-in PR 안내)
        ├─ liquidity_source 호출 → SymbolMeta list 수신
        ├─ policy 적용 → 통과 / 제외 사유별 카운트
        ├─ avg_trading_value 내림차순 정렬 → top N cap
        └─ UniverseResolution 반환 (symbols + 사유 + operator_note)
```

**핵심 invariant**:
- `liquidity_source` 가 외부 데이터 의존 — 본 모듈은 *fetch 자체 수행 X*.
- 후보가 없으면 **빈 list 반환** (억지 생성 0건).

---

## 4. 단계적 확장 정책

운영자가 본 universe 옵션을 활용할 때 **반드시** 따라야 할 순서:

```
1) sample10 (10종)
       │
       │  파이프라인 동작 확인 + 1차 metric / verdict 검증
       ▼
2) liquidity_top50 (별도 PR 로 데이터 source wiring + 운영자 명시 승인)
       │
       │  50종 운용 결과를 운영자가 *검토* (1주~4주)
       │  - 통과 종목 분포 / Paper 후보 / 위험 신호 점검
       │  - 필터 정책 조정 필요 시 임계 변경 PR
       ▼
3) liquidity_top100 (운영자 옵트인)
       │
       │  100종 결과 검토 후 안정성 확인
       ▼
4) liquidity_top300 (최종 단계 — 별도 옵트인)
```

**금지 사항**:
- sample10 → liquidity_top300 직접 점프 *금지*.
- 필터 비활성화 (`min_listed_days=0` + `exclude_*=False`) 로 전체 종목 강제 실행
  *금지*.
- 후보 0건 시 정책 우회해서 "후보를 만드는" 코드 *금지* — 본 모듈이
  `UniverseResolution.symbols=[]` 반환을 강제.

---

## 5. LIQUIDITY_TOP* 데이터 source 주입 가이드 (후속 PR)

본 PR 시점에는 *주입되지 않음*. 후속 PR 에서 다음 형식의 callable 을 wiring:

```python
from app.backtest.real_data.universe import (
    UniverseKind, SymbolMeta, LiquiditySource,
)

def my_liquidity_source(kind: UniverseKind, top_n: int) -> list[SymbolMeta]:
    """KRX listing / 거래대금 / 유동성 데이터 fetch.

    *주의*: 본 함수가 외부 HTTP 호출 / API key 사용 시 secret 보호 정책 준수.
    """
    # ① KRX listing JSON 또는 CSV 로드.
    # ② 거래대금 / 평균 거래량 / 상장일 / 결측 비율 집계 (최근 N영업일).
    # ③ ETF/ETN/SPAC 플래그 판별 (종목명 패턴 또는 listing 정보).
    # ④ trading_value desc 정렬 후 top_n + α 반환 (필터 후 cap 위해 여유분).
    return [SymbolMeta(...), ...]
```

**주입 PR 체크리스트**:
1. 데이터 source 가 *외부 HTTP* 호출 시 — Secret 정책 (`security_scan.py`) 통과.
2. 데이터 source 가 *KRX listing* fetch 시 — rate limit / 캐시 정책 명시.
3. 데이터 source 가 *DB / file* 의존 시 — 갱신 주기 / staleness 검증 정책.
4. 본 문서 §3 다이어그램 갱신.
5. 운영자 명시 opt-in 코멘트 + PR 승인.

---

## 6. 안전 invariant (테스트로 lock)

| 항목 | 강제 위치 |
|---|---|
| `UniverseKind` 5종 lock (sample10/top50/top100/top300/custom) | `test_real_data_universe.py::TestUniverseKind` |
| `SymbolFilterPolicy` 8 필드 + 범위 검증 | `test_real_data_universe.py::TestSymbolFilterPolicy` |
| `liquidity_top*` + source None → `UniverseDataNotAvailableError` | `test_liquidity_without_source_raises` |
| 후보 없으면 빈 list 반환 (억지 생성 0건) | `test_liquidity_empty_filter_result_returns_empty_list` |
| 사유별 제외 카운트 carry | `test_liquidity_with_source_applies_filter` |
| sample10 은 source 없이 동작 | `test_sample10_does_not_require_liquidity_source` |
| `top_n` cap 으로 잘림 (trading_value desc) | `test_liquidity_respects_top_n_cap` |
| broker / OrderExecutor / route_order import 0건 | 정적 grep |
| 외부 HTTP / AI SDK import 0건 (`anthropic` / `openai` / `httpx` / `requests` / `yfinance`) | 정적 grep |
| `settings.enable_*_trading =` mutate 0건 | 정적 grep |
| schema 에 API key / Secret / 계좌번호 필드 0건 | `test_*_has_no_secret_fields` |
| advisory invariant (`is_order_signal=False` 등) JSON carry | `test_to_dict_carries_advisory_invariants` |

---

## 7. CLI 동작 매트릭스

| `--universe` | `--symbol` (legacy) | 결과 |
|---|---|---|
| (없음) | (없음) | sample10 (10종) — backwards compat |
| `sample10` | (없음) | sample10 (명시) |
| `custom` | (없음) | `--symbols ...` 필요, 없으면 에러 |
| `liquidity_top50` | (없음) | 본 PR: `UniverseDataNotAvailableError` (별도 PR 필요) |
| (없음) | `--symbol 005930 --symbol 000660` | 운영자 명시 list (legacy backwards compat) |
| `sample10` | `--symbol 005930` | legacy `--symbol` 우선 — `--universe` 무시 |

**Recommended**: 새 코드는 `--universe` 사용. legacy `--symbol` 은 backwards
compat 유지용.

---

## 8. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건 (정적 grep 가드).
- ✅ KIS 주문 API 호출 0건. 본 모듈은 universe resolver 만.
- ✅ 실 매수 / 매도 / Place Order 0건.
- ✅ 안전 flag default 변경 0건 — `KIS_IS_PAPER=true` /
  `ENABLE_LIVE_TRADING=false` / `ENABLE_AI_EXECUTION=false` /
  `ENABLE_FUTURES_LIVE_TRADING=false`.
- ✅ secret / API key / 계좌번호 / `.env` 노출 0건.
- ✅ `BACKTEST_PASS` / `OPTIMIZATION_PASS` 라벨은 *분석 라벨* — paper 운용 /
  실거래 활성화 의미 X.

---

## 9. 관련 문서

- [`docs/real_data_backtest.md`](real_data_backtest.md) — 3-02 백테스트 runner
- [`docs/parameter_optimization.md`](parameter_optimization.md) — 3-03 파라미터 최적화
- [`docs/strategy_portfolio.md`](strategy_portfolio.md) — 6 전략 모듈 → 4 매매기법군 매핑 (#0-02)
- [`docs/live_readiness_policy.md`](live_readiness_policy.md) — AI Paper / AI Live 단계 분리 (#0-01)
- [`docs/paper_candidate_aggregator.md`](paper_candidate_aggregator.md) — 3-07 Paper 후보 통합
