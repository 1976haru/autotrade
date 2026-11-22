"""KIS 모의 자동매매 일일 운영 보고서 (DAILY-OPERATION-REPORT-V1, 2026-10-26).

매 거래일 15:40 KST 이후(또는 다음 기동 시 누락분) 하루치 운영 결과를 Markdown + CSV 로 남긴다.
거래 0 건인 날도 생성한다. 순수 *조회* — 주문·취소·설정 변경·긴급정지 변경 0.

데이터 출처
- DB: order_audit_log(프로그램 주문 = trade_reason 'kis_paper_auto' / 취소 'kis_paper_cancel'),
  agent_decision_episode(Agent Council 판단·판단 시점 스냅샷·주문 지연), eod_liquidation_state(EOD 판정).
- KIS(읽기 전용, 실패해도 보고서는 생성): 잔고·보유(get_positions/get_balance), 당일 주문 잔량(list_today_orders),
  매수 종목의 당일 1분봉(30분 급등 추격 판정용, `market_data.kis_realtime._fetch_full_day_minutes`).
손익: 실제 체결가 FIFO(전 기간 프로그램 체결) — 당일 *청산된* 거래가 실현손익. 비용 = 수수료(매수·매도 각 1.5bp) + 매도 거래세(20bp),
`reporting.cost_tracker` 상수와 같다. 슬리피지는 체결가에 이미 포함되어 따로 빼지 않는다.
판정 기준(no-chase-gate 사전등록과 같음): 30분 급등 = 매수 직전 완결 1분봉 기준 r30 ≥ 2.6%, 시가 갭 = 판단 시점 갭 ≥ 3%.
값이 없으면 UNKNOWN — 추정하지 않는다.
"""
from __future__ import annotations

import csv
import io
import json
import os
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.reporting.cost_tracker import BUY_COMMISSION_BPS, SELL_COMMISSION_BPS, SELL_TAX_BPS

KST = timezone(timedelta(hours=9))
PROGRAM_REASON = "kis_paper_auto"
CANCEL_REASON = "kis_paper_cancel"
CHASE_R30 = 0.026
CHASE_GAP = 0.03
REPORT_AFTER = time(15, 40)
IS_LIVE_AUTHORIZATION = False


# ───────────────────────── 시간 ─────────────────────────
def utc_bounds(d: date) -> tuple[datetime, datetime]:
    lo = datetime.combine(d, time(0, 0), KST).astimezone(timezone.utc).replace(tzinfo=None)
    return lo, lo + timedelta(days=1)


def to_kst(ts: datetime) -> datetime:
    t = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return t.astimezone(KST)


def reports_dir() -> Path:
    from app.core.config import get_settings
    configured = str(getattr(get_settings(), "daily_operation_report_dir", "") or "").strip()
    if configured:
        return Path(configured)
    base = os.environ.get("APPDATA")
    root = Path(base) / "Autotrade" if base else Path("./data").resolve()
    return root / "reports" / "daily"


# ───────────────────────── 분류 ─────────────────────────
def exit_kind(reasons: list[str] | None, strategy: str | None = None) -> str:
    r = " ".join(reasons or []).lower() + " " + (strategy or "").lower()
    if "eod" in r or "장마감" in r or "market_close" in r:
        return "EOD"
    if "stop_loss" in r or "손절" in r:
        return "STOP_LOSS"
    if "take_profit" in r or "익절" in r:
        return "TAKE_PROFIT"
    if "sell_score" in r or "청산" in r or "sell" in r:
        return "SIGNAL_EXIT"
    return "OTHER"


def _meta(row) -> dict:
    m = row.ai_decision_meta
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError:
            m = {}
    return m if isinstance(m, dict) else {}


def _json(v) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


# ───────────────────────── FIFO ─────────────────────────
@dataclass
class Trade:
    symbol: str
    qty: int
    entry_at: datetime          # KST
    exit_at: datetime           # KST
    entry_px: float
    exit_px: float
    entry_audit_id: int
    exit_audit_id: int
    strategies: str
    exit_kind: str
    gross: float
    commission: float
    tax: float

    @property
    def net(self) -> float:
        return self.gross - self.commission - self.tax

    @property
    def net_bps(self) -> float:
        return self.net / (self.entry_px * self.qty) * 1e4 if self.entry_px and self.qty else 0.0


