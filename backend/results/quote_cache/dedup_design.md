# 시세 호출 dedup/캐시 패치 — 1단계 설계 + diff 초안 (read-only)

> 작성 2026-06-24. **read-only 조사 단계 — 코드 0 변경.** 적용은 승인 후 별도.
> 대상: 시세 조회 경로(`app/market_data/kis_realtime.py`)만. 보호 4계층·driver_bridge 판단부·손절 임계·청산 로직 0줄.

---

## 0. ★핵심 결론 (먼저)

조사 결과 **task의 전제가 실측으로 일부 뒤집혔다**:

1. **"스캔 vs 청산이 inquire-price를 각자 호출한다"는 이미 거짓** — 한 틱 안에서 종목당 `build_kis_market_input` **1회만** 호출하고, 그 `quote`(inquire-price 1회)를 진입·청산이 **공유**한다(`driver_bridge.py:692-755`). 틱내 가격 중복은 이미 없다.
2. **종목당 34.6회는 틱내 중복이 아니라 "하루 종일 반복 스캔"의 누적** — 틱마다 1회씩, 약 600틱 × union(~15종목) ≈ 9000 ≈ 실측 7,010.
3. **진짜 최대 볼륨·최대 안전 레버는 inquire-price가 아니라 분봉(`inquire-time-dailychartprice`)** — 7,871건(전체 50%), 종목당 매 틱 재조회, **진입 신호 전용**(청산 미사용)이라 캐시해도 **stale 위험 0**.
4. inquire-price는 **재시도 없음**(증폭 안 함) + 청산이 의존 → **캐시 부적합/이득 적음**. 보유종목은 절대 캐시 금지(손절 신선도).

→ **권고: inquire-price 캐시 대신, 분봉(오늘자 intraday) TTL 캐시로 방향 전환.** 가장 크고(7,871) 가장 안전한(청산 무관) 절감.

---

## 1. ★중복 출처 규명 — 34.6회/종목이 어디서 나나

### 실측 (06-24 로그, 봇 10:33~15:30)
| 엔드포인트 | 호출수 | 비중 | 재시도 | 용도 |
|---|---|---|---|---|
| `inquire-time-dailychartprice` (분봉) | **7,871** | 50% | **있음(≤4회, EGW00201)** | 진입 신호(30분봉) **전용** |
| `inquire-price` (현재가) | 7,010 | 44% | **없음**(단발) | 진입 sizing + **청산 평가 공유** |
| `inquire-balance` (잔고) | 838 | 5% | 없음 | 보유맵/현금 |
| `inquire-daily-ccld` | 83 | <1% | 없음 | 체결확인 |
| `order-cash` (주문) | 47 | <1% | 없음 | 주문(rate-limit 면역) |
| **합계** | **15,851** | | | |

### 호출 지점 전수 (live tick)
- 단일 진입점: `driver_bridge._run_kis_paper_pipeline` 루프 `for symbol in symbols`(`:692`).
  - `symbols = 회전윈도우(scan, ≤10) ∪ held(보유, ≤~10)` (`:681`, set union → **종목당 1회**).
  - 각 종목 `mi, quote = await build_kis_market_input(...)` (`:693`).
- `build_kis_market_input`(`kis_realtime.py:435`):
  - `quote = await fetch_realtime_quote(...)` → `client.get_price` = **inquire-price 1회**(`:452`, `:224`).
  - `today_min = await _fetch_full_day_minutes(...)` = **inquire-time-dailychartprice 최대 5회**(`:462` → `:358-373`, hour 역행하며 break-early, 장중 보통 1~2회).
  - `prior_60m = await _prior_days_bars(...)` = **이미 캐시됨**(하루 1회, `:386` `if ck in _prior_bars_cache`). ← 과거일은 캐시 완료, 갭 아님.

### 재시도 증폭 분해
- **inquire-price: 증폭 0** — `fetch_realtime_quote`(`:223-231`)는 단발 try/except, 실패 시 unavailable quote 반환(재시도 루프 없음). 500 → 그 틱 해당종목 skip. **자기제한적.**
- **분봉: 증폭 ≤4×** — `_paced_inquire`(`:341-354`) EGW00201 시 `0.6/1.2/1.8s 백오프 재시도`. 혼잡 시 1콜→최대 4콜. **cycle 14(12분 동결)의 증폭원이 바로 이 경로**(`_fetch_full_day_minutes`가 종목당 최대 5콜 × 재시도 4 = 최대 20콜).

