"""P-26: 매도(SELL) 사유 정밀 기록 — 자동 SELL 이 왜 발생했는지 reason_code 표준화.

자동매매 성능은 매수보다 *매도* 가 중요하므로, 손절/익절/트레일링/장마감/전략
반대신호/리스크축소/시간초과/Agent 복합판단 등 매도 사유별 성과를 분석할 수
있도록 SELL 마다 `sell_reason_code` 를 명확히 산출해 episode 에 기록한다.

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *추론/기록 전용* — broker / OrderExecutor / route_order / 외부 HTTP
  import 0건, 주문을 만들거나 전송하지 않는다.
- secret / API key / 계좌번호 carry 0건.
- `SellReason.is_live_authorization=False` / `is_order_signal=False` /
  `contains_secret=False` 영구.
- 결정적(deterministic) — 같은 입력 → 같은 결과.
- **자동 SELL 은 절대 UNKNOWN_SELL_REASON 으로 끝나지 않는다** — 특정 사유를
  못 찾으면 AI_AGENT_EXIT(복합 판단)로 정규화. UNKNOWN 은 SELL 이 아닌 입력에
  대한 방어적 fallback 으로만 사용.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── 매도 사유 코드 ──
STOP_LOSS           = "STOP_LOSS"
TAKE_PROFIT         = "TAKE_PROFIT"
TRAILING_STOP       = "TRAILING_STOP"
VWAP_BREAKDOWN      = "VWAP_BREAKDOWN"
MOMENTUM_WEAKENING  = "MOMENTUM_WEAKENING"
MARKET_CLOSE_EXIT   = "MARKET_CLOSE_EXIT"
RISK_REDUCTION      = "RISK_REDUCTION"
MANUAL_EXIT         = "MANUAL_EXIT"
STRATEGY_REVERSAL   = "STRATEGY_REVERSAL"
TIME_STOP           = "TIME_STOP"
AI_AGENT_EXIT       = "AI_AGENT_EXIT"
UNKNOWN_SELL_REASON = "UNKNOWN_SELL_REASON"

# ── 카테고리 ──
CAT_RISK_EXIT     = "RISK_EXIT"        # 손절 / 트레일링 / 리스크 축소
CAT_PROFIT_EXIT   = "PROFIT_EXIT"      # 익절
CAT_STRATEGY_EXIT = "STRATEGY_EXIT"    # VWAP / 모멘텀 / 전략 반대
CAT_TIME_EXIT     = "TIME_EXIT"        # 보유시간 초과
CAT_MARKET_EXIT   = "MARKET_EXIT"      # 장마감 청산
CAT_MANUAL_EXIT   = "MANUAL_EXIT"      # 운영자 수동
CAT_AI_EXIT       = "AI_EXIT"          # Agent 복합 판단
CAT_UNKNOWN       = "UNKNOWN"

# 코드 → 카테고리.
REASON_CATEGORY: dict[str, str] = {
    STOP_LOSS:           CAT_RISK_EXIT,
    TRAILING_STOP:       CAT_RISK_EXIT,
    RISK_REDUCTION:      CAT_RISK_EXIT,
    TAKE_PROFIT:         CAT_PROFIT_EXIT,
    VWAP_BREAKDOWN:      CAT_STRATEGY_EXIT,
    MOMENTUM_WEAKENING:  CAT_STRATEGY_EXIT,
    STRATEGY_REVERSAL:   CAT_STRATEGY_EXIT,
    TIME_STOP:           CAT_TIME_EXIT,
    MARKET_CLOSE_EXIT:   CAT_MARKET_EXIT,
    MANUAL_EXIT:         CAT_MANUAL_EXIT,
    AI_AGENT_EXIT:       CAT_AI_EXIT,
    UNKNOWN_SELL_REASON: CAT_UNKNOWN,
}

# 코드 → 한국어 사용자 메시지 (사용자 이해 가능).
REASON_MESSAGE: dict[str, str] = {
    STOP_LOSS:           "손절 기준에 도달하여 매도 판단",
    TAKE_PROFIT:         "익절 목표에 도달하여 매도 판단",
    TRAILING_STOP:       "트레일링 스탑 발동으로 매도 판단",
    VWAP_BREAKDOWN:      "VWAP 하향 이탈로 매도 판단",
    MOMENTUM_WEAKENING:  "상승 모멘텀 약화로 매도 판단",
    MARKET_CLOSE_EXIT:   "장 마감 청산으로 매도 판단",
    RISK_REDUCTION:      "리스크 축소(노출 감소)를 위한 매도 판단",
    MANUAL_EXIT:         "운영자 수동 청산 요청에 따른 매도",
    STRATEGY_REVERSAL:   "전략 신호 반전(매수→매도)으로 매도 판단",
    TIME_STOP:           "보유 시간 초과(타임 스탑)로 매도 판단",
    AI_AGENT_EXIT:       "Agent 복합 판단에 따른 매도",
    UNKNOWN_SELL_REASON: "매도 사유를 특정하지 못함(점검 필요)",
}

VALID_CODES: tuple[str, ...] = tuple(REASON_CATEGORY.keys())

# 외부에서 들어오는 명시 reason_code 정규화 alias.
_CODE_ALIASES: dict[str, str] = {
    "SL": STOP_LOSS, "STOPLOSS": STOP_LOSS, "STOP-LOSS": STOP_LOSS,
    "TP": TAKE_PROFIT, "TAKEPROFIT": TAKE_PROFIT, "TAKE-PROFIT": TAKE_PROFIT,
    "TRAILING": TRAILING_STOP, "TRAIL": TRAILING_STOP,
    "VWAP": VWAP_BREAKDOWN, "VWAP_BREAK": VWAP_BREAKDOWN,
    "MOMENTUM": MOMENTUM_WEAKENING, "MOMENTUM_WEAK": MOMENTUM_WEAKENING,
    "MARKET_CLOSE": MARKET_CLOSE_EXIT, "EOD": MARKET_CLOSE_EXIT, "CLOSE": MARKET_CLOSE_EXIT,
    "RISK": RISK_REDUCTION, "REDUCE": RISK_REDUCTION, "RISK_REDUCE": RISK_REDUCTION,
    "MANUAL": MANUAL_EXIT, "OPERATOR": MANUAL_EXIT,
    "REVERSAL": STRATEGY_REVERSAL, "FLIP": STRATEGY_REVERSAL,
    "TIME": TIME_STOP, "TIMESTOP": TIME_STOP, "TIME-STOP": TIME_STOP,
    "AI": AI_AGENT_EXIT, "AGENT": AI_AGENT_EXIT, "AI_EXIT": AI_AGENT_EXIT,
}


def _normalize_code(code: str | None) -> str | None:
    if not code:
        return None
    c = str(code).strip().upper().replace(" ", "_")
    if c in REASON_CATEGORY:
        return c
    return _CODE_ALIASES.get(c)


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SellReason:
    """매도 사유 — episode.council.sell_reason 에 저장되는 안전 payload."""

    reason_code:      str
    category:         str
    message:          str
    evaluated_action: str = "SELL"
    source_strategies: list[str] = field(default_factory=list)
    triggered_by:     str | None = None
    metadata:         dict[str, Any] = field(default_factory=dict)

    contains_secret:       bool = False
    is_live_authorization: bool = False
    is_order_signal:       bool = False

    def __post_init__(self) -> None:
        if self.contains_secret is not False:
            raise ValueError("SellReason.contains_secret must be False")
        if self.is_live_authorization is not False:
            raise ValueError("SellReason.is_live_authorization must be False")
        if self.is_order_signal is not False:
            raise ValueError("SellReason.is_order_signal must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason_code":       self.reason_code,
            "category":          self.category,
            "message":           self.message,
            "evaluated_action":  self.evaluated_action,
            "source_strategies": list(self.source_strategies),
            "triggered_by":      self.triggered_by,
            "metadata":          dict(self.metadata),
            "contains_secret":       False,
            "is_live_authorization": False,
            "is_order_signal":       False,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "category":    self.category,
            "message":     self.message,
        }


def _make(code: str, *, triggered_by: str | None = None,
          source_strategies: list[str] | None = None,
          metadata: dict[str, Any] | None = None) -> SellReason:
    return SellReason(
        reason_code=code,
        category=REASON_CATEGORY.get(code, CAT_UNKNOWN),
        message=REASON_MESSAGE.get(code, REASON_MESSAGE[UNKNOWN_SELL_REASON]),
        source_strategies=list(source_strategies or []),
        triggered_by=triggered_by,
        metadata=dict(metadata or {}),
    )


def _sell_strategies_from(votes: Any, selected_strategies: Any) -> list[str]:
    """SELL 을 던진 전략 목록 — votes 우선, 없으면 selected_strategies."""
    out: list[str] = []
    if isinstance(votes, list):
        for v in votes:
            if isinstance(v, dict) and str(v.get("signal", "")).upper() == "SELL":
                strat = v.get("strategy")
                if strat:
                    out.append(str(strat).upper())
    if not out and isinstance(selected_strategies, (list, tuple)):
        out = [str(s).upper() for s in selected_strategies if s]
    return out


def _stop_loss_triggered(pos: dict, exit_plan: dict, entry, cur) -> bool:
    if pos.get("stop_loss_triggered") is True:
        return True
    slp = _f(pos.get("stop_loss_price"))
    if slp is not None and cur is not None and cur <= slp:
        return True
    sl_pct = _f(exit_plan.get("stop_loss_pct"))
    if sl_pct and entry and cur is not None and cur <= entry * (1.0 - sl_pct / 100.0):
        return True
    return False


def _take_profit_triggered(pos: dict, exit_plan: dict, entry, cur) -> bool:
    if pos.get("take_profit_triggered") is True:
        return True
    tpp = _f(pos.get("take_profit_price"))
    if tpp is not None and cur is not None and cur >= tpp:
        return True
    tp_pct = _f(exit_plan.get("take_profit_pct"))
    if tp_pct and entry and cur is not None and cur >= entry * (1.0 + tp_pct / 100.0):
        return True
    return False


def _trailing_triggered(pos: dict, cur) -> bool:
    if pos.get("trailing_stop_triggered") is True:
        return True
    tsp = _f(pos.get("trailing_stop_price"))
    if tsp is not None and cur is not None and cur <= tsp:
        return True
    return False


def _holding_exceeded(pos: dict) -> bool:
    hm = _f(pos.get("holding_minutes"))
    mx = _f(pos.get("max_holding_minutes"))
    return bool(hm is not None and mx and hm >= mx)


def infer_sell_reason(
    *,
    decision: Any = None,
    council: Any = None,
    market_snapshot: dict | None = None,
    position: dict | None = None,
    exit_plan: dict | None = None,
    risk_result: dict | None = None,
    votes: list | None = None,
    selected_strategies: list | None = None,
    explicit_reason_code: str | None = None,
    triggered_by: str | None = None,
    market_closed: bool = False,
) -> SellReason:
    """SELL 사유를 우선순위 규칙으로 추론 (deterministic, 예외 0).

    우선순위:
      1. 명시 reason_code → 그대로 사용 (정규화)
      2. stop_loss        → STOP_LOSS
      3. take_profit      → TAKE_PROFIT
      4. trailing_stop    → TRAILING_STOP
      5. VWAP 하향 이탈    → VWAP_BREAKDOWN
      6. 모멘텀 약화        → MOMENTUM_WEAKENING
      7. 장마감 청산        → MARKET_CLOSE_EXIT
      8. 리스크 축소        → RISK_REDUCTION
      9. 운영자 수동        → MANUAL_EXIT
     10. 전략 반대 신호      → STRATEGY_REVERSAL
     11. 보유시간 초과       → TIME_STOP
     12. Agent 복합 판단     → AI_AGENT_EXIT  (SELL 이면 UNKNOWN 대신 이 값)
     13. (SELL 이 아니면)    → UNKNOWN_SELL_REASON

    `position` 지원 필드: entry_price / current_price / stop_loss_price /
    take_profit_price / trailing_stop_price / *_triggered(bool) /
    holding_minutes / max_holding_minutes.
    """
    pos = dict(position or {})
    ep = dict(exit_plan or {})
    if not ep and decision is not None:
        ep = dict(getattr(decision, "exit_plan", {}) or {})
    snap = dict(market_snapshot or {})
    tb = (str(triggered_by).strip().upper() if triggered_by else None)

    # action 판별.
    action = ""
    if council is not None:
        fa = getattr(council, "final_action", None)
        action = str(getattr(fa, "value", fa) or "").upper()
    if not action and decision is not None:
        action = str(getattr(decision, "side", "") or "").upper()
    if not action:
        action = "SELL" if (votes or selected_strategies or pos or explicit_reason_code) else ""

    # 1. 명시 reason_code.
    code = _normalize_code(explicit_reason_code)
    if code:
        return _make(code, triggered_by=tb or "EXPLICIT")

    cur = _f(pos.get("current_price"))
    if cur is None:
        cur = _f(snap.get("price"))
    entry = _f(pos.get("entry_price"))

    votes_list = votes
    if votes_list is None and council is not None and getattr(council, "votes", None):
        votes_list = [v.to_dict() if hasattr(v, "to_dict") else v for v in council.votes]
    if selected_strategies is None and council is not None:
        selected_strategies = list(getattr(council, "selected_strategies", []) or [])
    sell_strats = _sell_strategies_from(votes_list, selected_strategies)

    rr = dict(risk_result or {})
    risk_reduction = bool(rr.get("risk_reduction")) or tb in ("RISK_REDUCTION", "RISK_REDUCE")
    manual = tb in ("MANUAL", "MANUAL_EXIT", "OPERATOR")
    market_close = bool(market_closed) or tb in ("MARKET_CLOSE", "MARKET_CLOSE_EXIT", "EOD")
    time_stop = tb in ("TIME_STOP", "TIME") or _holding_exceeded(pos)
    reversal = tb in ("STRATEGY_REVERSAL", "REVERSAL", "FLIP")

    # 2~4. 가격 기반 트리거.
    if _stop_loss_triggered(pos, ep, entry, cur):
        return _make(STOP_LOSS, triggered_by=tb or "PRICE", source_strategies=sell_strats)
    if _take_profit_triggered(pos, ep, entry, cur):
        return _make(TAKE_PROFIT, triggered_by=tb or "PRICE", source_strategies=sell_strats)
    if _trailing_triggered(pos, cur):
        return _make(TRAILING_STOP, triggered_by=tb or "PRICE", source_strategies=sell_strats)

    # 5~6. 전략 vote 기반.
    if "VWAP" in sell_strats:
        return _make(VWAP_BREAKDOWN, triggered_by=tb or "STRATEGY_VOTE",
                     source_strategies=sell_strats)
    if "MOMENTUM" in sell_strats:
        return _make(MOMENTUM_WEAKENING, triggered_by=tb or "STRATEGY_VOTE",
                     source_strategies=sell_strats)

    # 7~9. 운영/리스크/장마감.
    if market_close:
        return _make(MARKET_CLOSE_EXIT, triggered_by=tb or "MARKET_CLOSE",
                     source_strategies=sell_strats)
    if risk_reduction:
        return _make(RISK_REDUCTION, triggered_by=tb or "RISK", source_strategies=sell_strats)
    if manual:
        return _make(MANUAL_EXIT, triggered_by=tb or "MANUAL", source_strategies=sell_strats)

    # 10. 전략 반대 신호 (명시 reversal 또는 다수 전략 SELL).
    if reversal or len(sell_strats) >= 2:
        return _make(STRATEGY_REVERSAL, triggered_by=tb or "STRATEGY_VOTE",
                     source_strategies=sell_strats)

    # 11. 보유시간 초과.
    if time_stop:
        return _make(TIME_STOP, triggered_by=tb or "TIME", source_strategies=sell_strats)

    # 12. SELL 인데 특정 사유 없음 → Agent 복합 판단 (UNKNOWN 금지).
    if action == "SELL" or sell_strats:
        return _make(AI_AGENT_EXIT, triggered_by=tb or "AGENT", source_strategies=sell_strats)

    # 13. SELL 컨텍스트가 아니면 방어적 UNKNOWN.
    return _make(UNKNOWN_SELL_REASON, triggered_by=tb)


def sell_reason_summary(sell_reason: dict[str, Any] | None) -> dict[str, Any]:
    """sell_reason dict → 목록 표시용 요약."""
    if not isinstance(sell_reason, dict) or not sell_reason.get("reason_code"):
        return {}
    code = sell_reason.get("reason_code")
    return {
        "reason_code": code,
        "category":    sell_reason.get("category") or REASON_CATEGORY.get(code, CAT_UNKNOWN),
        "message":     sell_reason.get("message") or REASON_MESSAGE.get(code, ""),
    }


__all__ = [
    "SellReason", "infer_sell_reason", "sell_reason_summary",
    "REASON_CATEGORY", "REASON_MESSAGE", "VALID_CODES",
    "STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP", "VWAP_BREAKDOWN",
    "MOMENTUM_WEAKENING", "MARKET_CLOSE_EXIT", "RISK_REDUCTION", "MANUAL_EXIT",
    "STRATEGY_REVERSAL", "TIME_STOP", "AI_AGENT_EXIT", "UNKNOWN_SELL_REASON",
    "CAT_RISK_EXIT", "CAT_PROFIT_EXIT", "CAT_STRATEGY_EXIT", "CAT_TIME_EXIT",
    "CAT_MARKET_EXIT", "CAT_MANUAL_EXIT", "CAT_AI_EXIT", "CAT_UNKNOWN",
]
