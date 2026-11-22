# 소유권 원장 (Ownership Ledger) — 설계 문서

> 작성: 2026-07-02. **read-only. 봇·보호계층 0 변경.**
> 목적: 봇은 "자기가 산 포지션만" 청산 평가. 수동 매수는 절대 안 건드림.
> 한 계좌에서 자동매매(단타) + 수동/장기보유 공존.

---

## 요약 (TL;DR)

| 항목 | 결론 |
|---|---|
| 새 DB 테이블 필요? | **불필요** — `order_audit_log.trade_reason='kis_paper_auto'`가 이미 원장 |
| 변경 파일 | `driver_bridge.py` (+~20줄) + `test_stoploss_...py` (+~10줄 DB seed) |
| 손절 로직 자체 변경? | **0줄** — 입구(대상 선정)에 필터만 |
| 소급 처리 | **즉시 완료** — 기존 424건 BUY 레코드가 이미 원장 |
| fail-safe | **ledger 조회 실패 → 전부 봇소유로 간주** (손절이 죽는 게 더 치명적) |
| STOP 조건 | 없음 — 입구 필터로 안전하게 구현 가능 |

---

## 1. 소유권 원장 구조 — 기존 데이터 재활용

### 핵심 발견: 원장이 이미 존재한다

`order_audit_log` 테이블에서:
```sql
SELECT DISTINCT symbol
FROM order_audit_log
WHERE trade_reason = 'kis_paper_auto'
  AND side = 'BUY'
  AND decision = 'APPROVED'
  AND filled_quantity > 0
```

이 쿼리가 곧 "봇이 매수한 적 있는 종목 원장"이다.

**근거**:
- 봇의 모든 주문은 `auto_executor.py:414`에서 `trade_reason="kis_paper_auto"`로 고정 태깅됨
- 수동으로 KIS에서 직접 매수한 종목은 이 태그가 없음 (DB에 BUY 레코드 자체가 없음)
- 현재 DB: BUY 체결 424건 전부 `'kis_paper_auto'` 태그 확인 (진단 테스트 2건 제외)

```
BUY fills by trade_reason:
  'kis_paper_auto'        count=424  (진짜 봇 자동매매)
  'fill_probe_diagnostic' count=1    (테스트 무시)
  'fill_detection_test'   count=1    (테스트 무시)
```

### 새 테이블을 만들지 않는 이유

새 `ownership_ledger` 테이블을 만들면:
- **동기화 위험**: BUY 체결 시 두 곳에 써야 함 → 한 쪽 실패 시 불일치
- **불필요한 중복**: `order_audit_log`가 이미 ground truth
- **마이그레이션 부담**: 기존 424건을 수동으로 옮겨야 함

**결론**: `order_audit_log`를 원장으로 직접 쿼리하는 헬퍼 함수로 충분.

### 부분 보유 처리 (같은 종목을 봇+수동이 같이 보유)

**종목 단위로 처리** (수량 분할 없음):

```
봇이 산 적 있는 종목 → 봇 소유 → 손절 평가 대상
봇이 산 적 없는 종목 → 수동 소유 → 손절 평가 제외
```

수량 분할(봇 보유 N주, 수동 보유 M주 → N주만 손절)은 2차로 분리.
1차는 "종목이 봇 원장에 있는가"로 단순 분기.

**같은 종목을 봇도 수동도 산 경우**: 봇 원장에 있으므로 → 봇 소유로 처리.
이 경우 수동 보유분도 같이 평가될 수 있으나, 1차에서는 이 엣지 케이스를 감수.
(실제로 이 상황 발생 시 종목을 계좌 분리로 해결하는 게 올바른 운영 방법)

---

## 2. 청산 루프 필터 지점 — 손절 로직 0줄 변경

### 현재 흐름 (driver_bridge.py)

```
line 691: held_map = await _kis_held_map(...)   # KIS 잔고 전체
line 692: held_symbols = set(held_map.keys())    # 모든 KIS 보유 종목 (봇+수동 혼재)
line 695: symbols union → held_symbols 추가       # T1 fix: 보유 종목 항상 스캔
...
for symbol in symbols:
  line 732: _position = None
  line 733: if symbol in held_symbols:            # KIS에 있는 모든 보유
  line 734-761:  _position = PositionContext(...) # ← 여기가 손절 평가의 입구
  line 762: council = run_agent_council(..., position=_position)
  ...
  line 814: if final==SELL and symbol not in held_symbols: skip  # SELL 가드
```

