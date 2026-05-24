"""3-09: 거래 없음(no-trade) 사유 누적 (read-only, advisory).

자동매매 tick/cycle 에서 *실제 주문이 발생하지 않은* 경우에도 "왜 거래가
없었는지" 를 명확히 집계하기 위한 **표시 전용** 모듈. P-17 `blocked_reasons.py`
(매수 *차단* 사유) 보다 넓은 범위 — 정상 HOLD / NO_SIGNAL / 데이터 없음 /
장 시간 외 등 *차단이 아닌* 거래 없음 사유까지 포함해, 사용자가 "시스템이
멈춘 것인지, 조건이 없어서 쉰 것인지" 를 구분할 수 있게 한다.

본 모듈은 *기존 ledger event* (`PaperLoopEvent`) 를 read-only 로 읽어 거기에
이미 기록된 reason_code / decision_action / fill_status 를 정규화 + 집계할
뿐이다. **실제 주문 로직을 열지 않으며**, broker / OrderExecutor / route_order
를 import 하거나 호출하지 않는다.

## 절대 invariant (CLAUDE.md 절대 원칙 상속)

- **거래가 없는데 성공 주문처럼 표시하지 않는다** — no-trade record 는
  broker_order_no 0건, broker_order_sent=False 의미를 carry.
- broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건.
- DB write 0건 — 입력 event dict 를 읽기만 한다.
- `NoTradeSummary.is_order_signal=False` / `is_live_authorization=False` /
  `auto_apply_allowed=False` / `contains_secret=False` 불변.
- secret / API key / 계좌번호 carry 0건 — detail 은 안전 키만 통과.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.auto_paper.blocked_reasons import (
    BUY_BLOCK_REASON_CATEGORY,
    BUY_BLOCK_REASON_SEVERITY,
    normalize_reason_code as _normalize_block_code,
)

# ─────────────────────────────────────────────────────────────────────────────
# no-trade reason 어휘 (단일 진실 — frontend noTradeReasons formatter 와 정합)
#
# blocked_reasons 의 *매수 차단* 문구는 "매수하지 않았습니다" 형태지만, 본
# 모듈은 *거래(매수/매도) 없음* 관점이므로 "거래하지 않음" 문구로 표준화한다.
# ─────────────────────────────────────────────────────────────────────────────


NO_TRADE_REASON_TITLES_KO: dict[str, str] = {
    # 신호 / 전략 / 데이터 / 시장.
    "NO_SIGNAL":                  "조건에 맞는 매수/매도 신호가 없어 거래하지 않음",
    "NO_STRATEGY_SIGNAL":         "전략 신호가 없어 거래하지 않음",
    "NO_CANDIDATE":               "매수 후보가 생성되지 않아 거래하지 않음",
    "NO_MARKET_DATA":             "시장 데이터가 없어 거래하지 않음",
    "MARKET_CLOSED":              "장 시간이 아니어서 거래하지 않음",
    "PRICE_STALE":                "현재가가 오래되어 거래하지 않음",
    "INVALID_PRICE":              "현재가가 비정상이라 거래하지 않음",
    "ABNORMAL_PRICE_MOVE":        "가격 급등락이 감지되어 거래하지 않음",
    # 권한 / 위험 / 준비.
    "BLOCKED_BY_RISK_MANAGER":    "리스크 조건으로 거래 차단",
    "BLOCKED_BY_PERMISSION_GATE": "주문 권한 조건으로 거래 차단",
    "BLOCKED_BY_KIS_READINESS":   "KIS 모의투자 준비 상태가 충족되지 않아 거래하지 않음",
    "PAPER_EXECUTION_DISABLED":   "Paper 가상 실행이 비활성화되어 거래하지 않음",
    "EMERGENCY_STOP":             "긴급정지 상태라 거래하지 않음",
    # exit plan / 위험 플래그 (BUY 필수 게이트).
    "EXIT_PLAN_MISSING":          "손절/익절 계획이 없어 매수 차단",
    "EXIT_PLAN_INVALID":          "손절/익절 계획이 유효하지 않아 매수 차단",
    "RISK_FLAGS_EXCEEDED":        "위험 플래그 초과로 HOLD",
    # 자금 / 수량 / 노출 (buy block carry).
    "DUPLICATE_POSITION_BUY_BLOCKED": "이미 보유 중인 종목이라 추가 매수 차단",
    "DAILY_BUY_LIMIT_EXCEEDED":   "일일 최대 매수금액을 초과하여 매수 차단",
    "DAILY_ORDER_LIMIT_EXCEEDED": "일일 최대 주문 횟수를 초과하여 매수 차단",
    "NOTIONAL_LIMIT_EXCEEDED":    "1회 주문금액 한도를 초과하여 매수 차단",
    "SYMBOL_WEIGHT_LIMIT_EXCEEDED": "종목별 최대 비중을 초과하여 매수 차단",
    "MAX_POSITIONS_REACHED":      "최대 보유 종목 수에 도달하여 매수 차단",
    "INSUFFICIENT_PAPER_CASH":    "남은 Paper 현금이 부족하여 매수 차단",
    "MIN_LOT_NOT_AFFORDABLE":     "1주 가격이 투자한도 초과로 제외",
    # fallback.
    "UNKNOWN":                    "알 수 없는 사유로 거래하지 않음",
}


# blocked_reasons 에 없는 *추가* code 의 category / severity.
_EXTRA_CATEGORY: dict[str, str] = {
    "NO_SIGNAL":                "strategy",
    "BLOCKED_BY_KIS_READINESS": "permission",
    "EXIT_PLAN_MISSING":        "risk",
    "EXIT_PLAN_INVALID":        "risk",
    "RISK_FLAGS_EXCEEDED":      "risk",
}

_EXTRA_SEVERITY: dict[str, str] = {
    "NO_SIGNAL":                "info",
    "BLOCKED_BY_KIS_READINESS": "warning",
    "EXIT_PLAN_MISSING":        "blocked",
    "EXIT_PLAN_INVALID":        "blocked",
    "RISK_FLAGS_EXCEEDED":      "warning",
}


# raw code (loop / agent council / kis readiness / exit-plan gate) → 정규 code.
_NO_TRADE_ALIASES: dict[str, str] = {
    # 신호 없음 / 보류.
    "HOLD":                 "NO_SIGNAL",
    "NO_OP":                "NO_SIGNAL",
    "NO_TRADE":             "NO_SIGNAL",
    "NO_DECISION":          "NO_SIGNAL",
    "NO_ACTION":            "NO_SIGNAL",
    # exit plan 게이트 (2-09).
    "EXIT_PLAN_REQUIRED":   "EXIT_PLAN_MISSING",
    "NO_EXIT_PLAN":         "EXIT_PLAN_MISSING",
    "EXIT_PLAN_NOT_VALID":  "EXIT_PLAN_INVALID",
    # 위험 플래그 (2-08) — spec 이 두 철자 모두 사용.
    "RISK_FLAGS_EXCEED":    "RISK_FLAGS_EXCEEDED",
    "RISK_FLAG_EXCEEDED":   "RISK_FLAGS_EXCEEDED",
    "RISK_VETO":            "RISK_FLAGS_EXCEEDED",
    # KIS 모의 준비 (2-11).
    "KIS_READINESS_BLOCKED": "BLOCKED_BY_KIS_READINESS",
    "KIS_NOT_READY":         "BLOCKED_BY_KIS_READINESS",
    "BLOCKED_BY_KIS_READINESS": "BLOCKED_BY_KIS_READINESS",
}


def normalize_no_trade_reason_code(raw: Any) -> str:
    """raw reason_code 를 no-trade 정규 어휘로 변환. 알 수 없으면 'UNKNOWN'.

    1) no-trade 전용 alias / title 우선.
    2) 없으면 blocked_reasons 정규화기로 위임 (PAPER_GUARD_* / KIS_PAPER_* 등).
    """
    if raw is None:
        return "UNKNOWN"
    code = str(raw).strip().upper()
    if not code:
        return "UNKNOWN"
    code = _NO_TRADE_ALIASES.get(code, code)
    if code in NO_TRADE_REASON_TITLES_KO:
        return code
    blocked = _normalize_block_code(code)
    if blocked != "UNKNOWN" and blocked in NO_TRADE_REASON_TITLES_KO:
        return blocked
    return "UNKNOWN"


def title_for(code: str) -> str:
    return NO_TRADE_REASON_TITLES_KO.get(
        code, NO_TRADE_REASON_TITLES_KO["UNKNOWN"]
    )


def category_for(code: str) -> str:
    if code in _EXTRA_CATEGORY:
        return _EXTRA_CATEGORY[code]
    return BUY_BLOCK_REASON_CATEGORY.get(code, "unknown")


def severity_for(code: str) -> str:
    if code in _EXTRA_SEVERITY:
        return _EXTRA_SEVERITY[code]
    return BUY_BLOCK_REASON_SEVERITY.get(code, "info")


# ─────────────────────────────────────────────────────────────────────────────
# event → no-trade record 추출
# ─────────────────────────────────────────────────────────────────────────────


# detail 에 통과시킬 안전 키 (secret / 계좌번호 류 차단 — allowlist).
_SAFE_DETAIL_KEYS: frozenset[str] = frozenset({
    "required_amount", "remaining_cash", "cap_krw", "price", "quantity",
    "notional_krw", "max_positions", "current_positions", "weight_pct",
    "max_symbol_weight_pct", "daily_buy_used", "daily_buy_limit",
    "age_seconds", "change_pct", "sizer_verdict", "freshness_reason_code",
    "confidence", "quality_score",
})


def _raw_code_from_event(event: dict[str, Any]) -> str | None:
    """ledger event dict 에서 raw reason_code 후보를 추출 (정규화 전)."""
    meta = event.get("metadata") or {}
    if isinstance(meta, dict):
        for key in ("reason_code", "no_trade_reason", "sell_reason_code"):
            if meta.get(key):
                return str(meta[key])
    flags = event.get("risk_flags") or []
    if isinstance(flags, list) and flags:
        return str(flags[0])
    # reason 텍스트의 "[paper-flow] CODE: ..." / "[run-once] CODE" 패턴.
    reason = str(event.get("reason") or "")
    if "]" in reason:
        tail = reason.split("]", 1)[1].strip()
        token = tail.split(":", 1)[0].strip().split()[0] if tail else ""
        if token:
            return token
    return None


def _safe_detail(meta: Any) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    return {k: meta[k] for k in _SAFE_DETAIL_KEYS if k in meta}


def is_trade_fill(event: dict[str, Any]) -> bool:
    """event 가 *실제로 (가상) 체결된 거래* 인지 — no-trade 집계 제외 대상."""
    if not isinstance(event, dict):
        return False
    return str(event.get("paper_fill_status") or "") == "PAPER_FILLED"


def classify_no_trade_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """단일 ledger event 가 *거래 없음* 을 나타내면 표시용 record, 아니면 None.

    체결된(PAPER_FILLED) event 는 거래가 발생한 것이므로 None.
    그 외(HOLD / NO_OP / PAPER_REJECTED / PAPER_PENDING / PAPER_CANCELLED) 는
    모두 no-trade cycle 로 간주하고 reason_code 를 부여한다.
    """
    if not isinstance(event, dict):
        return None
    if is_trade_fill(event):
        return None   # 체결됨 — 거래 발생.

    action = str(event.get("decision_action") or "").upper()
    raw = _raw_code_from_event(event)
    code = normalize_no_trade_reason_code(raw)
    if code == "UNKNOWN":
        # code 불명 — action 기반 fallback. HOLD/NO_OP/무판단 → NO_SIGNAL.
        if action in ("HOLD", "NO_OP", ""):
            code = "NO_SIGNAL"
        # PAPER_REJECTED 인데 사유 불명 → UNKNOWN 유지 (거절은 됐으나 사유 미상).

    return {
        "reason_code":     code,
        "title":           title_for(code),
        "category":        category_for(code),
        "severity":        severity_for(code),
        "symbol":          event.get("symbol"),
        "strategy":        event.get("strategy"),
        "timestamp":       event.get("timestamp"),
        "decision_action": event.get("decision_action"),
        "reason_message":  event.get("reason"),
        "detail":          _safe_detail(event.get("metadata")),
        # 거래 없음 — 절대 broker 주문 아님.
        "broker_order_no":     None,
        "broker_order_sent":   False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 집계 결과
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NoTradeSummary:
    """거래 없음 사유 집계 — 표시 전용 안전 payload."""

    cycle_count:    int
    order_count:    int
    no_trade_count: int
    by_reason:      dict[str, int]
    recent:         list[dict[str, Any]]
    last_no_trade:        dict[str, Any] | None = None
    last_no_trade_reason: str | None = None
    last_no_trade_symbol: str | None = None

    contains_secret:       bool = False
    is_order_signal:       bool = False
    auto_apply_allowed:    bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("is_order_signal must be False")
        if self.auto_apply_allowed is not False:
            raise ValueError("auto_apply_allowed must be False")
        if self.is_live_authorization is not False:
            raise ValueError("is_live_authorization must be False")
        if self.contains_secret is not False:
            raise ValueError("contains_secret must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            # spec §6 응답 형식.
            "cycle_count":          self.cycle_count,
            "order_count":          self.order_count,
            "no_trade_count":       self.no_trade_count,
            "total_no_trade":       self.no_trade_count,   # alias (spec §6).
            "by_reason":            dict(self.by_reason),
            "by_no_trade_reason":   dict(self.by_reason),   # alias (spec §5).
            "recent":               list(self.recent),
            "last_no_trade":        self.last_no_trade,
            "last_no_trade_reason": self.last_no_trade_reason,
            "last_no_trade_symbol": self.last_no_trade_symbol,
            # 절대 invariant (JSON consumer 안전).
            "contains_secret":       False,
            "is_order_signal":       False,
            "auto_apply_allowed":    False,
            "is_live_authorization": False,
        }


def summarize_no_trade_reasons(
    events: list[dict[str, Any]] | None,
    *,
    limit: int = 10,
) -> NoTradeSummary:
    """ledger event dict 목록(시간 오름차순 가정) → 거래 없음 사유 집계.

    - `order_count`: PAPER_FILLED 체결 event 수.
    - `no_trade_count`: 거래 없음 cycle 수 (체결 외 전부).
    - `cycle_count`: order_count + no_trade_count (주문 없어도 증가).
    - `recent` 는 최신순, 최대 `limit` 개. `last_no_trade` 는 가장 최근 record.
    """
    records: list[dict[str, Any]] = []
    by_reason: dict[str, int] = {}
    order_count = 0
    for ev in (events or []):
        if is_trade_fill(ev):
            order_count += 1
            continue
        rec = classify_no_trade_event(ev)
        if rec is None:
            continue
        records.append(rec)
        by_reason[rec["reason_code"]] = by_reason.get(rec["reason_code"], 0) + 1

    no_trade_count = len(records)
    newest_first = list(reversed(records))
    recent = newest_first[: max(0, int(limit))]
    last = newest_first[0] if newest_first else None
    return NoTradeSummary(
        cycle_count=order_count + no_trade_count,
        order_count=order_count,
        no_trade_count=no_trade_count,
        by_reason=by_reason,
        recent=recent,
        last_no_trade=last,
        last_no_trade_reason=(last["reason_code"] if last else None),
        last_no_trade_symbol=(last.get("symbol") if last else None),
    )


__all__ = [
    "NO_TRADE_REASON_TITLES_KO",
    "NoTradeSummary",
    "normalize_no_trade_reason_code",
    "title_for",
    "category_for",
    "severity_for",
    "is_trade_fill",
    "classify_no_trade_event",
    "summarize_no_trade_reasons",
]
