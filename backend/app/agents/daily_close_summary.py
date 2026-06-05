"""PART3: 하루 마감 1장 요약 (초보자용, read-only).

배경 (2026-06-01): 운영자가 그날 결과를 보려면 매번 DB 를 수동 조회해야 했다.
장 마감 후(또는 수동 호출) 그날을 *쉬운 말 1장* 으로 정리해 reports/daily/ 에
자동 생성한다 — 운영자가 퇴근 후 이 1장만 보면 되게.

기존 #57 `daily_report_agent` 는 12 섹션 advisory 리포트(분석 깊음). 본 모듈은
*초보자 1장 요약* 으로 결이 다르다 — 거래·체결·손익·BUY/SELL/HOLD 분포·EGW
횟수·에이전트 성적·안전 플래그 확인을 한눈에.

CLAUDE.md 절대 원칙:
  - read-only DB SELECT 만 — broker / OrderExecutor / route_order 호출 0건
  - 외부 HTTP / AI SDK import 0건
  - 투자 조언 아님 — 종목 추천 / 매수매도 신호 0건
  - secret / 계좌번호 출력 0건 (숫자 집계만)
  - is_live_authorization / is_order_signal = False
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from typing import Any

# 기존 #57 의 KST 윈도우 + 로더를 재사용 (중복 구현 0건).
from app.agents.daily_report_agent import (
    _kst_day_window,
    load_agent_decisions_for_date,
    load_audit_rows_for_date,
    load_emergency_events_for_date,
)

_KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class DailyCloseSummary:
    """하루 마감 1장 요약 — read-only 집계. 투자 조언 아님."""

    report_date:        str
    # 거래
    buy_orders:         int = 0
    sell_orders:        int = 0
    buy_received:       int = 0
    sell_received:      int = 0
    rejected_orders:    int = 0
    filled_orders:      int = 0          # filled_quantity > 0 인 주문 수
    symbols_traded:     list[str] = field(default_factory=list)
    # 판단 분포
    decision_buy:       int = 0
    decision_sell:      int = 0
    decision_hold:      int = 0
    decision_total:     int = 0
    # 손익 (체결가 있을 때만)
    realized_pnl_krw:   float | None = None
    pnl_measurable:     bool = False
    # 운영 신호
    rejection_reasons:  dict[str, int] = field(default_factory=dict)
    emergency_events:   int = 0
    # 안전 플래그 (read-only 확인)
    safety_flags:       dict[str, Any] = field(default_factory=dict)
    safety_ok:          bool = True
    live_orders_sent:   int = 0          # is_paper=False / LIVE mode 실주문 (기대 0)

    is_live_authorization: bool = False
    is_order_signal:       bool = False
    contains_secret:       bool = False

    def __post_init__(self) -> None:
        if self.is_live_authorization is not False:
            raise ValueError("is_live_authorization must be False")
        if self.is_order_signal is not False:
            raise ValueError("is_order_signal must be False")
        if self.contains_secret is not False:
            raise ValueError("contains_secret must be False")


def _read_safety_flags() -> tuple[dict[str, Any], bool, bool]:
    """안전 플래그를 read-only 로 읽어 표시용 dict + ok 여부 반환.

    (settings 를 *읽기만* 한다 — mutation 0건.) market_data_provider 도 표시.
    """
    try:
        from app.core.config import get_settings
        s = get_settings()
        flags = {
            "ENABLE_LIVE_TRADING":         bool(s.enable_live_trading),
            "ENABLE_AI_EXECUTION":         bool(s.enable_ai_execution),
            "ENABLE_FUTURES_LIVE_TRADING": bool(s.enable_futures_live_trading),
            "KIS_IS_PAPER":                bool(s.kis_is_paper),
            "DEFAULT_MODE":                getattr(s.default_mode, "value", str(s.default_mode)),
            "MARKET_DATA_PROVIDER":        str(s.market_data_provider),
        }
        # 안전 기준: LIVE/AI/FUTURES off + KIS_IS_PAPER on.
        ok = (
            not flags["ENABLE_LIVE_TRADING"]
            and not flags["ENABLE_AI_EXECUTION"]
            and not flags["ENABLE_FUTURES_LIVE_TRADING"]
            and flags["KIS_IS_PAPER"]
        )
        return flags, ok, True
    except Exception:  # noqa: BLE001 — settings 미가용 시 안전쪽으로 unknown.
        return {}, False, False


def build_daily_close_summary(db, report_date: Date) -> DailyCloseSummary:
    """그날의 거래/판단/안전 집계 — read-only SELECT 만."""
    audits = load_audit_rows_for_date(db, report_date)
    decisions = load_agent_decisions_for_date(db, report_date)
    estops = load_emergency_events_for_date(db, report_date)

    buy_orders = sell_orders = buy_recv = sell_recv = rejected = filled = 0
    live_sent = 0
    symbols: list[str] = []
    rej_reasons: dict[str, int] = {}
    for r in audits:
        side = str(getattr(r, "side", "") or "").upper()
        bstat = str(getattr(r, "broker_status", "") or "").upper()
        mode = str(getattr(r, "mode", "") or "").upper()
        executed = bool(getattr(r, "executed", False))
        sym = getattr(r, "symbol", None)
        if sym and sym not in symbols:
            symbols.append(sym)
        if side in ("BUY", "B"):
            buy_orders += 1
            if bstat == "RECEIVED":
                buy_recv += 1
        elif side in ("SELL", "S"):
            sell_orders += 1
            if bstat == "RECEIVED":
                sell_recv += 1
        if bstat == "REJECTED":
            rejected += 1
            msg = str(getattr(r, "message", "") or "").strip() or "사유 미기록"
            rej_reasons[msg] = rej_reasons.get(msg, 0) + 1
        if int(getattr(r, "filled_quantity", 0) or 0) > 0:
            filled += 1
        # 실주문 (LIVE mode + executed) — 기대 0.
        if mode.startswith("LIVE") and executed:
            live_sent += 1

    d_buy = d_sell = d_hold = 0
    for d in decisions:
        dec = str(getattr(d, "decision", "") or "").upper()
        if dec == "BUY":
            d_buy += 1
        elif dec == "SELL":
            d_sell += 1
        elif dec == "HOLD":
            d_hold += 1

    flags, safety_ok, _ = _read_safety_flags()

    return DailyCloseSummary(
        report_date=report_date.isoformat(),
        buy_orders=buy_orders, sell_orders=sell_orders,
        buy_received=buy_recv, sell_received=sell_recv,
        rejected_orders=rejected, filled_orders=filled,
        symbols_traded=symbols,
        decision_buy=d_buy, decision_sell=d_sell, decision_hold=d_hold,
        decision_total=len(decisions),
        realized_pnl_krw=None,
        pnl_measurable=(filled > 0),
        rejection_reasons=rej_reasons,
        emergency_events=len(estops),
        safety_flags=flags, safety_ok=safety_ok,
        live_orders_sent=live_sent,
    )


def render_markdown(summary: DailyCloseSummary) -> str:
    """초보자용 1장 markdown — 쉬운 말."""
    s = summary
    lines: list[str] = []
    a = lines.append
    a(f"# 오늘의 자동매매 요약 — {s.report_date}")
    a("")
    a("> 이 화면은 **모의투자(Paper)** 결과입니다. 실제 돈으로 거래한 것이 "
      "아니며, 투자 조언도 아닙니다.")
    a("")

    # 안전 먼저 (가장 중요).
    a("## 🔒 안전 상태 (가장 먼저 확인)")
    if s.safety_ok and s.live_orders_sent == 0:
        a("- ✅ **안전합니다.** 실거래는 꺼져 있고, 모든 주문은 모의(Paper)였습니다.")
    else:
        a("- ⚠️ **확인 필요!** 아래 플래그를 점검하세요.")
    if s.safety_flags:
        for k in ("ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION",
                  "ENABLE_FUTURES_LIVE_TRADING", "KIS_IS_PAPER", "DEFAULT_MODE",
                  "MARKET_DATA_PROVIDER"):
            if k in s.safety_flags:
                a(f"  - {k} = `{s.safety_flags[k]}`")
    a(f"- 실거래(LIVE) 주문 전송: **{s.live_orders_sent}건** (정상값 0)")
    a("")

    # 거래 요약.
    a("## 📊 오늘 거래")
    a(f"- 매수 주문: **{s.buy_orders}건** (접수 {s.buy_received})")
    a(f"- 매도 주문: **{s.sell_orders}건** (접수 {s.sell_received})")
    a(f"- 거부된 주문: **{s.rejected_orders}건**")
    if s.pnl_measurable:
        a(f"- 체결 확인된 주문: **{s.filled_orders}건**")
    else:
        a("- 체결 확인: **아직 없음** (체결 조회가 꺼져 있거나 장외 시간일 수 "
          "있습니다)")
    if s.symbols_traded:
        a(f"- 거래한 종목: {', '.join(s.symbols_traded)}")
    a("")

    # 손익.
    a("## 💰 손익")
    if s.pnl_measurable and s.realized_pnl_krw is not None:
        a(f"- 실현 손익: **{int(s.realized_pnl_krw):,}원**")
    else:
        a("- 아직 계산할 수 없습니다 — 체결가가 기록되어야 손익을 알 수 있습니다 "
          "(체결 조회가 켜지면 다음부터 표시됩니다).")
    a("")

    # 판단 분포.
    a("## 🤖 AI 판단 분포")
    a(f"- 매수(BUY) 판단: {s.decision_buy}회")
    a(f"- 매도/청산(SELL) 판단: {s.decision_sell}회")
    a(f"- 관망(HOLD) 판단: {s.decision_hold}회")
    a(f"- 전체 판단 횟수: {s.decision_total}회")
    a("")

    # 운영 신호 / 거부 사유.
    a("## ⚠️ 살펴볼 점")
    if s.rejection_reasons:
        a("- 주문 거부 사유:")
        for reason, cnt in sorted(s.rejection_reasons.items(),
                                  key=lambda kv: -kv[1]):
            a(f"  - {reason} — {cnt}건")
    else:
        a("- 거부된 주문이 없습니다. 👍")
    a(f"- 긴급정지(Emergency Stop) 발동: {s.emergency_events}건")
    a("")

    a("---")
    a("_이 요약은 시스템 운영·점검용이며 투자 조언이 아닙니다. 실제 매매 결정은 "
      "운영자 책임입니다._")
    return "\n".join(lines)


__all__ = [
    "DailyCloseSummary",
    "build_daily_close_summary",
    "render_markdown",
]