**결론**: 34.6/종목 = (분봉 ~1.1/종목/틱 × 600틱) + (현재가 1/종목/틱 × 600틱)을 종목 수로 나눈 누적. **틱내 중복이 아니라 틱 반복**. inquire-price 틱내 중복 호출 = 같은초 중복 16건뿐(틱 경계 overlap, 무시 가능).

---

## 2. ★스캔·청산 시세 공유 가능 범위

| 데이터 | 진입(스캔) | 청산(손절/익절) | 공유 현황 |
|---|---|---|---|
| **현재가**(inquire-price `quote.price`) | sizing/가격 | `PositionContext.current_price`(`:722,732`) | **이미 공유**(틱당 1회, 둘 다 같은 quote) |
| **30분봉**(inquire-time-dailychartprice) | `recent_closes`/ORB/vwap → voter | **미사용** (청산은 현재가만, `:697-700` 명시) | 청산 무관 → **캐시 안전** |

- 신선도 요구: **청산은 실시간 현재가 필수**(손절은 지금 가격 기준). **분봉은 진입 신호용**이라 수십초~수분 stale 허용(30분봉은 30분마다만 확정, 장중 partial bar의 close만 미세 변동).
- → **공유/캐시 가능한 건 분봉**. 현재가는 이미 공유됐고, 보유종목은 신선도 때문에 캐시 불가.

---

## 3. ★캐시 위치·TTL + stale 안전 설계 (청산 보호)

### 위치
`kis_realtime.py`의 **`_fetch_full_day_minutes` 결과(오늘자 분봉)** 에만 캐시 래퍼 추가. `build_kis_market_input`이 호출하는 함수 한 곳 교체. **그 외 0 변경.**

### TTL / 키
- 키: `(symbol, asof_date_kst)`.
- TTL: **기본 180s 제안**(설정화). 근거: 30분봉은 30분마다만 확정 → partial bar의 close 변화만 누락, 진입 신호엔 무해. TTL을 30분 슬롯 경계-aware로 더 키울 수도 있으나, 보수적으로 180s(=평균 사이클 2.7분과 유사)로 시작해 효과 측정 후 상향.
- 과거일 분봉(`_prior_days_bars`)은 **이미 일 단위 캐시** — 변경 불필요.

### ★stale 안전 (불변식)
1. **청산/손절은 캐시를 절대 거치지 않는다** — 청산은 `quote.price`(`fetch_realtime_quote`→`inquire-price`, **항상 신선, 캐시 없음**)만 사용. 캐시는 `mi`(진입 voter 입력)에만 영향. 손절 평가 경로(`PositionContext`, `:718-735`) 무접촉.
2. **inquire-price는 캐시하지 않는다** — 현재가는 진입 sizing + 청산이 공유하므로, 캐시하면 손절이 낡은 가격으로 평가될 위험. **캐시 대상에서 제외**(보유·비보유 불문). (비보유 한정 초단 TTL은 이득<위험으로 *비권장* — 4절.)
3. 캐시는 **진입 데이터(분봉)만, 신선도 둔감 영역만**.

→ **손절/청산은 패치 전후 100% 동일 신선도.** 캐시가 청산을 stale하게 만들 경로가 코드상 없음(STOP 조건 회피).

---

## 4. ★diff 초안 (적용 0) + 예상 효과

### (A) 오늘자 분봉 TTL 캐시 — 주 패치
```python
# kis_realtime.py — 신규(시세층 한정)
_today_min_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_TODAY_MIN_TTL = float(_env_or("KIS_TODAY_MIN_TTL_SEC", 180))  # 진입 분봉 전용. 청산 무관.

async def _fetch_full_day_minutes_cached(client, symbol, date) -> list[dict]:
    """오늘자 분봉 = 진입 신호 전용 → 짧은 TTL 캐시. 청산(현재가)은 본 캐시 미경유."""
    ck = (symbol, date)
    mono = _time.perf_counter()
    hit = _today_min_cache.get(ck)
    if hit and (mono - hit[0]) < _TODAY_MIN_TTL:
        return hit[1]
    bars = await _fetch_full_day_minutes(client, symbol, date)   # 기존 함수 그대로
    if bars:                              # 빈 결과는 캐시 안 함(다음 틱 재시도)
        _today_min_cache[ck] = (mono, bars)
    return bars
```
```python
# build_kis_market_input  (kis_realtime.py:462)  — 한 줄 교체
-           today_min = await _fetch_full_day_minutes(client, symbol, date)
+           today_min = await _fetch_full_day_minutes_cached(client, symbol, date)
```
- 영향 범위: **시세 가져오는 층 1함수**. 진입 voter 입력만. 보호계층/판단부/청산/손절 임계 **0줄**.