def fifo(fills: list) -> tuple[list[Trade], dict[str, list[dict]], int]:
    """fills = 시간순 프로그램 체결 행. (거래 목록, 종목별 미청산 로트, 짝 없는 매도 수량)."""
    lots: dict[str, deque] = defaultdict(deque)
    trades: list[Trade] = []
    unmatched = 0
    for f in fills:
        qty, px = int(f.filled_quantity or 0), float(f.avg_fill_price or 0)
        if qty <= 0 or px <= 0:
            continue
        meta = _meta(f)
        if str(f.side).upper() == "BUY":
            lots[f.symbol].append({"qty": qty, "px": px, "at": to_kst(f.created_at), "id": f.id,
                                   "strategies": ",".join(meta.get("selected_strategies") or []) or (f.strategy or "")})
            continue
        need = qty
        kind = exit_kind(meta.get("reasons"), f.strategy)
        while need > 0 and lots[f.symbol]:
            lot = lots[f.symbol][0]
            q = min(need, lot["qty"])
            gross = (px - lot["px"]) * q
            comm = (lot["px"] * q * BUY_COMMISSION_BPS + px * q * SELL_COMMISSION_BPS) / 1e4
            tax = px * q * SELL_TAX_BPS / 1e4
            trades.append(Trade(f.symbol, q, lot["at"], to_kst(f.created_at), lot["px"], px, lot["id"], f.id,
                                lot["strategies"], kind, gross, comm, tax))
            lot["qty"] -= q
            need -= q
            if lot["qty"] == 0:
                lots[f.symbol].popleft()
        unmatched += need
    open_lots = {s: [dict(x) for x in d] for s, d in lots.items() if d}
    return trades, open_lots, unmatched


def pf(values: list[float]) -> float | None:
    neg = -sum(v for v in values if v < 0)
    return round(sum(v for v in values if v > 0) / neg, 3) if neg > 0 else None


# ───────────────────────── 30분 급등 판정 ─────────────────────────
def r30_from_minutes(minutes: list[dict], entry_kst: datetime) -> float | None:
    """판단 직전 *완결* 1분봉 기준 30분 상승률. minutes = kis_realtime 형식({'t': HHMMSS, 'c': close}) 오름차순."""
    if entry_kst.time() < time(9, 30):
        return None
    cut_now = (entry_kst.replace(second=0, microsecond=0)).strftime("%H%M%S")          # 진행 중 분 제외
    cut_then = (entry_kst.replace(second=0, microsecond=0) - timedelta(minutes=30)).strftime("%H%M%S")
    done = [m for m in minutes if m.get("t", "") < cut_now]
    then = [m for m in minutes if m.get("t", "") < cut_then]
    if not done or not then or not then[-1].get("c"):
        return None
    return float(done[-1]["c"]) / float(then[-1]["c"]) - 1


def judge(v: float | None, th: float) -> str:
    if v is None:
        return "UNKNOWN"
    return "CHASE" if v >= th - 1e-9 else "NO"


# ───────────────────────── 보고서 생성 ─────────────────────────
BarsFn = Callable[[str, date], Awaitable[list[dict]]]


