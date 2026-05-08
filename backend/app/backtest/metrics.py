"""백테스트 성과지표 (#24).

순수 함수 모음 — DB / network / broker 의존성 0건. 거래 0건 / NaN / inf 입력
모두 안전 처리. 모든 결과는 JSON 직렬화 가능 (dict / int / float / list / None).

본 모듈은 BacktestResult가 위임하는 단일 진실 — 같은 공식이 두 군데에 흩어져
있어 결과가 갈라지는 사고를 방지한다. metric drift 위험을 줄이기 위해 이 모듈만
유지보수하면 된다.

CLAUDE.md 절대 원칙 — broker / RiskManager / PermissionGate / OrderExecutor
import 0건. 본 모듈은 strategy 평가 / 승격 검토 / 운영자 리포트에만 사용된다.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone


# ---------- 안전 추출 ----------


def extract_trade_pnl(trade) -> int:
    """trade.pnl 또는 trade.net_pnl을 안전하게 추출.

    trade.pnl이 None이거나 trade에 pnl 속성이 없으면 0. dataclass / dict /
    pydantic 모델 모두 호환.
    """
    if trade is None:
        return 0
    pnl = getattr(trade, "pnl", None)
    if pnl is None and isinstance(trade, dict):
        pnl = trade.get("pnl")
    if pnl is None:
        # net_pnl 호환성 (Trade.net_pnl property는 pnl과 동일)
        pnl = getattr(trade, "net_pnl", None)
    if pnl is None:
        return 0
    return int(pnl)


def _exit_ts(trade) -> datetime | None:
    """trade.exit_ts를 datetime으로 추출. ISO 문자열도 허용."""
    if trade is None:
        return None
    ts = getattr(trade, "exit_ts", None)
    if ts is None and isinstance(trade, dict):
        ts = trade.get("exit_ts")
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return None
    return None


def _entry_price(trade) -> int | None:
    if trade is None:
        return None
    p = getattr(trade, "entry_price", None)
    if p is None and isinstance(trade, dict):
        p = trade.get("entry_price")
    return p


def _quantity(trade) -> int | None:
    if trade is None:
        return None
    q = getattr(trade, "quantity", None)
    if q is None and isinstance(trade, dict):
        q = trade.get("quantity")
    return q


# ---------- 기본 통계 ----------


def total_pnl(trades) -> int:
    return sum(extract_trade_pnl(t) for t in trades)


def win_count(trades) -> int:
    return sum(1 for t in trades if extract_trade_pnl(t) > 0)


def loss_count(trades) -> int:
    return sum(1 for t in trades if extract_trade_pnl(t) < 0)


def flat_count(trades) -> int:
    return sum(1 for t in trades if extract_trade_pnl(t) == 0)


def win_rate(trades) -> float:
    """win_count / 총 거래 수. 거래 0건이면 0.0."""
    n = len(list(trades)) if not hasattr(trades, "__len__") else len(trades)
    if n == 0:
        return 0.0
    return win_count(trades) / n


def avg_win(trades) -> float:
    """이익 거래의 평균. 없으면 0.0."""
    wins = [extract_trade_pnl(t) for t in trades if extract_trade_pnl(t) > 0]
    return sum(wins) / len(wins) if wins else 0.0


def avg_loss(trades) -> float:
    """손실 거래의 평균 (음수). pnl == 0은 별도 분류 — flat (계산 영향 없음).

    기존 BacktestResult.avg_loss는 pnl <= 0를 손실로 분류했으나, 신규 정의는
    *손실만* 평균낸다 — flat은 별도 카운트. 이 차이를 호출자가 인지하도록 본
    함수 이름은 명확히 'avg_loss' (음수만).
    """
    losses = [extract_trade_pnl(t) for t in trades if extract_trade_pnl(t) < 0]
    return sum(losses) / len(losses) if losses else 0.0


# 기존 BacktestResult.avg_loss와 lockstep — pnl<=0 (flat 포함) 평균.
def avg_loss_legacy(trades) -> float:
    losses = [extract_trade_pnl(t) for t in trades if extract_trade_pnl(t) <= 0]
    return sum(losses) / len(losses) if losses else 0.0


# ---------- 핵심 지표 ----------


def expectancy(trades) -> float:
    """기대값.

    expectancy = win_rate × avg_win + loss_rate × avg_loss

    avg_loss는 음수 유지 — 손실 비율이 클수록 expectancy를 끌어내린다. flat은
    win/loss에 모두 들지 않아 기대값에 0으로 기여 (본 정의 기준).
    """
    n = len(trades) if hasattr(trades, "__len__") else len(list(trades))
    if n == 0:
        return 0.0
    wr = win_count(trades) / n
    lr = loss_count(trades) / n
    return wr * avg_win(trades) + lr * avg_loss(trades)


def profit_factor(trades) -> float | None:
    """gross profit / |gross loss|.

    - 거래 0건: None
    - 손실 0건: None (+inf 대신 — JSON 직렬화 안전)
    - 그 외: 양수
    """
    if not trades:
        return None
    pnls = [extract_trade_pnl(t) for t in trades]
    gross_win  = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    if gross_loss == 0:
        return None
    return gross_win / gross_loss


def max_drawdown(trades) -> int:
    """누적 PnL 곡선의 최대 peak-to-trough 낙폭(절대값)."""
    peak = 0
    running = 0
    max_dd = 0
    for t in trades:
        running += extract_trade_pnl(t)
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd
    return max_dd


def sharpe_ratio(trades) -> float | None:
    """체결당(per-trade) 단순 Sharpe = mean(returns) / stdev(returns).

    trade return = pnl / (entry_price × quantity). 봉 간격을 모르므로 연환산하지
    않는다. NaN/inf가 발생할 수 있는 경계는 모두 None 반환.

    - 거래 < 2: None
    - stdev == 0 (모두 같은 return): None
    - entry_price × quantity == 0인 거래는 분모 0이라 제외 (운영적으로 비현실적이지만 안전 측)
    """
    if len(trades) < 2:
        return None
    returns: list[float] = []
    for t in trades:
        ep = _entry_price(t)
        q  = _quantity(t)
        if ep is None or q is None:
            continue
        denom = ep * q
        if denom == 0:
            continue
        returns.append(extract_trade_pnl(t) / denom)
    if len(returns) < 2:
        return None
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    if variance == 0:
        return None
    s = mean / math.sqrt(variance)
    if not math.isfinite(s):
        return None
    return s


# ---------- 연속 구간 ----------


def max_consecutive_losses(trades) -> int:
    """pnl < 0이 연속으로 발생한 최대 길이. flat은 streak를 끊지 않고 보존하지
    않는다 — 단타 운영에서 'flat은 손실도 이익도 아니다'를 자연스럽게.

    실제로는 flat이 streak를 *끊는* 보수적 의미가 더 안전 — 본 모듈은 그렇게.
    """
    longest = 0
    cur = 0
    for t in trades:
        p = extract_trade_pnl(t)
        if p < 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return longest


def max_consecutive_wins(trades) -> int:
    longest = 0
    cur = 0
    for t in trades:
        p = extract_trade_pnl(t)
        if p > 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return longest


# ---------- 시간대별 손익 ----------


def hourly_pnl(trades) -> dict[int, int]:
    """exit_ts의 시(hour) 기준으로 손익 집계. UTC 기준.

    - exit_ts가 없거나 파싱 실패한 거래는 별도 키 -1로 분류 (운영자가 인지).
    - 결과 dict의 key는 0~23 또는 -1 (unknown), value는 정수 합계.
    """
    out: dict[int, int] = {}
    for t in trades:
        ts = _exit_ts(t)
        if ts is None:
            hour = -1
        else:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
            hour = ts.hour
        out[hour] = out.get(hour, 0) + extract_trade_pnl(t)
    return out


# ---------- equity curve ----------


def equity_curve(trades, initial_cash: float = 0.0) -> list[dict]:
    """거래 순서대로 누적 손익 곡선 생성.

    - 거래는 exit_ts로 정렬 (가능한 경우). exit_ts 없는 거래는 원본 순서 유지.
    - 각 점은 {"timestamp": ISO|None, "equity": float} 형태.
    - 첫 점은 거래 0개일 때만 [{"timestamp": None, "equity": initial_cash}].
    """
    sortable = [(t, _exit_ts(t)) for t in trades]
    sortable.sort(key=lambda x: x[1] if x[1] is not None else datetime.min.replace(tzinfo=timezone.utc))
    out: list[dict] = [{"timestamp": None, "equity": float(initial_cash)}]
    running = float(initial_cash)
    for trade, ts in sortable:
        running += extract_trade_pnl(trade)
        out.append({
            "timestamp": ts.isoformat() if ts else None,
            "equity":    running,
        })
    return out


# ---------- summary ----------


def summarize_metrics(trades, *, initial_cash: int = 0) -> dict:
    """JSON 직렬화 가능한 dict 형태의 종합 지표 — API 응답 / dashboard 카드 공용.

    None 값은 그대로 둠 (JSON `null`). NaN / inf는 None으로 sanitize.
    """
    n = len(trades)
    pf  = profit_factor(trades)
    sh  = sharpe_ratio(trades)
    return {
        "trade_count":           n,
        "total_pnl":             total_pnl(trades),
        "win_count":             win_count(trades),
        "loss_count":            loss_count(trades),
        "flat_count":            flat_count(trades),
        "win_rate":              _safe_float(win_rate(trades)),
        "avg_win":               _safe_float(avg_win(trades)),
        "avg_loss":              _safe_float(avg_loss(trades)),
        "expectancy":            _safe_float(expectancy(trades)),
        "profit_factor":         _safe_float(pf) if pf is not None else None,
        "sharpe_ratio":          _safe_float(sh) if sh is not None else None,
        "max_drawdown":          max_drawdown(trades),
        "max_consecutive_wins":   max_consecutive_wins(trades),
        "max_consecutive_losses": max_consecutive_losses(trades),
        "hourly_pnl":            hourly_pnl(trades),
        "initial_cash":          int(initial_cash),
    }


def _safe_float(v: float | None) -> float | None:
    """NaN / inf → None. 일반 float은 그대로."""
    if v is None:
        return None
    if not math.isfinite(v):
        return None
    return float(v)