### 필터를 꽂을 위치 (2곳)

**위치 A (필수): `_position` 설정 조건 (line 733)**

```python
# 현재:
if symbol in held_symbols:
    _position = PositionContext(...)

# 변경 후:
if symbol in held_symbols and symbol in _bot_owned:  # ← 소유권 체크 추가
    _position = PositionContext(...)
```

이것만으로 stop_loss/take_profit **강제청산은 차단**된다.
`_position=None`이면 `council`의 forced-exit 트리거가 없으므로.

**위치 B (완전 차단): SELL 사전 가드 (line 810 부근)**

```python
# 현재:
if final == CouncilAction.SELL and symbol not in held_symbols:
    skipped(SELL_NO_HELD_POSITION)

# 추가:
if final == CouncilAction.SELL and symbol in held_symbols \
        and symbol not in _bot_owned:
    skipped(SELL_NOT_BOT_OWNED)   # ← 수동 보유 SELL 차단
    continue
```

위치 A는 position-based forced exit를 막고,
위치 B는 signal-based SELL까지 완전 차단한다.
**양쪽 모두 적용** — "절대 안 건드림"이라면 B까지 필요.

### ★손절 로직 자체 변경 = 0줄

`PositionContext`, `infer_position_sell_reason`, `run_agent_council`의 stop/take 판단 코드 무변경.
"`_position`을 설정할지 말지"의 **입구 조건**만 추가되는 구조 ✓

### 헬퍼 함수 위치

스캔 루프 진입 직전 (line 663의 `symbols = _scan_universe_symbols(...)` 직후):

```python
_bot_owned = _bot_owned_symbols(db)  # ← 루프 밖, 1회 쿼리
```

루프 안에서 per-symbol로 DB 쿼리하면 RTT × symbol 수 = 300회 과다 호출.
**루프 밖 1회 쿼리, set으로 O(1) 조회**가 정답.

---

## 3. 기존 보유 소급 처리 (마이그레이션)

### 결론: 소급 처리가 이미 완료됨

`order_audit_log`의 `trade_reason='kis_paper_auto'` 레코드 = 봇이 산 기록 전체.
지금 계좌에 있는 보유 종목들의 BUY 기록도 이미 DB에 있다.

```
현재 DB 추정 보유 (06-22~07-02 net): 082640, 323410, 002790, 096530,
028300, 000880, 005830, 033780, 004370 — 전부 trade_reason='kis_paper_auto' BUY 존재
```

### "봇이 샀다" 증명 불가능한 종목이 있나?

시나리오 A: **오래 전 수동으로 산 종목이 지금도 KIS 계좌에 있는 경우**
- DB에 BUY 레코드 없음 → `_bot_owned`에 미포함 → 자동으로 수동 보유로 처리 ✓

시나리오 B: **봇 초창기(DB 도입 전) 매수한 종목**
- DB 도입 시점(약 06-22)보다 이전 매수: BUY 레코드 없음 → 수동 처리와 동일
- 그 종목을 봇이 청산해야 하면 → 수동 등록(아래 참조)이 필요

### 수동 등록 방법 (예외 케이스)

봇이 샀는데 DB 기록이 없는 경우(DB 이전 매수 등):
```sql
INSERT INTO order_audit_log
  (created_at, symbol, side, filled_quantity, avg_fill_price,
   decision, executed, trade_reason, source)
VALUES
  ('2026-06-01 01:00:00', '005930', 'BUY', 5, 350000,
   'APPROVED', 1, 'kis_paper_auto', 'manual_backfill');
```

이렇게 하면 헬퍼 쿼리에 잡힌다. 감사 로그 포함(created_at 명시).

---

## 4. ★ Fail-safe 방향 — 가장 중요한 설계 결정

### 두 선택지

**(a) Fail-Open**: 원장 조회 실패 → **전부 봇 소유**로 간주 → 손절 평가 계속
- 비용: 수동 보유 종목이 손절 당할 수 있음 (회복 가능 — 재매수)
- 이득: 봇 포지션 손절이 죽지 않음