async def build_daily_operation_report(db, trading_date: date, *, broker=None, bars_fn: BarsFn | None = None,
                                       now: datetime | None = None) -> dict:
    from app.db.models import AgentDecisionEpisode, OrderAuditLog
    now = now or datetime.now(timezone.utc)
    lo, hi = utc_bounds(trading_date)
    rep: dict[str, Any] = {"trading_date": trading_date.isoformat(), "generated_at_kst": to_kst(now).isoformat(timespec="seconds"),
                           "is_live_authorization": False, "issues": [], "next_day_items": []}

    # P0 ACCOUNT-ISOLATION: 보고서는 *현재 운영 계좌* 원장만 — 다른 계좌·LEGACY 기록 혼입 금지.
    from app.kis_paper.account_scope import current_account_scope, label, legacy_unverified_holdings, scope_query
    scope = current_account_scope()
    rep["account_scope"] = label(scope) if scope is not None else "UNSCOPED(SIMULATION/MOCK)"
    try:
        rep["legacy_unverified_holdings"] = legacy_unverified_holdings(db) if scope is not None else []
    except Exception:  # noqa: BLE001
        rep["legacy_unverified_holdings"] = []

    # 1. 당일 주문
    day_orders = (scope_query(db.query(OrderAuditLog), OrderAuditLog, scope)
                  .filter(OrderAuditLog.created_at >= lo, OrderAuditLog.created_at < hi)
                  .order_by(OrderAuditLog.created_at, OrderAuditLog.id).all())
    prog = [o for o in day_orders if o.trade_reason == PROGRAM_REASON]
    cancels = [o for o in day_orders if o.trade_reason == CANCEL_REASON]
    other = [o for o in day_orders if o.trade_reason not in (PROGRAM_REASON, CANCEL_REASON)]

    def cnt(rows, side):
        rs = [o for o in rows if str(o.side).upper() == side]
        return {"submitted": sum(1 for o in rs if o.decision in ("APPROVED", "NEEDS_APPROVAL") and o.executed),
                "filled_orders": sum(1 for o in rs if (o.filled_quantity or 0) > 0),
                "filled_qty": sum(int(o.filled_quantity or 0) for o in rs),
                "rejected": sum(1 for o in rs if o.decision == "REJECTED" or str(o.broker_status or "").upper() == "REJECTED"),
                "approved_not_filled": sum(1 for o in rs if o.decision == "APPROVED" and not (o.filled_quantity or 0))}
    rep["orders"] = {"BUY": cnt(prog, "BUY"), "SELL": cnt(prog, "SELL"), "CANCEL": len(cancels),
                     "non_program_orders": len(other)}
    filled = [o for o in prog if (o.filled_quantity or 0) > 0]
    rep["filled"] = [{"time": to_kst(o.created_at).strftime("%H:%M:%S"), "symbol": o.symbol, "side": o.side,
                      "qty": int(o.filled_quantity), "avg_px": float(o.avg_fill_price or 0)} for o in filled]
    rej_reasons: dict[str, int] = defaultdict(int)
    for o in prog:
        if o.decision == "REJECTED":
            r = (o.reasons or ["?"])[0] if isinstance(o.reasons, list) else str(o.reasons)
            rej_reasons[str(r)[:60]] += 1
    rep["reject_reasons_top"] = sorted(rej_reasons.items(), key=lambda kv: -kv[1])[:5]

    # 2. 판단 기록(에피소드)
    eps = (db.query(AgentDecisionEpisode).filter(AgentDecisionEpisode.created_at >= lo, AgentDecisionEpisode.created_at < hi)
           .all())
    ep_by_audit = {e.audit_id: e for e in eps if e.audit_id is not None}
    latencies, api_errors, egw = [], 0, 0
    for e in eps:
        kr = _json(e.kis_order_result) or {}
        q = kr.get("order_quality") if isinstance(kr, dict) else None
        if isinstance(q, dict) and isinstance(q.get("latency_ms"), (int, float)):
            latencies.append(float(q["latency_ms"]))
        if e.reason_code == "KIS_PAPER_ERROR":
            api_errors += 1
        if "EGW00201" in json.dumps(kr, ensure_ascii=False):
            egw += 1
    rep["api"] = {"decision_episodes": len(eps), "kis_paper_errors": api_errors, "egw00201": egw,
                  "order_latency_ms": {"n": len(latencies),
                                       "avg": round(sum(latencies) / len(latencies)) if latencies else None,
                                       "max": round(max(latencies)) if latencies else None}}

    # 3. FIFO 손익 (전 기간 체결 → 당일 청산분)
    all_fills = (scope_query(db.query(OrderAuditLog), OrderAuditLog, scope).filter(OrderAuditLog.trade_reason == PROGRAM_REASON,
                                                OrderAuditLog.filled_quantity > 0, OrderAuditLog.created_at < hi)
                 .order_by(OrderAuditLog.created_at, OrderAuditLog.id).all())
    trades, open_lots, unmatched = fifo(all_fills)
    today = [t for t in trades if t.exit_at.date() == trading_date]
    rep["pnl"] = {"realized_trades": len(today),
                  "gross_krw": round(sum(t.gross for t in today)),
                  "commission_krw": round(sum(t.commission for t in today)),
                  "sell_tax_krw": round(sum(t.tax for t in today)),
                  "net_krw": round(sum(t.net for t in today)),
                  "win_rate": round(sum(1 for t in today if t.net > 0) / len(today), 3) if today else None,
                  "profit_factor": pf([t.net for t in today]),
                  "unmatched_sell_qty_all_time": unmatched,
                  "open_lots_end_of_day": {s: sum(x["qty"] for x in v) for s, v in open_lots.items()}}
    by_strat: dict[str, list[float]] = defaultdict(list)
    by_hour: dict[str, list[float]] = defaultdict(list)
    for t in today:
        by_strat[t.strategies or "?"].append(t.net)
        by_hour[f"{t.entry_at.hour:02d}시"].append(t.net)
    rep["by_strategy"] = {k: {"trades": len(v), "net_krw": round(sum(v)), "profit_factor": pf(v)}
                          for k, v in sorted(by_strat.items())}
    rep["by_entry_hour"] = {k: {"trades": len(v), "net_krw": round(sum(v))} for k, v in sorted(by_hour.items())}

    # 4. 당일 매수 — 매수 이유·Agent 판단·추격 판정
    minutes_cache: dict[str, list[dict] | None] = {}
    buys = []
    for o in (x for x in filled if str(x.side).upper() == "BUY"):
        meta, ep = _meta(o), ep_by_audit.get(o.id)
        council = _json(ep.council) if ep is not None else None
        snap = _json(ep.market_snapshot) if ep is not None else None
        gap = None
        if isinstance(snap, dict):
            if snap.get("open") and snap.get("previous_close"):
                gap = float(snap["open"]) / float(snap["previous_close"]) - 1
            elif isinstance(snap.get("gap_pct"), (int, float)):
                gap = float(snap["gap_pct"]) / 100.0
        r30 = None
        if bars_fn is not None:
            if o.symbol not in minutes_cache:
                try:
                    minutes_cache[o.symbol] = await bars_fn(o.symbol, trading_date)
                except Exception:  # noqa: BLE001 — 분봉 실패 = UNKNOWN
                    minutes_cache[o.symbol] = None
            mins = minutes_cache[o.symbol]
            r30 = r30_from_minutes(mins, to_kst(o.created_at)) if mins else None
        buys.append({"audit_id": o.id, "time": to_kst(o.created_at).strftime("%H:%M:%S"), "symbol": o.symbol,
                     "qty": int(o.filled_quantity), "avg_px": float(o.avg_fill_price or 0),
                     "strategies": meta.get("selected_strategies") or [], "reason": " / ".join(meta.get("reasons") or [])[:200],
                     "council_action": (council or {}).get("final_action") if isinstance(council, dict) else None,
                     "confidence": ep.confidence if ep is not None else None,
                     "quality": ep.quality_score if ep is not None else None,
                     "r30": r30, "chase_30m": judge(r30, CHASE_R30), "gap": gap, "chase_gap": judge(gap, CHASE_GAP)})
    rep["buys"] = buys
    rep["chase_summary"] = {k: {v: sum(1 for b in buys if b[k] == v) for v in ("CHASE", "NO", "UNKNOWN")}
                            for k in ("chase_30m", "chase_gap")}
    buy_flags = {b["audit_id"]: b for b in buys}

    # 5. 당일 매도 이유 / 청산 거래 / 최대 손실 5건
    rep["sells"] = [{"time": to_kst(o.created_at).strftime("%H:%M:%S"), "symbol": o.symbol,
                     "qty": int(o.filled_quantity), "avg_px": float(o.avg_fill_price or 0),
                     "exit_kind": exit_kind(_meta(o).get("reasons"), o.strategy),
                     "reason": " / ".join(_meta(o).get("reasons") or [])[:200]}
                    for o in filled if str(o.side).upper() == "SELL"]
    rep["trades"] = [{"symbol": t.symbol, "qty": t.qty, "entry": t.entry_at.isoformat(timespec="seconds"),
                      "exit": t.exit_at.isoformat(timespec="seconds"), "entry_px": t.entry_px, "exit_px": t.exit_px,
                      "strategies": t.strategies, "exit_kind": t.exit_kind, "gross_krw": round(t.gross),
                      "commission_krw": round(t.commission), "sell_tax_krw": round(t.tax), "net_krw": round(t.net),
                      "net_bps": round(t.net_bps, 1), "same_day": t.entry_at.date() == t.exit_at.date(),
                      "chase_30m": buy_flags.get(t.entry_audit_id, {}).get("chase_30m", "N/A(이전 거래일 매수)"),
                      "chase_gap": buy_flags.get(t.entry_audit_id, {}).get("chase_gap", "N/A(이전 거래일 매수)")}
                     for t in today]
    worst = sorted(rep["trades"], key=lambda x: x["net_krw"])[:5]
    for w in worst:
        cause = [w["exit_kind"]]
        if w["chase_30m"] == "CHASE":
            cause.append("30분 급등 추격 매수")
        if w["chase_gap"] == "CHASE":
            cause.append("시가 갭 추격 매수")
        if not w["same_day"]:
            cause.append("이월 보유")
        w["cause"] = ", ".join(cause)
    rep["worst5"] = [w for w in worst if w["net_krw"] < 0]

    # 6. EOD
    from app.kis_paper.eod_liquidation import get_state_row
    row = get_state_row(db, trading_date.isoformat())   # 현재 운영 계좌의 EOD 행만
    rep["eod"] = ({"state": row.state, "reason_code": row.reason_code, "final_verdict_at": str(row.final_verdict_at),
                   "totals": (row.summary or {}).get("totals"), "dry_run": (row.summary or {}).get("verdict_basis")}
                  if row is not None else {"state": "NO_RECORD",
                                           "note": "EOD 기록 없음 — 앱 미실행이거나 KIS 모의 자동매매가 승인되지 않은 날"})

    # 7. KIS 계좌(읽기 전용) — 실패하면 그대로 기록
    acct: dict[str, Any] = {"queried": broker is not None}
    if broker is not None:
        try:
            pos = await broker.get_positions()
            acct["holdings"] = [{"symbol": p.symbol, "qty": p.quantity, "avg_px": p.avg_price} for p in pos]
            bal = await broker.get_balance()
            acct["cash"] = bal.cash
            acct["status"] = "OK"
        except Exception as exc:  # noqa: BLE001
            acct["status"] = "ERROR"
            acct["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        if hasattr(broker, "list_today_orders"):
            try:
                rows = await broker.list_today_orders(kst_date=trading_date.strftime("%Y%m%d"))
                acct["open_orders"] = [r for r in rows if int(r.get("remaining") or 0) > 0 and not r.get("canceled")]
            except Exception as exc:  # noqa: BLE001
                acct["open_orders_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    rep["account"] = acct

    # 7b. 상태 요약 — 검증되지 않은 것을 정상으로 쓰지 않는다
    rep["status"] = _status_summary(db, rep, acct)

    # 8. 주요 문제 / 다음 거래일 검토
    iss, nxt = rep["issues"], rep["next_day_items"]
    if acct.get("status") == "ERROR":
        iss.append(f"KIS 모의계좌 조회 실패: {acct['error']}")
        if "41100000" in acct.get("error", "") or "모의투자 신청" in acct.get("error", ""):
            nxt.append("KIS 홈페이지에서 모의투자 신청(기간)을 확인·재신청")
    if rep["eod"]["state"] == "FLATTEN_INCOMPLETE":
        iss.append(f"EOD 청산 미완료({rep['eod'].get('reason_code')}) — 다음 거래일 신규 매수 차단, 운영자 확인 필요")
        nxt.append("잔여 보유·미체결 확인 후 /api/kis-paper/eod/acknowledge 로 확인 처리")
    if acct.get("holdings"):
        iss.append(f"보고 시점 KIS 보유 {len(acct['holdings'])}종목 — 당일 청산형 원칙 위반 여부 확인")
    if acct.get("open_orders"):
        iss.append(f"보고 시점 미체결 주문 {len(acct['open_orders'])}건")
    if rep["api"]["egw00201"]:
        iss.append(f"호출 제한(EGW00201) {rep['api']['egw00201']}건")
    if rep["api"]["kis_paper_errors"]:
        iss.append(f"KIS 주문 오류 {rep['api']['kis_paper_errors']}건")
    chased = [b for b in buys if b["chase_30m"] == "CHASE" or b["chase_gap"] == "CHASE"]
    if chased:
        iss.append(f"추격 매수 {len(chased)}건(30분 급등 또는 시가 갭)")
        nxt.append("추격 매수 거래의 손익을 Shadow 관찰 결과와 대조")
    if rep["pnl"]["net_krw"] < 0:
        nxt.append("최대 손실 거래의 청산 유형·진입 사유 검토")
    if not prog:
        iss.append("프로그램 주문 0건(거래 기회 없음 또는 자동매매 미실행)")
    return rep


def _status_summary(db, rep: dict, acct: dict) -> dict:
    import hashlib

    from app.core.config import get_settings
    from app.db.models import EmergencyStopEvent
    s = get_settings()
    account_no = str(getattr(s, "kis_account_no", "") or "")
    ks = db.query(EmergencyStopEvent).order_by(EmergencyStopEvent.id.desc()).first()
    eod_state = rep["eod"]["state"]
    if eod_state == "FLAT_CONFIRMED" and not rep["eod"].get("dry_run"):
        eod_v = "EOD_FLAT_CONFIRMED"
    elif eod_state == "FLAT_CONFIRMED":
        eod_v = "EOD_DRY_RUN_ONLY"
    elif eod_state == "FLATTEN_INCOMPLETE":
        eod_v = "EOD_INCOMPLETE"
    else:
        eod_v = "EOD_NOT_VERIFIED"
    return {
        "account_verification": "ACCOUNT_VERIFIED" if acct.get("status") == "OK" else "ACCOUNT_NOT_VERIFIED",
        "account_verification_reason": (None if acct.get("status") == "OK" else
                                        acct.get("error") or "계좌를 조회하지 않음(KIS 자격·모의 설정 없음)"),
        "account_ref": (f"fp:{hashlib.sha256(account_no.encode()).hexdigest()[:8]}·상품{account_no[-2:]}"
                        if len(account_no) >= 10 else "미설정"),
        "kill_switch": ({"active": bool(ks.enabled), "level": ks.level,
                         "legacy_strict": bool(ks.enabled) and not getattr(ks, "policy_version", None)}
                        if ks is not None else {"active": False, "level": "OFF", "legacy_strict": False}),
        "auto_trading_enabled": bool(getattr(s, "enable_kis_paper_auto_trading", False)),
        "order_dry_run": bool(getattr(s, "kis_paper_auto_order_dry_run", True)),
        "background_tick": bool(getattr(s, "enable_ai_paper_background_tick", False)),
        "fill_polling": bool(getattr(s, "enable_fill_polling", False)),
        "eod_verification": eod_v,
        "program_orders": sum(rep["orders"][k]["submitted"] + rep["orders"][k]["rejected"] for k in ("BUY", "SELL")),
        "fills": len(rep["filled"]),
        "realized_net_krw": rep["pnl"]["net_krw"],
    }


# ───────────────────────── 출력 ─────────────────────────
def _fmt(n) -> str:
    return "-" if n is None else (f"{n:,.0f}" if isinstance(n, (int, float)) else str(n))


def render_markdown(r: dict) -> str:
    o, p = r["orders"], r["pnl"]
    st = r["status"]
    ks = st["kill_switch"]
    eod_text = {"EOD_FLAT_CONFIRMED": "EOD 청산 확인(FLAT_CONFIRMED)",
                "EOD_DRY_RUN_ONLY": "EOD 판정은 dry-run 기준 — 실제 청산 아님",
                "EOD_INCOMPLETE": "EOD 청산 미완료(FLATTEN_INCOMPLETE)",
                "EOD_NOT_VERIFIED": "EOD 검증 불가 — 실행 기록 없음(거래가 없었다고 정상 완료로 보지 않음)"}[st["eod_verification"]]
    L = [f"# KIS 모의 자동매매 일일 보고서 — {r['trading_date']}", "",
         f"생성 {r['generated_at_kst']} · 실계좌 거래 아님(모의) · 이 보고서는 주문을 실행하지 않습니다.", "",
         "## 0. 상태 요약",
         f"- 계좌: **{st['account_verification']}** ({st['account_ref']})"
         + (f" — {st['account_verification_reason']}" if st["account_verification_reason"] else ""),
         f"- 프로그램 주문 {st['program_orders']}건 · 실제 체결 {st['fills']}건 · 실현 순손익 {_fmt(st['realized_net_krw'])}원",
         f"- {eod_text}",
         f"- 긴급정지: {'활성 ' + str(ks['level']) + (' (legacy strict — 모든 주문·취소 차단)' if ks['legacy_strict'] else '') if ks['active'] else '해제'}",
         f"- 자동매매: {'활성' if st['auto_trading_enabled'] else '비활성'} · 주문 dry-run {'켜짐' if st['order_dry_run'] else '꺼짐'} · "
         f"스캔 실행기 {'켜짐' if st['background_tick'] else '꺼짐'} · 체결 조회 {'켜짐' if st['fill_polling'] else '꺼짐'}",
         f"- 원장 계좌 스코프: {r.get('account_scope', '?')} (이 보고서의 주문·손익은 이 계좌 기록만)",
         "", "## 1. 계좌 상태 (KIS 모의, 읽기 전용)"]
    for lh in r.get("legacy_unverified_holdings") or []:
        L.insert(L.index("## 0. 상태 요약") + 1,
                 f"- ⚠ 구계좌(LEGACY_ACCOUNT) 미확인 보유: {lh['symbol']} {lh['quantity']}주 — 격리 보존(현재 계좌 계산 제외, 자동 청산 대상 아님)")
    a = r["account"]
    if not a.get("queried"):
        L.append("- ACCOUNT_NOT_VERIFIED — 조회하지 않음(KIS 자격·모의 설정 없음). 보유·현금은 알 수 없음")
    elif a.get("status") == "OK":
        L.append(f"- 현금 {_fmt(a.get('cash'))}원 · 보유 {len(a.get('holdings') or [])}종목 · 미체결 {len(a.get('open_orders') or [])}건")
        for h in a.get("holdings") or []:
            L.append(f"  - {h['symbol']} {h['qty']}주 @ {_fmt(h['avg_px'])}")
    else:
        L.append(f"- **조회 실패 (ACCOUNT_NOT_VERIFIED)**: {a.get('error')} — 보유·현금·미체결은 확인되지 않음(0 아님)")
    L += ["", "## 2. 주문 건수 (프로그램)", "| 구분 | 접수 | 체결 주문 | 체결 수량 | 거절 | 승인됐으나 미체결 |", "|---|---|---|---|---|---|"]
    for s in ("BUY", "SELL"):
        x = o[s]
        L.append(f"| {s} | {x['submitted']} | {x['filled_orders']} | {x['filled_qty']} | {x['rejected']} | {x['approved_not_filled']} |")
    L.append(f"\n- 취소 {o['CANCEL']}건 · 프로그램 외 주문 {o['non_program_orders']}건")
    if r["reject_reasons_top"]:
        L.append("- 거절 사유 상위: " + "; ".join(f"{k} ({v})" for k, v in r["reject_reasons_top"]))
    L += ["", "## 3. 손익 (당일 청산분, 실제 체결가 FIFO)",
          f"- 청산 거래 {p['realized_trades']}건 · 총손익 {_fmt(p['gross_krw'])}원 · 수수료 {_fmt(p['commission_krw'])}원 · "
          f"거래세 {_fmt(p['sell_tax_krw'])}원 · **순손익 {_fmt(p['net_krw'])}원**",
          f"- 승률 {p['win_rate'] if p['win_rate'] is not None else '-'} · Profit Factor {p['profit_factor'] if p['profit_factor'] is not None else '-'}",
          f"- 장 마감 기준 미청산 로트: {p['open_lots_end_of_day'] or '없음'}", "",
          "### 전략별", "| 전략 | 거래 | 순손익 | PF |", "|---|---|---|---|"]
    L += [f"| {k} | {v['trades']} | {_fmt(v['net_krw'])} | {v['profit_factor'] or '-'} |" for k, v in r["by_strategy"].items()] or ["| - | 0 | - | - |"]
    L += ["", "### 진입 시간대별", "| 시간 | 거래 | 순손익 |", "|---|---|---|"]
    L += [f"| {k} | {v['trades']} | {_fmt(v['net_krw'])} |" for k, v in r["by_entry_hour"].items()] or ["| - | 0 | - |"]
    L += ["", "## 4. 매수 (이유 · Agent 판단 · 추격 여부)"]
    if not r["buys"]:
        L.append("- 매수 체결 없음")
    for b in r["buys"]:
        r30 = "-" if b["r30"] is None else f"{b['r30']:+.2%}"
        gap = "-" if b["gap"] is None else f"{b['gap']:+.2%}"
        L.append(f"- {b['time']} {b['symbol']} {b['qty']}주 @ {_fmt(b['avg_px'])} · 전략 {','.join(b['strategies']) or '-'} · "
                 f"신뢰도 {b['confidence']} · 품질 {b['quality']} · 30분 급등 {b['chase_30m']}({r30}) · "
                 f"시가 갭 {b['chase_gap']}({gap})")
        if b["reason"]:
            L.append(f"  - 이유: {b['reason']}")
    cs = r["chase_summary"]
    L.append(f"- 추격 요약: 30분 급등 {cs['chase_30m']} · 시가 갭 {cs['chase_gap']}")
    L += ["", "## 5. 매도 (이유)"]
    L += [f"- {s['time']} {s['symbol']} {s['qty']}주 @ {_fmt(s['avg_px'])} · {s['exit_kind']} · {s['reason']}" for s in r["sells"]] or ["- 매도 체결 없음"]
    L += ["", "## 6. 최대 손실 거래 5건"]
    L += [f"- {w['symbol']} {w['qty']}주 {_fmt(w['net_krw'])}원 ({w['net_bps']}bp) · 원인: {w['cause']}" for w in r["worst5"]] or ["- 손실 거래 없음"]
    e = r["eod"]
    L += ["", "## 7. EOD 청산", f"- 상태: **{e['state']}** {e.get('reason_code') or ''} {e.get('note') or ''}".rstrip(),
          f"- 잔여(프로그램 기준): {e.get('totals') or '-'}" + (f" · 판정 근거 {e['dry_run']}" if e.get("dry_run") else "")]
    api = r["api"]
    L += ["", "## 8. API 오류 · 주문 지연",
          f"- 판단 기록 {api['decision_episodes']}건 · KIS 주문 오류 {api['kis_paper_errors']}건 · EGW00201 {api['egw00201']}건",
          f"- 주문 지연: 평균 {_fmt(api['order_latency_ms']['avg'])}ms · 최대 {_fmt(api['order_latency_ms']['max'])}ms (n={api['order_latency_ms']['n']})",
          "", "## 9. 오늘의 주요 문제"]
    L += [f"- {x}" for x in r["issues"]] or ["- 없음"]
    L += ["", "## 10. 다음 거래일 검토 항목"]
    L += [f"- {x}" for x in r["next_day_items"]] or ["- 없음"]
    return "\n".join(L) + "\n"


def trades_csv(r: dict) -> str:
    cols = ["symbol", "qty", "entry", "exit", "entry_px", "exit_px", "strategies", "exit_kind", "gross_krw",
            "commission_krw", "sell_tax_krw", "net_krw", "net_bps", "same_day", "chase_30m", "chase_gap"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(r["trades"])
    return buf.getvalue()


def buys_csv(r: dict) -> str:
    cols = ["time", "symbol", "qty", "avg_px", "strategies", "confidence", "quality", "r30", "chase_30m", "gap", "chase_gap", "reason"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for b in r["buys"]:
        w.writerow({**b, "strategies": ",".join(b["strategies"])})
    return buf.getvalue()


def write_report(r: dict, out_dir: Path | None = None) -> dict[str, str]:
    d = Path(out_dir or reports_dir())
    d.mkdir(parents=True, exist_ok=True)
    stem = r["trading_date"]
    files = {"markdown": d / f"{stem}.md", "trades_csv": d / f"{stem}_trades.csv",
             "buys_csv": d / f"{stem}_buys.csv", "json": d / f"{stem}.json"}
    for key, text in (("markdown", render_markdown(r)), ("trades_csv", trades_csv(r)), ("buys_csv", buys_csv(r)),
                      ("json", json.dumps(r, ensure_ascii=False, indent=1, default=str))):
        tmp = files[key].with_suffix(files[key].suffix + ".tmp")
        if key.endswith("csv"):      # csv 모듈이 \r\n 을 넣으므로 줄바꿈 변환 끔(Windows 빈 줄 방지). BOM = 엑셀 한글
            tmp.write_text(text, encoding="utf-8-sig", newline="")
        else:
            tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, files[key])
    return {k: str(v) for k, v in files.items()}
