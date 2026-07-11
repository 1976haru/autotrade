# 연패 차단 쿨다운 — 시도횟수 → 시간 기준 재검증 + 설계 수정

> `results/circuit_breaker/design.md` 후속. 코드 변경은 별도 커밋(설정 배선만,
> `#36 ConsecutiveLossRule` 판정 로직 자체는 `loss_limits.py` 미변경). 이 문서는
> 그 코드 변경의 근거가 된 재검증 백테스트를 기록한다. read-only 분석 +
> 구현 근거 문서 — 백테스트 자체는 과거 데이터 분석일 뿐 미래 수익을 보장하지
> 않는다.

## 문제

`design.md` §8의 권장 설계("cooldown: next 5 BUY candidates, or 30~60 minutes
if candidate count is unavailable")를 그대로 구현한 첫 버전(commit
`f9f4d34`)은 "차단된 BUY *시도* 횟수 5회"로 쿨다운을 소진시켰다. 그런데 실제
라이브 봇은 300종목을 30초 틱마다 한번에 스캔한다 — 여러 종목이 같은 틱에
동시에 BUY 후보를 낼 수 있어 "5회"가 사실상 같은 틱 안에서 즉시 소진될 수
있다. "연패 후 몇 십 분 쉬기"라는 설계 의도와 실제 동작(시도 횟수)이
어긋난다.

## 1. "다음 5개 후보"가 실제로 몇 분이었나

`design.md`와 동일 corpus(35종목, 2025-05-15~2026-05-26, MTF 5m entry +
60m/AGENT_COUNCIL confirm, 비용 33bps + 미체결역선택 5%)로 재현:

| 항목 | 값 |
|---|---:|
| 실행풀 n | 24,664 (design.md 기대값과 일치) |
| 실행풀 누적net | -6,673.67%p (design.md 기대값과 일치) |
| "다음 5개 후보" episode 수 | 968 |
| 소요시간 중앙값 | **10.0분** |
| 소요시간 평균 | 210.8분 (긴 꼬리 — 장마감 넘어가는 episode 포함) |

**35종목 기준으로도 이미 중앙값 10분**이었다. 300종목(신호빈도 ≈8.6배 가정)
스케일로 추정하면 **중앙값 ≈1.2분** — 설계 의도(30~60분)와 자릿수가 다르다.
즉 "시도 횟수" 기준은 원래 데이터에서도 이미 설계 의도보다 짧았고, 실제
운영 스케일에서는 사실상 무력화된다.

## 2. 시간 기준 쿨다운 재시뮬레이션

같은 실행풀에서 "5연패 시점 → 그 시점의 candidate timestamp 기준 N분 경과할
때까지 신규 후보 skip → streak reset"으로 시뮬레이션(재현 스크립트:
`backend/scripts/_tmp_circuit_breaker_time_cooldown.py`, 비커밋):

| N(분) | skip 건 | skip 승률 | skip 평균 | 차단 후 누적net | 베이스라인 대비 개선 |
|---:|---:|---:|---:|---:|---:|
| 15  | 5,243  | 10.36% | -1.0114% | -1,370.92%p | +5,302.74%p |
| 30  | 7,403  | 12.74% | -0.9703% |    509.37%p | +7,183.04%p |
| 45  | 8,763  | 14.91% | -0.9161% |  1,353.96%p | +8,027.63%p |
| **60**  | 9,766  | 16.64% | -0.8824% |  1,943.47%p | **+8,617.14%p** |
| 90  | 11,204 | 19.83% | -0.7960% |  2,244.55%p | +8,918.22%p |
| 120 | 12,086 | 21.60% | -0.7417% |  2,290.07%p | +8,963.74%p (최대) |
| 180 | 14,022 | 25.98% | -0.6163% |  1,968.59%p | +8,642.26%p (하락 전환) |

참고 — 원 규칙("다음 5개 후보", 시도횟수 기준) 재현: skip 4,840건, 차단 후
누적net -1,800.06%p, 베이스라인 대비 **+4,873.61%p** (design.md 수치와 일치).

## 3. 해석

- **시간 기준이 모든 N에서 시도횟수 기준(next-5, +4,873.61%p)보다 낫다.**
  30분만 줘도(+7,183%p) 이미 원 규칙을 크게 앞선다 — "시도 횟수"가 애초에
  방어 효과를 스스로 깎아먹는 설계였다는 뜻.
- **60~120분이 최대 개선폭(+8,617~+8,964%p) 구간**, 180분부터는 하락 전환.
  Skip 승률이 N이 늘수록 오른다(10.4%→26.0%) — 쿨다운이 길어질수록
  "진짜 나쁜 후보"뿐 아니라 회복 중인 괜찮은 후보까지 같이 막기 시작한다는
  신호(특이성 저하).
- **60분을 기본값으로 선택**: 최대 개선폭의 ~97%(+8,617 / +8,964)를 확보하면서,
  skip 승률(16.64%)이 베이스라인(39.86%)과 여전히 뚜렷이 구분됨 — "덜 나쁜
  후보까지 과도하게 차단"하기 시작하는 지점(90분 이후 승률 20%대 진입)
  이전에서 멈춘다. 90~120분은 대안(더 보수적 운용 원하면 검토 가능).

## 4. 구현

- `daily_pnl.get_consecutive_loss_state`: 반환값에 최근 손실 SELL의
  `created_at`(UTC) 추가 — 시간 계산의 앵커.
- `risk_manager.RiskPolicy`: `consecutive_loss_cooldown_buys`(int, 시도횟수)
  → `consecutive_loss_cooldown_minutes`(int, 분) 필드명 변경.
- `order_router.route_order`: "차단된 BUY 이후 REJECTED 로그를 다시 세는"
  헬퍼(`_count_consecutive_loss_cooldown_buys`, DB 재조회 필요)를 제거하고
  `datetime.now(UTC) - latest_losing_sell_at >= timedelta(minutes=...)` 비교로
  대체 — 코드도 더 단순해짐(로그 재조회 쿼리 1개 제거).
- `config.py`: `risk_consecutive_loss_cooldown_minutes` 기본값 **60**.
- `#36 ConsecutiveLossRule`(`loss_limits.py`) 판정 로직 자체는 미변경 —
  SELL/EXIT 항상 통과 불변식 그대로 유지.

## 5. 안전 확인

- 4계층(`RiskManager`/`OrderGuard`/`PermissionGate`/`OrderExecutor`) 중
  `order_executor.py`/`executor.py`/`permission/gate.py`/`order_guard.py`/
  `loss_limits.py` diff 0줄 — 배선만 변경.
- SELL/EXIT 항상 통과 테스트 유지(`test_consecutive_loss_cooldown_does_not_block_sell_exit`).
- 신규 회귀 테스트: 다종목 동시burst(10건 동시 BUY 시도)가 전부 차단 유지되는지
  직접 검증(`test_consecutive_loss_cooldown_blocks_rapid_multi_symbol_burst`) —
  구버전 결함의 재발 방지 테스트.
- 관련 테스트(order_router/risk_manager/loss_limits/order_guard) 182개 통과,
  전체 백엔드 스위트 8,483 passed / 37 failed — 실패 37건은 변경 전(f9f4d34)
  기준선에서도 동일하게 실패(`git stash` 전후 비교로 확인, 네트워크/백업파일
  의존 등 기존 이슈, 이번 변경과 무관).