**(b) Fail-Closed**: 원장 조회 실패 → **전부 수동**으로 간주 → 모든 SELL skip
- 비용: **봇 포지션 손절이 죽음** — 손실 포지션이 무제한 적립
- 이득: 수동 보유 보호

### ★ 권고: **(a) Fail-Open**

**근거: 우리 프로젝트의 역사**

> "손절이 죽는 게 더 치명적이었던 우리 역사"

06-25 STOP_LOSS 0건 사고: avg=0 → position=None → 손절 평가 skip → 손실 포지션 미청산.
이것이 얼마나 치명적이었는지 경험했다. 그 수정에 패치 3개(A+B+C)와 E2E 14개 테스트가 필요했다.

- **수동 보유 손절 당함**: 1주 20만원짜리 팔림 → 재매수로 복구 가능
- **봇 포지션 손절 죽음**: 손실 포지션이 계속 커짐 → 운영자가 못 보면 큰 손실

원장 조회 실패(DB 장애/파일 깨짐)는 **어떤 경우에도 손절이 죽으면 안 된다.**

### fail-safe 구현

```python
def _bot_owned_symbols(db: Session) -> frozenset[str]:
    """봇이 BUY 체결한 종목 집합. 조회 실패 시 None 반환."""
    try:
        rows = (db.query(OrderAuditLog.symbol)
                .filter(OrderAuditLog.trade_reason == "kis_paper_auto",
                        OrderAuditLog.side == "BUY",
                        OrderAuditLog.decision == "APPROVED",
                        OrderAuditLog.filled_quantity > 0)
                .distinct().all())
        return frozenset(r[0] for r in rows)
    except Exception:
        return None   # ← None = 조회 실패 신호

# 스캔 루프에서:
_bot_owned = _bot_owned_symbols(db)
_ledger_ok = _bot_owned is not None
if not _ledger_ok:
    _log.error("[ownership-ledger] 원장 조회 실패 — fail-open: 전부 봇소유로 처리")
    # _bot_owned = None 일 때: 아래 체크에서 None → 전부 통과
```

```python
# 위치 A (position 설정):
if symbol in held_symbols:
    _is_bot_owned = (_bot_owned is None) or (symbol in _bot_owned)  # fail-open
    if _is_bot_owned:
        _position = PositionContext(...)

# 위치 B (SELL 차단):
if final == CouncilAction.SELL and symbol in held_symbols:
    _is_bot_owned = (_bot_owned is None) or (symbol in _bot_owned)
    if not _is_bot_owned:
        skipped(SELL_NOT_BOT_OWNED)
        continue
```

`_bot_owned is None` → 모든 symbol이 `_is_bot_owned=True` → fail-open 동작 ✓

---

## 5. 회귀 위험 점검

### (a) 기존 손절 패치(A-1, B, C)와 충돌

| 패치 | 충돌 가능성 |
|---|---|
| **A-1** avg=0 스냅샷 복구 (line 576-589) | ✗ 없음. `_kis_held_map`에서 avg를 복구한 후에 `_bot_owned` 체크를 하므로 순서 무관 |
| **B** is_risk_exit 게이트 면제 | ✗ 없음. `_bot_owned` 체크는 `_position` 설정 단계이고, gate 면제는 executor 단계 |
| **C** 절대 손절가 계산 | ✗ 없음. `_position`이 설정되는 경우의 내부 로직 — `_bot_owned` 체크 후에 실행 |

### (b) 기존 E2E 테스트 14개 영향 ← ★ 주의

**현재 테스트 구조**:
- `test_stoploss_exit_gate_and_avg_recovery.py`의 `test_E2E_stoploss_fires_at_1pct_with_avg0_recovery`
- Mock broker + Mock session_factory 사용
- **DB에 `kis_paper_auto` BUY 레코드 없음** → `_bot_owned = frozenset()` (빈 집합)

**회귀 위험**:
필터 구현 후 E2E 테스트는 `_bot_owned`가 비어 있어 → 모든 symbol이 비소유 → `_position=None`, SELL 차단 → 손절 E2E가 FAIL

**해결책 (2단계에 포함)**:
테스트용 `session_factory`가 반환하는 mock DB에 BUY 레코드 1건 seed:
```python
# test fixture에 추가:
db.add(OrderAuditLog(
    symbol=TEST_SYMBOL, side="BUY", trade_reason="kis_paper_auto",
    decision="APPROVED", filled_quantity=10, avg_fill_price=10000,
    created_at=datetime.now(timezone.utc)
))
db.commit()
```

