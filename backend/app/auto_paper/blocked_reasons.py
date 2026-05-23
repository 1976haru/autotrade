"""P-17: 매수 불가 사유 집계 (read-only, advisory).

AI Paper / Agent / AutoPaperLoop 가 BUY 후보를 검토했지만 *자금 / 가격 / 위험 /
권한 / 시장* 조건 때문에 매수하지 못한 경우, "왜 안 샀는지" 를 사용자에게
설명하기 위한 **표시 전용 집계** 모듈.

본 모듈은 *기존 ledger event* (`PaperLoopEvent`) 를 read-only 로 읽어, 거기에
이미 기록된 reason_code (run-once / paper-flow 가 `risk_flags` / `metadata` 에
남긴 값) 를 정규화 + 집계할 뿐이다.

## 절대 invariant (CLAUDE.md 절대 원칙 상속)

- **실제 매수 로직을 열지 않는다** — 본 모듈은 *설명/표시* 전용.
- broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건.
- DB write 0건 — 입력 event dict 를 읽기만 한다.
- `BuyBlockSummary.is_order_signal=False` / `is_live_authorization=False` /
  `auto_apply_allowed=False` 불변.
- secret / API key / 계좌번호 carry 0건 — detail 은 안전 키만 통과.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# 정규 reason_code 어휘 + 한국어 제목 (단일 진실 — frontend formatter 와 정합)
# ─────────────────────────────────────────────────────────────────────────────


# code → 한국어 표시 제목.
BUY_BLOCK_REASON_TITLES_KO: dict[str, str] = {
    # 자금 / 수량.
    "MIN_LOT_NOT_AFFORDABLE":      "1주 가격이 투자한도 초과로 제외",
    "INSUFFICIENT_PAPER_CASH":     "남은 Paper 현금이 부족하여 매수 차단",
    "DAILY_BUY_LIMIT_EXCEEDED":    "일일 최대 매수금액을 초과하여 매수 차단",
    "DAILY_ORDER_LIMIT_EXCEEDED":  "일일 최대 주문 횟수를 초과하여 매수 차단",
    "NOTIONAL_LIMIT_EXCEEDED":     "1회 주문금액 한도를 초과하여 매수 차단",
    "SYMBOL_WEIGHT_LIMIT_EXCEEDED": "종목별 최대 비중을 초과하여 매수 차단",
    "MAX_POSITIONS_REACHED":       "최대 보유 종목 수에 도달하여 매수 차단",
    "DUPLICATE_POSITION_BUY_BLOCKED": "이미 보유 중인 종목이라 추가 매수 차단",
    # 가격.
    "PRICE_STALE":                 "현재가가 오래되어 매수 차단",
    "INVALID_PRICE":               "현재가가 비정상이라 매수 차단",
    "ABNORMAL_PRICE_MOVE":         "가격 급등락이 감지되어 매수 차단",
    # 권한 / 위험.
    "PAPER_EXECUTION_DISABLED":    "Paper 가상 실행이 비활성화되어 매수 보류",
    "BLOCKED_BY_PERMISSION_GATE":  "PermissionGate에서 매수 흐름이 차단되었습니다",
    "BLOCKED_BY_RISK_MANAGER":     "RiskManager에서 매수 흐름이 차단되었습니다",
    "EMERGENCY_STOP":              "긴급정지 상태라 매수하지 않았습니다",
    "AI_EXECUTION_DISABLED_SAFE":  "AI 자동 실행이 안전상 비활성화되어 매수하지 않았습니다",
    "LIVE_DISABLED_SAFE":          "실거래가 안전상 비활성화되어 있습니다",
    # 시장 / 데이터 / 전략.
    "MARKET_CLOSED":               "장 시간이 아니어서 매수하지 않았습니다",
    "NO_MARKET_DATA":              "시장 데이터가 없어 매수하지 않았습니다",
    "NO_STRATEGY_SIGNAL":          "전략 매수 신호가 없어 매수하지 않았습니다",
    "NO_CANDIDATE":                "매수 후보가 생성되지 않았습니다",
    "NO_UNIVERSE":                 "매매 대상 종목군이 비어 매수하지 않았습니다",
    "USING_FALLBACK_UNIVERSE":     "임시 종목군을 사용 중이라 매수를 보류했습니다",
    "STRATEGY_ENGINE_NOT_CONNECTED": "전략 엔진이 연결되지 않아 매수하지 않았습니다",
    "AUTO_BOT_NOT_RUNNING":        "자동매매가 실행 중이 아니어서 매수하지 않았습니다",
    "UNKNOWN":                     "알 수 없는 사유로 매수하지 않았습니다",
}


# code → category.
BUY_BLOCK_REASON_CATEGORY: dict[str, str] = {
    "MIN_LOT_NOT_AFFORDABLE":      "capital",
    "INSUFFICIENT_PAPER_CASH":     "capital",
    "DAILY_BUY_LIMIT_EXCEEDED":    "capital",
    "DAILY_ORDER_LIMIT_EXCEEDED":  "capital",
    "NOTIONAL_LIMIT_EXCEEDED":     "capital",
    "SYMBOL_WEIGHT_LIMIT_EXCEEDED": "capital",
    "MAX_POSITIONS_REACHED":       "capital",
    "DUPLICATE_POSITION_BUY_BLOCKED": "capital",
    "PRICE_STALE":                 "price",
    "INVALID_PRICE":               "price",
    "ABNORMAL_PRICE_MOVE":         "price",
    "PAPER_EXECUTION_DISABLED":    "permission",
    "BLOCKED_BY_PERMISSION_GATE":  "permission",
    "BLOCKED_BY_RISK_MANAGER":     "risk",
    "EMERGENCY_STOP":              "risk",
    "AI_EXECUTION_DISABLED_SAFE":  "permission",
    "LIVE_DISABLED_SAFE":          "permission",
    "MARKET_CLOSED":               "market",
    "NO_MARKET_DATA":              "market",
    "NO_STRATEGY_SIGNAL":          "strategy",
    "NO_CANDIDATE":                "strategy",
    "NO_UNIVERSE":                 "strategy",
    "USING_FALLBACK_UNIVERSE":     "strategy",
    "STRATEGY_ENGINE_NOT_CONNECTED": "system",
    "AUTO_BOT_NOT_RUNNING":        "system",
    "UNKNOWN":                     "unknown",
}


# code → severity.
BUY_BLOCK_REASON_SEVERITY: dict[str, str] = {
    "MIN_LOT_NOT_AFFORDABLE":      "info",
    "INSUFFICIENT_PAPER_CASH":     "warning",
    "DAILY_BUY_LIMIT_EXCEEDED":    "warning",
    "DAILY_ORDER_LIMIT_EXCEEDED":  "warning",
    "NOTIONAL_LIMIT_EXCEEDED":     "warning",
    "SYMBOL_WEIGHT_LIMIT_EXCEEDED": "warning",
    "MAX_POSITIONS_REACHED":       "info",
    "DUPLICATE_POSITION_BUY_BLOCKED": "info",
    "PRICE_STALE":                 "warning",
    "INVALID_PRICE":               "danger",
    "ABNORMAL_PRICE_MOVE":         "danger",
    "PAPER_EXECUTION_DISABLED":    "blocked",
    "BLOCKED_BY_PERMISSION_GATE":  "blocked",
    "BLOCKED_BY_RISK_MANAGER":     "blocked",
    "EMERGENCY_STOP":              "danger",
    "AI_EXECUTION_DISABLED_SAFE":  "info",
    "LIVE_DISABLED_SAFE":          "info",
    "MARKET_CLOSED":               "info",
    "NO_MARKET_DATA":              "warning",
    "NO_STRATEGY_SIGNAL":          "info",
    "NO_CANDIDATE":                "info",
    "NO_UNIVERSE":                 "warning",
    "USING_FALLBACK_UNIVERSE":     "info",
    "STRATEGY_ENGINE_NOT_CONNECTED": "warning",
    "AUTO_BOT_NOT_RUNNING":        "info",
    "UNKNOWN":                     "unknown",
}


# raw code (run-once / paper-flow / freshness / sizer) → 정규 code.
_CODE_ALIASES: dict[str, str] = {
    # 가격 freshness / sizer.
    "PRICE_MISSING":          "NO_MARKET_DATA",
    "STALE_DATA":             "PRICE_STALE",
    "STALE_PRICE":            "PRICE_STALE",
    "BELOW_MIN_LOT":          "MIN_LOT_NOT_AFFORDABLE",
    "PRICE_OVER_CAP":         "MIN_LOT_NOT_AFFORDABLE",
    "INSUFFICIENT_CASH":      "INSUFFICIENT_PAPER_CASH",
    "MAX_POSITIONS_REACHED":  "MAX_POSITIONS_REACHED",
    "BLOCKED_MAX_POSITIONS":  "MAX_POSITIONS_REACHED",
    # paper-flow guard sentinels.
    "PAPER_GUARD_DUPLICATE":     "DUPLICATE_POSITION_BUY_BLOCKED",
    "PAPER_GUARD_DAILY_LIMIT":   "DAILY_BUY_LIMIT_EXCEEDED",
    "PAPER_GUARD_SYMBOL_WEIGHT": "SYMBOL_WEIGHT_LIMIT_EXCEEDED",
    "PAPER_GUARD_RISK_MANAGER":  "BLOCKED_BY_RISK_MANAGER",
    # KIS paper permission gate sentinels.
    "KIS_PAPER_ORDER_LIMIT_EXCEEDED":    "DAILY_ORDER_LIMIT_EXCEEDED",
    "KIS_PAPER_NOTIONAL_LIMIT_EXCEEDED": "NOTIONAL_LIMIT_EXCEEDED",
    "KIS_PAPER_MAX_POSITIONS":           "MAX_POSITIONS_REACHED",
    # run-once misc.
    "UNKNOWN_ERROR":             "UNKNOWN",
}


# "성공" / "비차단" 으로 간주해 집계에서 제외하는 code (정규화 후).
_NON_BLOCK_CODES: frozenset[str] = frozenset({
    "OK",
    "VIRTUAL_ORDER_CANDIDATE_CREATED",
    "PAPER_DRY_RUN_OK",
    "PRICE_FRESHNESS_OK",
    "ALLOWED",
    "ALLOW",
    "AFFORDABLE",
    "SIZED",
    "DAILY_BUY_LIMIT_NOT_APPLICABLE",
    "SYMBOL_WEIGHT_LIMIT_NOT_APPLICABLE",
    "SKIP_NON_BUY",
})


# detail 에 통과시킬 안전 키 (secret / 계좌번호 류 차단 — allowlist).
_SAFE_DETAIL_KEYS: frozenset[str] = frozenset({
    "required_amount", "remaining_cash", "cap_krw", "price", "quantity",
    "notional_krw", "max_positions", "current_positions", "weight_pct",
    "max_symbol_weight_pct", "daily_buy_used", "daily_buy_limit",
    "age_seconds", "change_pct", "sizer_verdict", "freshness_reason_code",
})


def normalize_reason_code(raw: Any) -> str:
    """raw reason_code 문자열을 정규 어휘로 변환. 알 수 없으면 'UNKNOWN'."""
    if raw is None:
        return "UNKNOWN"
    code = str(raw).strip().upper()
    if not code:
        return "UNKNOWN"
    code = _CODE_ALIASES.get(code, code)
    if code in BUY_BLOCK_REASON_TITLES_KO:
        return code
    if code in _NON_BLOCK_CODES:
        return code   # 비차단 — caller 가 걸러냄.
    return "UNKNOWN"


def is_block_code(code: str) -> bool:
    """정규화된 code 가 *매수 차단* 사유인지."""
    return code in BUY_BLOCK_REASON_TITLES_KO and code not in _NON_BLOCK_CODES


def title_for(code: str) -> str:
    return BUY_BLOCK_REASON_TITLES_KO.get(code, BUY_BLOCK_REASON_TITLES_KO["UNKNOWN"])


def category_for(code: str) -> str:
    return BUY_BLOCK_REASON_CATEGORY.get(code, "unknown")


def severity_for(code: str) -> str:
    return BUY_BLOCK_REASON_SEVERITY.get(code, "unknown")


# ─────────────────────────────────────────────────────────────────────────────
# event → block record 추출
# ─────────────────────────────────────────────────────────────────────────────


def _raw_code_from_event(event: dict[str, Any]) -> str | None:
    """ledger event dict 에서 raw reason_code 후보를 추출 (정규화 전)."""
    meta = event.get("metadata") or {}
    if isinstance(meta, dict) and meta.get("reason_code"):
        return str(meta["reason_code"])
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


def extract_block_record(event: dict[str, Any]) -> dict[str, Any] | None:
    """단일 ledger event 가 *매수 차단* 을 나타내면 표시용 record, 아니면 None.

    차단 판정: 정규화된 reason_code 가 block code 거나, paper_fill_status 가
    PAPER_REJECTED 인 경우. 체결된(FILLED) BUY 는 차단이 아니다.
    """
    if not isinstance(event, dict):
        return None
    fill = str(event.get("paper_fill_status") or "")
    if fill == "PAPER_FILLED":
        return None   # 체결됨 — 차단 아님.

    raw = _raw_code_from_event(event)
    code = normalize_reason_code(raw)
    is_rejected = fill == "PAPER_REJECTED"

    # 명시적 차단 code (UNKNOWN 제외) 가 있으면 차단으로 집계.
    # UNKNOWN 은 *실제 거절(PAPER_REJECTED)* 일 때만 집계 — 정상 HOLD/NO_OP 는
    # code 가 없어 UNKNOWN 으로 정규화되더라도 차단으로 세지 않는다.
    has_explicit_block = is_block_code(code) and code != "UNKNOWN"
    if not has_explicit_block:
        if is_rejected:
            code = "UNKNOWN"   # 거절됐지만 code 불명.
        else:
            return None        # 차단 신호 없음 (정상 HOLD 등).

    return {
        "reason_code":    code,
        "title":          title_for(code),
        "category":       category_for(code),
        "severity":       severity_for(code),
        "symbol":         event.get("symbol"),
        "strategy":       event.get("strategy"),
        "timestamp":      event.get("timestamp"),
        "decision_action": event.get("decision_action"),
        "reason_message": event.get("reason"),
        "detail":         _safe_detail(event.get("metadata")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 집계 결과
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BuyBlockSummary:
    """매수 불가 사유 집계 — 표시 전용 안전 payload."""

    total_blocked: int
    by_reason:     dict[str, int]
    recent:        list[dict[str, Any]]
    last_block:    dict[str, Any] | None = None

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_blocked": self.total_blocked,
            "by_reason":     dict(self.by_reason),
            "recent":        list(self.recent),
            "last_block":    self.last_block,
            "is_order_signal":       False,
            "auto_apply_allowed":    False,
            "is_live_authorization": False,
        }


def summarize_blocked_reasons(
    events: list[dict[str, Any]] | None,
    *,
    limit: int = 10,
) -> BuyBlockSummary:
    """ledger event dict 목록(시간 오름차순 가정) → 매수 불가 사유 집계.

    `recent` 는 최신순 (가장 최근이 먼저), 최대 `limit` 개. `last_block` 은
    가장 최근 차단 record.
    """
    records: list[dict[str, Any]] = []
    by_reason: dict[str, int] = {}
    for ev in (events or []):
        rec = extract_block_record(ev)
        if rec is None:
            continue
        records.append(rec)
        by_reason[rec["reason_code"]] = by_reason.get(rec["reason_code"], 0) + 1

    # records 는 입력 순서(오름차순) — 최신순으로 뒤집어 recent / last_block.
    newest_first = list(reversed(records))
    recent = newest_first[: max(0, int(limit))]
    last_block = newest_first[0] if newest_first else None
    return BuyBlockSummary(
        total_blocked=len(records),
        by_reason=by_reason,
        recent=recent,
        last_block=last_block,
    )


__all__ = [
    "BUY_BLOCK_REASON_TITLES_KO",
    "BUY_BLOCK_REASON_CATEGORY",
    "BUY_BLOCK_REASON_SEVERITY",
    "BuyBlockSummary",
    "normalize_reason_code",
    "is_block_code",
    "title_for",
    "category_for",
    "severity_for",
    "extract_block_record",
    "summarize_blocked_reasons",
]