### (B) EGW00201 백오프↑ — 보조(분봉 경로만)
```python
# _paced_inquire (kis_realtime.py:352) — 백오프 상수만 상향
-                await asyncio.sleep(0.6 * (attempt + 1))   # 0.6/1.2/1.8s
+                await asyncio.sleep(1.0 * (attempt + 1))   # 1.0/2.0/3.0s (초당한도 재충돌 완화)
```
- inquire-price엔 재시도 자체가 없으므로 백오프 대상 아님(500 → skip = 자기제한).
- 페이싱(`_FETCH_MIN_INTERVAL` 0.6→0.8) 상향도 옵션이나 **스윕 길어짐 trade-off** → (A) 적용 후 효과 보고 결정 권장(이번엔 미포함).

### 예상 효과 (데이터 기반 추정)
- **분봉 호출 7,871 → 추정 −40~55%** (TTL 180s, 종목 재평가 간격이 TTL보다 짧은 비율만큼 재사용). 보수적으로 ~4,000~4,700.
- **틱당 분봉 버스트 제거**: 같은 종목이 짧은 간격에 재평가될 때(보유 union은 매 틱) 분봉 재조회가 사라짐 → **cycle 14식 12분 동결의 증폭원 직접 완화**. 사이클 길이/청산 지연 동반 개선.
- **inquire-price: 변화 없음**(의도) — 이미 1/종목/틱, 청산 신선도 유지.
- 총 KIS 호출 15,851 → 추정 ~12,000 (−24%), rate-limit(500=EGW00201) 동반 감소 예상. **400 확장 시 분봉 부하가 선형 증가하던 것을 캐시가 흡수** → 400 안전화의 핵심.

---

## 5. ★불변 확인 — 보호계층 0줄 / 청산 신선도 보장

- [x] 보호 4계층(RiskManager/PermissionGate/OrderExecutor/route_order) **무접촉** — 패치는 `kis_realtime.py` 시세층.
- [x] driver_bridge 판단부(`run_agent_council`, 투표/veto/승인) **무접촉**.
- [x] 손절/익절 임계(`effective_stop_loss_pct/take_profit_pct`), `PositionContext`, 청산 트리거 **무접촉**.
- [x] **청산은 캐시 미경유** — `quote.price`(inquire-price, 캐시 없음)만 사용. 캐시는 진입 분봉만.
- [x] 주문 0건 — 시세 read-only.
- [x] mock 대체 0 — 실패 시 빈 결과(캐시 안 함) → 다음 틱 재조회(기존 거동 보존).

### STOP 점검
- dedup이 청산을 stale로? → **불가**(청산은 캐시 안 거치는 inquire-price). 통과.
- 캐시가 판단부 수정 요구? → **불요**(시세층 1함수 교체). 통과.
- 보호계층 접촉? → **없음**. 통과.

---

## 6. 다음 단계 (승인 후)
1. (A) 분봉 TTL 캐시 적용 + 단위테스트(캐시 hit/miss, 빈결과 비캐시, 청산경로 캐시 미경유 검증).
2. dry-run으로 분봉 호출수·사이클 길이 before/after 측정.
3. 효과 확인 후 (B) 백오프↑/페이싱 조정 여부 결정.
4. 그 다음에야 size=400 검토.

**요약**: inquire-price는 이미 공유돼 손댈 게 없고(청산 신선도 때문에 손대면 안 됨), **진짜 절감은 진입 전용 분봉(7,871)의 TTL 캐시** — 청산과 완전 분리돼 안전하며, cycle 동결·400 부하의 핵심 완화책.