또는: `_bot_owned_symbols`를 injectable(주입 가능)로 만들어 테스트에서 override:
```python
async def kis_paper_realtime_scan_tick(
    ...,
    _bot_owned_override: frozenset[str] | None = None,  # 테스트 주입용
) -> dict:
    _bot_owned = _bot_owned_override if _bot_owned_override is not None \
                 else _bot_owned_symbols(db)
```

→ injectable 방식이 테스트를 DB state에 덜 의존시켜 더 낫다.

### (c) 오버나이트 sleeve (trade_reason 태그)와의 관계

현재 모든 봇 BUY = `'kis_paper_auto'`. 오버나이트 슬리브가 별도 태그(`'overnight_sleeve'` 등)를 쓰게 된다면 — 현재는 미구현이므로 해당 없음. 구현 시 `_bot_owned_symbols` 쿼리에 OR로 추가하면 됨.

---

## 6. Dry-run 계획 (2단계에서 실행)

### (a) 봇 포지션 → 여전히 손절 발동하는가

```python
# 테스트 시나리오:
# 1. symbol='005930'을 DB에 BUY 레코드로 seed (trade_reason='kis_paper_auto')
# 2. held_map = {'005930': {hldg:5, avg_price:10000}}
# 3. current_price = 9900 (손절선 1% 아래)
# 4. scan_tick 실행
# 기대: council SELL 발동, STOP_LOSS 제출
```

### (b) 원장 밖(수동) 포지션 → 청산 평가에서 제외

```python
# 테스트 시나리오:
# 1. DB에 '000660' BUY 레코드 없음 (수동 보유)
# 2. held_map = {'000660': {hldg:1, avg_price:300000}}
# 3. current_price = 280000 (손절선 아래)
# 4. scan_tick 실행
# 기대: '000660' SELL 제출 0건, reason='SELL_NOT_BOT_OWNED'
```

### (c) 원장 조회 실패 → fail-open 동작

```python
# 테스트 시나리오:
# 1. session_factory가 DB 오류 발생
# 2. held_map = {'005930': {hldg:5, avg_price:10000}}
# 3. current_price = 9900
# 4. scan_tick 실행
# 기대: _bot_owned=None → fail-open → 손절 발동 (봇 소유로 처리)
```

---

## 7. 예상 변경 규모

| 파일 | 변경 유형 | 예상 줄 수 |
|---|---|---|
| `app/kis_paper/driver_bridge.py` | 헬퍼 함수 추가 + 필터 2곳 + 주입 파라미터 | +20~25줄 |
| `tests/test_stoploss_exit_gate_and_avg_recovery.py` | E2E 테스트 DB seed 또는 override 주입 | +8~12줄 |
| `tests/test_ownership_filter.py` (신규) | 시나리오 a/b/c dry-run 테스트 3~5개 | +60~80줄 |

**총 3파일, +90~115줄. 손절 로직(PositionContext, run_agent_council, infer_position_sell_reason) 0줄 변경.**

---

## 8. STOP 조건 확인

- 손절 로직 자체 수정 필요? → **NO** (입구 필터로 안전하게 구현 가능)
- 보호 4계층(RiskManager/PermissionGate/OrderExecutor/route_order) 변경? → **NO**
- **STOP 조건 없음 — 2단계 구현 진행 가능**

---

## 부록: 헬퍼 함수 시그니처 (참고용)

```python
def _bot_owned_symbols(db: Session) -> frozenset[str] | None:
    """
    봇이 체결한 BUY 종목 집합.
    Returns:
        frozenset[str] : 봇 소유 종목 코드 집합 (비어있어도 frozenset)
        None           : DB 조회 실패 → fail-open (호출자가 전부 봇소유로 처리)
    """
```

```python
# driver_bridge.py scan tick 파라미터 추가 (테스트 주입용):
async def kis_paper_realtime_scan_tick(
    ...
    _bot_owned_override: frozenset[str] | None = ...,  # 테스트 전용; None=미주입
) -> dict[str, Any]:
```

---

*봇 코드 0 변경. 보호 4계층 0 변경. 2단계에서 구현 + dry-run.*
