"""Monte Carlo risk simulation for backtest trade logs (#26).

거래 순서를 섞거나(shuffle), 복원추출(bootstrap), 연속 블록 추출(block bootstrap)
하여 N회 시뮬레이션하고, 각 시도의 누적 PnL 곡선에서 MDD / total_pnl /
losing streak를 산출한다. percentile 분포로 risk_of_ruin / worst_5pct_avg_mdd /
p05 PnL / p95 MDD 등을 추정한다.

본 모듈은:
- broker / RiskManager / PermissionGate / OrderExecutor import 0건.
- 외부 네트워크 호출 0건. random.Random(seed)만 사용 — CI 결정성.
- read-only 분석 — 주문 신호가 아니라 *전략 리스크 검증*.
- Monte Carlo 결과만으로 전략 승인 금지 (`docs/monte_carlo_policy.md`).

CLAUDE.md 절대 원칙 — 본 모듈은 어떤 LIVE flag와도 무관하며, BUY/SELL 결정을
반환하지 않는다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.backtest.metrics import _safe_float, extract_trade_pnl


# ---------- 상수 (정책 임계, monte_carlo_policy.md와 lockstep) ----------


# 파산 임계 — equity가 initial_cash × (1 + RUIN_DRAWDOWN_PCT)이하로 떨어지면 ruin.
# 기본 -50% — 보수적 단타 운영 가정. 정책에서 운영자가 조정 가능.
DEFAULT_RUIN_DRAWDOWN_PCT = -0.5

# promotion_risk_flag 기준.
RISK_OF_RUIN_FAIL_THRESHOLD     = 0.10   # 10% 이상이면 FAIL
RISK_OF_RUIN_CAUTION_THRESHOLD  = 0.05   # 5% 이상이면 CAUTION


# ---------- DTO ----------


@dataclass(frozen=True)
class MonteCarloConfig:
    """Monte Carlo 시뮬레이션 설정.

    method:
      - "shuffle":         순서만 섞음. 표본 크기 = 원본 거래 수.
      - "bootstrap":       복원추출 (각 거래 독립적으로 N번 다시 뽑음).
      - "block_bootstrap": 연속 block_size 블록 단위로 복원추출. 자기상관 보존.

    iterations: 시도 횟수 (기본 1000).
    seed: 결정성 (테스트 / CI 안정).
    initial_cash: 파산위험 계산 시 기준.
    ruin_drawdown_pct: equity / initial_cash가 (1 + ruin_drawdown_pct) 이하면 ruin.
                      기본 -0.5 (즉 50% 손실). 0.0 이상이면 무효 — 검증.
    block_size: block_bootstrap에서 사용. 기본 5.
    iterations 한도: 라우트 단에서 강제. 본 dataclass는 검증만.
    """
    method:            str   = "shuffle"
    iterations:        int   = 1000
    seed:              int | None = None
    initial_cash:      int   = 10_000_000
    ruin_drawdown_pct: float = DEFAULT_RUIN_DRAWDOWN_PCT
    block_size:        int   = 5

    def __post_init__(self):
        if self.method not in ("shuffle", "bootstrap", "block_bootstrap"):
            raise ValueError(
                f"unknown method: {self.method!r}. must be "
                "'shuffle' / 'bootstrap' / 'block_bootstrap'."
            )
        if self.iterations <= 0:
            raise ValueError("iterations must be > 0")
        if self.iterations > 100_000:
            raise ValueError("iterations capped at 100000 — use a lower value")
        if self.block_size <= 0:
            raise ValueError("block_size must be > 0")
        if self.ruin_drawdown_pct >= 0:
            raise ValueError(
                "ruin_drawdown_pct must be negative (e.g. -0.5 for 50% drawdown)"
            )


@dataclass
class MonteCarloResult:
    config:                 MonteCarloConfig
    n_trades:               int   # 원본 거래 수
    iterations:             int   # 실행된 시뮬레이션 횟수

    # PnL 분포
    p05_total_pnl:          int
    p50_total_pnl:          int
    p95_total_pnl:          int
    median_final_equity:    int

    # MDD 분포
    p05_max_drawdown:       int
    p50_max_drawdown:       int
    p95_max_drawdown:       int
    worst_5pct_avg_mdd:     int    # 최악 5% MDD의 평균

    # streak 분포
    longest_losing_streak:  int    # 모든 시뮬레이션 중 최장 연속손실

    # 파산위험
    ruin_count:             int
    risk_of_ruin:           float  # ruin_count / iterations

    # 자동 추천 — 운영자 최종 승인.
    promotion_risk_flag:    str    # PASS / CAUTION / FAIL
    stability_grade:        str    # GOOD / WARNING / POOR (별도 분류)
    warnings:               list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "config": {
                "method":             self.config.method,
                "iterations":         self.config.iterations,
                "seed":               self.config.seed,
                "initial_cash":       self.config.initial_cash,
                "ruin_drawdown_pct":  self.config.ruin_drawdown_pct,
                "block_size":         self.config.block_size,
            },
            "n_trades":               self.n_trades,
            "iterations":             self.iterations,
            "p05_total_pnl":          self.p05_total_pnl,
            "p50_total_pnl":          self.p50_total_pnl,
            "p95_total_pnl":          self.p95_total_pnl,
            "median_final_equity":    self.median_final_equity,
            "p05_max_drawdown":       self.p05_max_drawdown,
            "p50_max_drawdown":       self.p50_max_drawdown,
            "p95_max_drawdown":       self.p95_max_drawdown,
            "worst_5pct_avg_mdd":     self.worst_5pct_avg_mdd,
            "longest_losing_streak":  self.longest_losing_streak,
            "ruin_count":             self.ruin_count,
            "risk_of_ruin":           _safe_float(self.risk_of_ruin),
            "promotion_risk_flag":    self.promotion_risk_flag,
            "stability_grade":        self.stability_grade,
            "warnings":               list(self.warnings),
        }


# ---------- 공용 helpers ----------


def _percentile_int(values: list[int], pct: float) -> int:
    """nearest-rank percentile, 정수 반환. 빈 리스트 → 0."""
    if not values:
        return 0
    n = len(values)
    k = max(0, min(n - 1, int(round(pct / 100.0 * (n - 1)))))
    return int(sorted(values)[k])


def _equity_curve_metrics(pnls: list[int], initial_cash: int) -> dict:
    """단일 시뮬레이션의 누적 PnL → MDD / total / streak."""
    running = 0
    peak = 0
    max_dd = 0
    longest_streak = 0
    cur_streak = 0
    final_equity = initial_cash
    for p in pnls:
        running += p
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd
        if p < 0:
            cur_streak += 1
            longest_streak = max(longest_streak, cur_streak)
        else:
            cur_streak = 0
        final_equity = initial_cash + running
    return {
        "total_pnl":       running,
        "max_drawdown":    max_dd,
        "losing_streak":   longest_streak,
        "final_equity":    final_equity,
    }


# ---------- 샘플링 전략 ----------


def _shuffle_once(pnls: list[int], rng: random.Random) -> list[int]:
    out = list(pnls)
    rng.shuffle(out)
    return out


def _bootstrap_once(pnls: list[int], rng: random.Random) -> list[int]:
    n = len(pnls)
    return [pnls[rng.randrange(n)] for _ in range(n)]


def _block_bootstrap_once(pnls: list[int], block_size: int, rng: random.Random) -> list[int]:
    """연속 block_size 거래를 한 단위로 복원추출. 표본 크기는 원본과 동일하게 자름."""
    n = len(pnls)
    if n == 0:
        return []
    out: list[int] = []
    while len(out) < n:
        start = rng.randrange(n)
        # 끝을 넘으면 줄여서 복사 — 더 이상 wrap 하지 않음 (단순화).
        end = min(n, start + block_size)
        out.extend(pnls[start:end])
    return out[:n]


# ---------- 주 함수 ----------


def run_monte_carlo(
    trades: list,
    *,
    config: MonteCarloConfig | None = None,
) -> MonteCarloResult:
    """Trade 목록 → Monte Carlo simulation 결과.

    `trades`는 Trade dataclass / dict / 그 외 pnl을 가진 임의 객체 호환
    (`metrics.extract_trade_pnl` 활용).
    """
    if config is None:
        config = MonteCarloConfig()

    pnls = [extract_trade_pnl(t) for t in trades]
    n = len(pnls)

    # 거래 0건이면 모든 지표 0 — promotion_risk_flag는 FAIL (검증 불가).
    if n == 0:
        return MonteCarloResult(
            config=config, n_trades=0, iterations=0,
            p05_total_pnl=0, p50_total_pnl=0, p95_total_pnl=0,
            median_final_equity=config.initial_cash,
            p05_max_drawdown=0, p50_max_drawdown=0, p95_max_drawdown=0,
            worst_5pct_avg_mdd=0, longest_losing_streak=0,
            ruin_count=0, risk_of_ruin=0.0,
            promotion_risk_flag="FAIL", stability_grade="POOR",
            warnings=["거래 0건 — Monte Carlo 검증 불가."],
        )

    rng = random.Random(config.seed)
    ruin_threshold = int(config.initial_cash * (1 + config.ruin_drawdown_pct))

    total_pnls: list[int] = []
    max_dds:    list[int] = []
    streaks:    list[int] = []
    final_equities: list[int] = []
    ruin_count = 0

    for _ in range(config.iterations):
        if config.method == "shuffle":
            sample = _shuffle_once(pnls, rng)
        elif config.method == "bootstrap":
            sample = _bootstrap_once(pnls, rng)
        else:  # block_bootstrap (검증 통과)
            sample = _block_bootstrap_once(pnls, config.block_size, rng)

        m = _equity_curve_metrics(sample, config.initial_cash)
        total_pnls.append(m["total_pnl"])
        max_dds.append(m["max_drawdown"])
        streaks.append(m["losing_streak"])
        final_equities.append(m["final_equity"])

        # ruin: 누적 path 상에서 한 번이라도 ruin_threshold 이하로 떨어지면 ruin.
        # _equity_curve_metrics는 max_drawdown만 반환하므로 별도 path 검사.
        running = 0
        ruined = False
        for p in sample:
            running += p
            if config.initial_cash + running <= ruin_threshold:
                ruined = True
                break
        if ruined:
            ruin_count += 1

    risk_of_ruin = ruin_count / config.iterations

    # 최악 5% MDD 평균 — MDD가 큰 순으로 상위 5%.
    sorted_dds_desc = sorted(max_dds, reverse=True)
    worst_5pct_n = max(1, int(round(0.05 * len(sorted_dds_desc))))
    worst_5pct_slice = sorted_dds_desc[:worst_5pct_n]
    worst_5pct_avg_mdd = (
        int(sum(worst_5pct_slice) / len(worst_5pct_slice)) if worst_5pct_slice else 0
    )

    # 등급 / 경고
    flag = _promotion_risk_flag(risk_of_ruin)
    grade = _stability_grade(
        risk_of_ruin=risk_of_ruin,
        p05_total_pnl=_percentile_int(total_pnls, 5),
        p95_max_drawdown=_percentile_int(max_dds, 95),
        initial_cash=config.initial_cash,
    )
    warnings = _build_warnings(
        risk_of_ruin=risk_of_ruin,
        p05_total_pnl=_percentile_int(total_pnls, 5),
        p95_max_drawdown=_percentile_int(max_dds, 95),
        worst_5pct_avg_mdd=worst_5pct_avg_mdd,
        longest_losing_streak=max(streaks) if streaks else 0,
        initial_cash=config.initial_cash,
    )

    return MonteCarloResult(
        config=config,
        n_trades=n,
        iterations=config.iterations,
        p05_total_pnl=_percentile_int(total_pnls, 5),
        p50_total_pnl=_percentile_int(total_pnls, 50),
        p95_total_pnl=_percentile_int(total_pnls, 95),
        median_final_equity=_percentile_int(final_equities, 50),
        p05_max_drawdown=_percentile_int(max_dds, 5),
        p50_max_drawdown=_percentile_int(max_dds, 50),
        p95_max_drawdown=_percentile_int(max_dds, 95),
        worst_5pct_avg_mdd=worst_5pct_avg_mdd,
        longest_losing_streak=max(streaks) if streaks else 0,
        ruin_count=ruin_count,
        risk_of_ruin=risk_of_ruin,
        promotion_risk_flag=flag,
        stability_grade=grade,
        warnings=warnings,
    )


# ---------- 자동 분류 helpers ----------


def _promotion_risk_flag(risk_of_ruin: float) -> str:
    if risk_of_ruin >= RISK_OF_RUIN_FAIL_THRESHOLD:
        return "FAIL"
    if risk_of_ruin >= RISK_OF_RUIN_CAUTION_THRESHOLD:
        return "CAUTION"
    return "PASS"


def _stability_grade(
    *,
    risk_of_ruin: float,
    p05_total_pnl: int,
    p95_max_drawdown: int,
    initial_cash: int,
) -> str:
    """GOOD / WARNING / POOR.

    - p05_total_pnl < -initial_cash × 0.2: 최악 5% PnL이 -20% 초과 → POOR.
    - p95_max_drawdown > initial_cash × 0.3: 95% MDD가 30% 초과 → WARNING.
    - 그 외: GOOD.
    """
    if risk_of_ruin >= RISK_OF_RUIN_FAIL_THRESHOLD:
        return "POOR"
    if p05_total_pnl < -int(initial_cash * 0.2):
        return "POOR"
    if p95_max_drawdown > int(initial_cash * 0.3):
        return "WARNING"
    if risk_of_ruin >= RISK_OF_RUIN_CAUTION_THRESHOLD:
        return "WARNING"
    return "GOOD"


def _build_warnings(
    *,
    risk_of_ruin: float,
    p05_total_pnl: int,
    p95_max_drawdown: int,
    worst_5pct_avg_mdd: int,
    longest_losing_streak: int,
    initial_cash: int,
) -> list[str]:
    out: list[str] = []
    if risk_of_ruin >= RISK_OF_RUIN_FAIL_THRESHOLD:
        out.append(
            f"파산위험 {risk_of_ruin:.1%} ≥ {RISK_OF_RUIN_FAIL_THRESHOLD:.0%} — 승격 보류."
        )
    elif risk_of_ruin >= RISK_OF_RUIN_CAUTION_THRESHOLD:
        out.append(
            f"파산위험 {risk_of_ruin:.1%} (CAUTION 임계 {RISK_OF_RUIN_CAUTION_THRESHOLD:.0%} 초과). "
            "Position size 축소 또는 추가 검증 필요."
        )
    if p05_total_pnl < -int(initial_cash * 0.2):
        out.append(
            f"p05 total_pnl이 큰 음수 ({p05_total_pnl:,}) — "
            f"초기 자본의 -20% 초과 손실 가능성."
        )
    if p95_max_drawdown > int(initial_cash * 0.3):
        out.append(
            f"p95 MDD가 큼 ({p95_max_drawdown:,}) — "
            f"초기 자본의 30% 초과. 운영 자본 여유 확인."
        )
    if longest_losing_streak >= 8:
        out.append(
            f"최장 연속손실 {longest_losing_streak}회 — 운영자 심리 / 사이즈 정책 점검."
        )
    return out
