"""KIS-INTRADAY-60D-WEEKLY-NEW-DATA — 고정 룰을 *추가 기간 새 데이터* 로 재검증 (read-only).

60D-WEEKLY-FIXED-REVALIDATION 의 고정 룰(`FORWARD_UNIVERSE_60D_WEEKLY_LOCKED_V1`)을 **절대
변경하지 않고**(rule hash 검증) 추가 기간 데이터에 적용해 forward 유지 여부를 본다.

**look-ahead 금지** (universe 는 각 rebalance 직전 lookback 만), **검증 중 파라미터 변경 금지**
(hash mismatch → BLOCKED). 추가 forward 기간 거래일이 부족하면 NEW_DATA_INSUFFICIENT.
Paper/Backtest only. broker / OrderExecutor / route_order / KIS 주문 API import·호출 0건.
EXE 빌드 0건, 실전 금지.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.system import locked_60d_weekly_validation as lk

# 룰 잠금 hash (LOCKED_PARAMS 캐논 sha256[:32]). 룰이 바뀌면 mismatch → BLOCKED.
EXPECTED_LOCKED_RULE_HASH = "97f7c5ff9d53bd9a4030f7a23737c6fc"
MIN_NEW_TRADING_DAYS = 20

# verdict.
NEW_DATA_BLOCKED = "NEW_DATA_BLOCKED"
NEW_DATA_INSUFFICIENT = "NEW_DATA_INSUFFICIENT"
NEW_DATA_FAIL = "NEW_DATA_FAIL"
NEW_DATA_WEAK = "NEW_DATA_WEAK"
NEW_DATA_WATCH = "NEW_DATA_WATCH"
NEW_DATA_PAPER_CANDIDATE = "NEW_DATA_PAPER_CANDIDATE"
NEW_DATA_RESEARCH_CONFIRMED = "NEW_DATA_RESEARCH_CONFIRMED"
_RANK = {NEW_DATA_BLOCKED: -1, NEW_DATA_INSUFFICIENT: 0, NEW_DATA_FAIL: 1, NEW_DATA_WEAK: 2,
         NEW_DATA_WATCH: 3, NEW_DATA_PAPER_CANDIDATE: 4, NEW_DATA_RESEARCH_CONFIRMED: 5}

_EXE_REC = {
    NEW_DATA_BLOCKED: "EXE 재빌드 보류 (룰 hash 불일치/안전 위반)",
    NEW_DATA_INSUFFICIENT: "EXE 재빌드 보류 — 추가 forward 데이터 부족(미존재). 새 기간 확보 후 재검증.",
    NEW_DATA_FAIL: "EXE 재빌드 보류 (새 데이터 forward 실패)",
    NEW_DATA_WEAK: "EXE 재빌드 보류 또는 관찰 UI만",
    NEW_DATA_WATCH: "관찰용 EXE 재빌드 가능, 자동매매/모의주문 비활성",
    NEW_DATA_PAPER_CANDIDATE: "EXE 재빌드 후 dry-run KIS 모의 리허설 가능 (자동주문 기본 OFF, DRY_RUN=true)",
    NEW_DATA_RESEARCH_CONFIRMED: "EXE 재빌드 후 제한적 dry-run 리허설 가능 (실전 금지, 실제 모의주문은 별도 승인 게이트)",
}

EXISTING_DIR = "data/market/intraday_ohlcv/kis_6m"
EXTRA_DIR = "data/market/intraday_5m_forward_extra"


@dataclass(frozen=True)
class NewDataReport:
    generated_at: str
    data_source: str
    locked_rule_name: str
    locked_rule_hash: str
    rule_hash_match: bool
    rule_locked_before_new_data_validation: bool
    no_parameter_change: bool
    no_look_ahead: bool
    additional_data_collected: bool
    new_data_quality: dict[str, Any]
    new_trading_days: int
    new_data_only: dict[str, Any]
    extended_walk_forward: dict[str, Any]
    slippage_stress: dict[str, Any]
    agent_mode_compare: dict[str, Any]
    breadth_stress: dict[str, Any]
    old_vs_new_decay: dict[str, Any]
    repeated_selected_change: dict[str, Any]
    overfit_warning: dict[str, Any]
    final_verdict: str
    paper_rehearsal_recommendation: str
    exe_rebuild_recommendation: str
    next_steps: tuple[str, ...]
    conclusions: tuple[str, ...]
    # 안전 불변값.
    do_not_auto_apply: bool = True
    auto_apply_allowed: bool = False
    is_live_authorization: bool = False
    live_trading_recommendation: bool = False
    real_order_allowed: bool = False
    dry_run_required: bool = True
    broker_order_sent: bool = False
    order_created: bool = False
    exe_build_executed: bool = False
    contains_secret: bool = False
    no_profit_guarantee: bool = True
    safety_disclaimer: str = (
        "이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. 새 데이터가 통과해도 실전매매는 "
        "금지이며, Paper rehearsal 은 KIS 모의매매 dry-run 후보일 뿐입니다(실제 모의주문도 별도 승인 "
        "게이트 필요). 수익을 보장하지 않습니다. 본 작업은 EXE 빌드를 수행하지 않습니다."
    )
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (not self.do_not_auto_apply or not self.no_profit_guarantee or self.auto_apply_allowed
                or self.exe_build_executed or self.live_trading_recommendation
                or self.real_order_allowed or not self.dry_run_required):
            raise ValueError("unsafe invariant")
        if self.is_live_authorization or self.broker_order_sent or self.order_created:
            raise ValueError("unsafe invariant True")
        if self.contains_secret:
            raise ValueError("contains_secret must be False")
        if self.final_verdict not in _RANK:
            raise ValueError(f"invalid verdict: {self.final_verdict}")


def compute_rule_hash() -> str:
    canon = json.dumps({"name": lk.LOCKED_RULE_NAME, "params": lk.LOCKED_PARAMS},
                       ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode()).hexdigest()[:32]


def _csv_dates(path: Path) -> set[str]:
    out: set[str] = set()
    try:
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ts = row.get("timestamp") or ""
                if len(ts) >= 10:
                    out.add(ts[:10])
    except OSError:
        pass
    return out


def _dir_dates(d: Path) -> set[str]:
    out: set[str] = set()
    if d.is_dir():
        for f in d.glob("*.csv"):
            out |= _csv_dates(f)
    return out


def run_new_data_validation(
    *, existing_dir: str | Path = EXISTING_DIR, extra_dir: str | Path = EXTRA_DIR,
    generated_at: str | None = None,
) -> NewDataReport:
    gen = generated_at or datetime.now(timezone.utc).isoformat()
    rule_hash = compute_rule_hash()
    hash_match = rule_hash == EXPECTED_LOCKED_RULE_HASH

    ed = _dir_dates(Path(existing_dir))
    xd = _dir_dates(Path(extra_dir))
    new_days = sorted(xd - ed)
    extra_files = len(list(Path(extra_dir).glob("*.csv"))) if Path(extra_dir).is_dir() else 0
    quality = {
        "existing_distinct_days": len(ed), "extra_distinct_days": len(xd),
        "new_trading_days_count": len(new_days), "new_trading_days": new_days[:60],
        "duplicate_removed_count": len(xd & ed), "extra_symbol_files": extra_files,
        "newest_existing": max(ed) if ed else None, "newest_extra": max(xd) if xd else None,
        "quality_status": "PASS" if len(new_days) >= MIN_NEW_TRADING_DAYS else (
            "WARN" if new_days else "FAIL"),
    }

    empty = {"note": "추가 forward 데이터 부족으로 미실행."}
    # 룰 hash mismatch → BLOCKED (룰 변경 감지).
    if not hash_match:
        return _build(gen, rule_hash, hash_match, quality, len(new_days), NEW_DATA_BLOCKED,
                      reason="locked rule hash 불일치 — 검증 중 룰 변경 감지(파라미터 변경 금지 위반).",
                      empty=empty, additional=extra_files > 0)
    # 새 거래일 부족 → INSUFFICIENT (heavy backtest 미실행).
    if len(new_days) < MIN_NEW_TRADING_DAYS:
        reason = (f"기존 6개월 미포함 새 거래일 {len(new_days)}일(<{MIN_NEW_TRADING_DAYS}). "
                  f"KIS 시세 최신일({quality['newest_extra']})이 기존 데이터에 이미 포함 — "
                  "추가 forward 기간이 사실상 미존재.")
        return _build(gen, rule_hash, hash_match, quality, len(new_days), NEW_DATA_INSUFFICIENT,
                      reason=reason, empty=empty, additional=extra_files > 0)

    # --- 충분한 새 데이터가 있을 때만 실행 (현재 미도달). 룰 고정 그대로 적용. ---
    from app.backtest.strategy_council_backtest import load_ohlcv_from_csv
    from app.system.wf_6m_signal_extract import extract_signal_map

    new_set = set(new_days)
    bars: list[Any] = []
    for f in sorted(Path(extra_dir).glob("*.csv")):
        bars += load_ohlcv_from_csv(str(f))
    # extended = existing + extra 이어붙임 (lookback 용 과거 확보).
    for f in sorted(Path(existing_dir).glob("*.csv")):
        bars += load_ohlcv_from_csv(str(f))
    signals = extract_signal_map(bars).signals
    rule = dict(lk._LOCKED_BASE_RULE)

    nd_only = lk._run(bars, signals, rule=rule, lookback=60, freq="weekly", size=10,
                      count_window=new_set, label="NEW_DATA_ONLY")
    ext_full = lk._run(bars, signals, rule=rule, lookback=60, freq="weekly", size=10,
                       label="EXTENDED_WALK_FORWARD")
    slip = {}
    for bps in (5.0, 7.0, 10.0):
        r = lk._run(bars, signals, rule={**rule, "slippage_bps": bps}, lookback=60, freq="weekly",
                    size=10, count_window=new_set, label=f"SLIP_{bps}")
        slip[f"slippage_{int(bps)}bps"] = lk._slim(r)
    slip_ok = (slip["slippage_10bps"]["forward_return_pct"] or -99) >= 0
    rv_off = lk._run(bars, signals, rule={**rule, "agent_mode": "AGENT_OFF"}, lookback=60,
                     freq="weekly", size=10, count_window=new_set, label="AGENT_OFF")
    agent_cmp = {"risk_veto_only": lk._slim(nd_only), "agent_off": lk._slim(rv_off),
                 "risk_veto_better": (nd_only["forward_return_pct"] or -99) > (rv_off["forward_return_pct"] or -99)}
    breadth = {}
    for sz in (15, 20):
        r = lk._run(bars, signals, rule=rule, lookback=60, freq="weekly", size=sz,
                    count_window=new_set, label=f"SIZE_{sz}")
        breadth[f"size_{sz}"] = lk._slim(r)
    decay = {"old_full_return": ext_full["forward_return_pct"],
             "new_only_return": nd_only["forward_return_pct"],
             "decay_pp": round((ext_full["forward_return_pct"] or 0)
                               - (nd_only["forward_return_pct"] or 0), 2)}

    verdict = _verdict_new(nd_only, slip_ok=slip_ok, risk_veto_better=agent_cmp["risk_veto_better"])
    return _build(gen, rule_hash, hash_match, quality, len(new_days), verdict,
                  reason="새 데이터 검증 실행.", empty=None, additional=True,
                  nd_only=lk._slim(nd_only), ext=lk._slim(ext_full), slip=slip,
                  agent=agent_cmp, breadth=breadth, decay=decay, slip_ok=slip_ok)


def _verdict_new(h: dict, *, slip_ok: bool, risk_veto_better: bool) -> str:
    fr = h.get("forward_return_pct")
    pf = h.get("median_pf")
    mdd = h.get("forward_mdd_pct")
    trades = h.get("total_trades", 0)
    if fr is None or pf is None:
        return NEW_DATA_FAIL
    if fr < 0 or pf < 1.05 or (mdd or 99) > 20:
        return NEW_DATA_FAIL
    suff = trades >= 50
    if fr >= 8 and pf >= 1.20 and (mdd or 99) <= 12 and suff and slip_ok and risk_veto_better:
        return NEW_DATA_RESEARCH_CONFIRMED
    if fr >= 5 and pf >= 1.15 and (mdd or 99) <= 15 and suff and slip_ok and risk_veto_better:
        return NEW_DATA_PAPER_CANDIDATE
    if fr >= 3 and pf >= 1.10 and (mdd or 99) <= 15:
        return NEW_DATA_WATCH
    return NEW_DATA_WEAK


def _build(gen, rule_hash, hash_match, quality, new_days, verdict, *, reason, empty,
           additional, nd_only=None, ext=None, slip=None, agent=None, breadth=None,
           decay=None, slip_ok=False) -> NewDataReport:
    paper_rec = ("KIS 모의매매 dry-run 리허설 후보 (새 데이터 통과, 단 실전 금지)"
                 if _RANK[verdict] >= _RANK[NEW_DATA_PAPER_CANDIDATE] else
                 "Paper 리허설 아직 불가 — 추가 forward 데이터 확보 후 재검증 필요")
    conclusions = [
        f"locked rule hash {'일치(룰 불변 확인)' if hash_match else '불일치(룰 변경 감지)'} · "
        f"새 거래일 {new_days}일 · verdict {verdict}.",
        reason,
        ("추가 forward 기간이 미존재해 새 데이터 검증을 *아직* 수행할 수 없다 — 기존 6개월 결과만으로는 "
         "종목 폭 의존/표본 한계가 남아 EXE 재빌드는 보류가 타당."
         if verdict == NEW_DATA_INSUFFICIENT else
         "고정 룰을 변경 없이 새 데이터에 적용한 결과."),
    ]
    next_steps = [
        "장이 더 진행돼 새 거래일(≥20)이 쌓이면 동일 고정 룰(hash 일치)로 자동 재검증.",
        "추가 종목/시장으로 breadth 의존을 완화할 데이터 확보.",
        "PAPER_CANDIDATE 이상 도달 시에만 dry-run(자동주문 OFF, DRY_RUN=true) EXE 재빌드 검토. 실전 금지.",
    ]
    return NewDataReport(
        generated_at=gen, data_source="KIS_INTRADAY_DAILYCHART_5M",
        locked_rule_name=lk.LOCKED_RULE_NAME, locked_rule_hash=rule_hash, rule_hash_match=hash_match,
        rule_locked_before_new_data_validation=True, no_parameter_change=True, no_look_ahead=True,
        additional_data_collected=additional, new_data_quality=quality, new_trading_days=new_days,
        new_data_only=nd_only or dict(empty or {}), extended_walk_forward=ext or dict(empty or {}),
        slippage_stress=({**slip, "slippage_10bps_ok": slip_ok} if slip else dict(empty or {})),
        agent_mode_compare=agent or dict(empty or {}), breadth_stress=breadth or dict(empty or {}),
        old_vs_new_decay=decay or dict(empty or {}),
        repeated_selected_change={"note": "새 데이터 충분 시 산출"} if empty else {},
        overfit_warning={"warning": _RANK[verdict] <= _RANK[NEW_DATA_FAIL],
                         "note": "새 데이터 부족/실패 시 forward 확인 불가 — 과적합 위험 잔존."},
        final_verdict=verdict, paper_rehearsal_recommendation=paper_rec,
        exe_rebuild_recommendation=_EXE_REC[verdict], next_steps=tuple(next_steps),
        conclusions=tuple(conclusions))


def to_dict(r: NewDataReport) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "data_source": r.data_source,
        "locked_rule_name": r.locked_rule_name, "locked_rule_hash": r.locked_rule_hash,
        "rule_hash_match": r.rule_hash_match,
        "rule_locked_before_new_data_validation": r.rule_locked_before_new_data_validation,
        "no_parameter_change": r.no_parameter_change, "no_look_ahead": r.no_look_ahead,
        "additional_data_collected": r.additional_data_collected,
        "new_data_quality": r.new_data_quality, "new_trading_days": r.new_trading_days,
        "new_data_only": r.new_data_only, "extended_walk_forward": r.extended_walk_forward,
        "slippage_stress": r.slippage_stress, "agent_mode_compare": r.agent_mode_compare,
        "breadth_stress": r.breadth_stress, "old_vs_new_decay": r.old_vs_new_decay,
        "repeated_selected_change": r.repeated_selected_change, "overfit_warning": r.overfit_warning,
        "final_verdict": r.final_verdict, "paper_rehearsal_recommendation": r.paper_rehearsal_recommendation,
        "exe_rebuild_recommendation": r.exe_rebuild_recommendation, "next_steps": list(r.next_steps),
        "conclusions": list(r.conclusions), "do_not_auto_apply": r.do_not_auto_apply,
        "auto_apply_allowed": r.auto_apply_allowed, "is_live_authorization": r.is_live_authorization,
        "live_trading_recommendation": r.live_trading_recommendation,
        "real_order_allowed": r.real_order_allowed, "dry_run_required": r.dry_run_required,
        "broker_order_sent": r.broker_order_sent, "order_created": r.order_created,
        "exe_build_executed": r.exe_build_executed, "contains_secret": r.contains_secret,
        "no_profit_guarantee": r.no_profit_guarantee, "safety_disclaimer": r.safety_disclaimer,
    }


def render_markdown(r: NewDataReport) -> str:
    q = r.new_data_quality
    lines = [
        "# 60d/weekly 고정 룰 — 추가 기간 새 데이터 재검증",
        "",
        "> 이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. 자동 적용/실전 전환/EXE 빌드 0건. 수익 보장 아님.",
        f"> locked_rule={r.locked_rule_name} · hash={r.locked_rule_hash} · "
        f"hash_match={r.rule_hash_match} · no_parameter_change={r.no_parameter_change} · "
        f"no_look_ahead={r.no_look_ahead}",
        "",
        f"## 최종 verdict: **{r.final_verdict}**",
        f"- EXE 재빌드 권고: **{r.exe_rebuild_recommendation}**",
        f"- Paper 리허설: {r.paper_rehearsal_recommendation}",
        f"- 실전매매 권고: **{r.live_trading_recommendation}** · 실제주문 허용: **{r.real_order_allowed}** · "
        f"dry_run 필수: **{r.dry_run_required}**",
        "",
        "## 추가 데이터 수집/품질",
        f"- 기존 distinct days {q.get('existing_distinct_days')} (newest {q.get('newest_existing')})",
        f"- 추가 distinct days {q.get('extra_distinct_days')} (newest {q.get('newest_extra')}) · "
        f"파일 {q.get('extra_symbol_files')}",
        f"- **새 거래일(기존 미포함): {q.get('new_trading_days_count')}** · 중복 제거 "
        f"{q.get('duplicate_removed_count')} · 품질 {q.get('quality_status')}",
        "",
        f"## New Data Only: {r.new_data_only}",
        f"## Extended Walk-forward: {r.extended_walk_forward}",
        f"## Slippage Stress: {r.slippage_stress}",
        f"## Agent Mode 비교: {r.agent_mode_compare}",
        f"## Breadth Stress: {r.breadth_stress}",
        f"## old vs new decay: {r.old_vs_new_decay}",
        f"## overfit warning: {r.overfit_warning}",
        "",
        "## 결론",
        *[f"- {c}" for c in r.conclusions],
        "## 다음 단계",
        *[f"- {s}" for s in r.next_steps],
        "",
        f"> {r.safety_disclaimer}",
    ]
    return "\n".join(lines) + "\n"
