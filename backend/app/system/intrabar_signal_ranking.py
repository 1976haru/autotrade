"""Intrabar execution + composite signal ranking 종합 리포트 (백테스트 전용).

1분봉 replay 체결(intrabar_execution) + composite signal ranking(signal_ranking)을
합쳐 (A) 5분봉 체결 착시가 줄었는지, (B) ranking 이 선착순 대비 더 나은 신호를
고르는지 비교한다. 1분봉 coverage 가 부족하면 WARN(FAIL 아님) + "체결 정확도 낮음".

본 모듈은 broker / OrderExecutor / route_order / KIS API import 0건, 실주문 0건,
read-only. 결과는 *연구용 백테스트* — is_live_authorization=False,
no_profit_guarantee=True.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.backtest.intrabar_execution import CostModel, simulate_intrabar_execution
from app.backtest.signal_ranking import (
    SignalCandidate,
    earliest_first,
    rank_signals,
)

# 1분봉 subset 표준 경로.
ONE_MIN_SUBSET_DIR = Path("data/market/robust_intraday_1m_subset")
_SYMBOL_RE = re.compile(r"^(\d{6})")


def scan_1m_coverage(base_dir: Path | None = None) -> dict[str, Any]:
    """1분봉 subset coverage — 없으면 graceful (WARN, FAIL 아님)."""
    d = Path(base_dir) if base_dir else ONE_MIN_SUBSET_DIR
    symbols: set[str] = set()
    files = 0
    if d.exists():
        for p in d.glob("*.csv"):
            files += 1
            m = _SYMBOL_RE.match(p.stem)
            if m:
                symbols.add(m.group(1))
    return {
        "dir": str(d),
        "exists": d.exists(),
        "files": files,
        "symbols_with_1m": sorted(symbols),
        "symbol_count": len(symbols),
        "status": "OK" if symbols else "WARN",
        "warning": ("" if symbols else
                    "1분봉 subset 없음 — 5분봉 fallback 사용, 체결 정확도 낮음(LOW)."),
    }


def _synthetic_trades() -> list[dict[str, Any]]:
    """샘플 거래 — 5분봉에서 stop/target 동시터치 케이스 포함 (1분봉으로 순서판정)."""
    t0 = datetime(2026, 5, 22, 1, 0, tzinfo=timezone.utc)  # 10:00 KST

    def bars5(seq):
        return [{"timestamp": (t0 + timedelta(minutes=5 * i)).isoformat(),
                 "open": o, "high": h, "low": lo, "close": c}
                for i, (o, h, lo, c) in enumerate(seq)]

    def bars1(seq):
        return [{"timestamp": (t0 + timedelta(minutes=i)).isoformat(),
                 "open": o, "high": h, "low": lo, "close": c}
                for i, (o, h, lo, c) in enumerate(seq)]

    # 거래1: 5분봉 한 캔들에서 stop·target 동시터치 → 1분봉은 target 먼저.
    trade1 = {
        "name": "ambiguous_target_first_in_1m",
        "side": "BUY", "entry_time": t0.isoformat(), "entry_price": 100.0,
        "stop_price": 98.0, "target_price": 103.0, "max_hold_minutes": 25,
        "bars_5m": bars5([(100, 103, 98, 101)]),
        "bars_1m": bars1([(100, 101, 100, 100.5), (100.5, 103, 100, 102),  # target 먼저
                          (102, 103, 98, 99)]),
    }
    # 거래2: 1분봉 없음 → 5분봉 fallback, 동시터치 → stop-first.
    trade2 = {
        "name": "fallback_ambiguous_stop_first",
        "side": "BUY", "entry_time": t0.isoformat(), "entry_price": 100.0,
        "stop_price": 98.0, "target_price": 103.0, "max_hold_minutes": 25,
        "bars_5m": bars5([(100, 103, 98, 100)]),
        "bars_1m": None,
    }
    # 거래3: 깔끔한 target.
    trade3 = {
        "name": "clean_target",
        "side": "BUY", "entry_time": t0.isoformat(), "entry_price": 100.0,
        "stop_price": 97.0, "target_price": 102.0, "max_hold_minutes": 25,
        "bars_5m": bars5([(100, 100.5, 99.8, 100.2), (100.2, 102.5, 100, 102)]),
        "bars_1m": bars1([(100, 100.5, 99.8, 100.2)] + [(100.2, 102.5, 100, 102)]),
    }
    return [trade1, trade2, trade3]


def _sample_candidates() -> list[SignalCandidate]:
    """동시 발생 후보 — 선착순(앞) vs 품질(뒤) 충돌 케이스."""
    base = "2026-05-22T01:00:00+00:00"
    later = "2026-05-22T01:00:30+00:00"
    return [
        # 먼저 왔지만 품질 낮음 (선착순이면 선택, composite 면 탈락 후보).
        SignalCandidate(symbol="000001", strategy="ORB", timestamp=base, confidence=0.55,
                        quality_score=55, expected_move_bps=20, estimated_cost_bps=15,
                        volume_expansion=1.2, liquidity_score=40, regime_label="SIDEWAYS",
                        symbol_group="A"),
        SignalCandidate(symbol="000002", strategy="VWAP", timestamp=base, confidence=0.6,
                        quality_score=60, expected_move_bps=25, estimated_cost_bps=18,
                        volume_expansion=1.5, liquidity_score=50, regime_label="UNKNOWN",
                        symbol_group="A"),
        # 늦게 왔지만 품질 높음 (composite 면 선택).
        SignalCandidate(symbol="005930", strategy="MOMENTUM", timestamp=later,
                        confidence=0.85, quality_score=88, expected_move_bps=45,
                        estimated_cost_bps=15, volume_expansion=2.5, liquidity_score=90,
                        regime_label="TREND_UP", symbol_group="B"),
        SignalCandidate(symbol="000660", strategy="GAP", timestamp=later, confidence=0.8,
                        quality_score=82, expected_move_bps=40, estimated_cost_bps=15,
                        volume_expansion=2.2, liquidity_score=85, regime_label="TREND_UP",
                        symbol_group="B"),
        # agent veto → 무조건 제외.
        SignalCandidate(symbol="111111", strategy="ORB", timestamp=base, confidence=0.9,
                        quality_score=95, expected_move_bps=60, estimated_cost_bps=15,
                        agent_risk_veto=True, symbol_group="C"),
    ]


def build_report(*, one_min_dir: Path | None = None,
                 slippage_bps: float = 5.0, max_slots: int = 5,
                 position_count: int = 3) -> dict[str, Any]:
    """종합 리포트 dict 생성 — 합성 샘플 기반(데이터 비의존). read-only."""
    coverage = scan_1m_coverage(one_min_dir)
    cost = CostModel(slippage_bps=slippage_bps)

    # ---- A. intrabar execution: 5분봉 fallback vs 1분봉 replay ----
    exec_rows = []
    ambiguous = replayable = fallback = 0
    conf_dist = {"HIGH": 0, "LOW": 0}
    for t in _synthetic_trades():
        five = simulate_intrabar_execution(
            side=t["side"], entry_time=t["entry_time"], entry_price=t["entry_price"],
            stop_price=t["stop_price"], target_price=t["target_price"],
            max_hold_minutes=t["max_hold_minutes"], bars_5m=t["bars_5m"],
            bars_1m=None, cost=cost)
        one = simulate_intrabar_execution(
            side=t["side"], entry_time=t["entry_time"], entry_price=t["entry_price"],
            stop_price=t["stop_price"], target_price=t["target_price"],
            max_hold_minutes=t["max_hold_minutes"], bars_5m=t["bars_5m"],
            bars_1m=t["bars_1m"], cost=cost)
        if one.execution_source == "ONE_MINUTE_REPLAY":
            replayable += 1
        else:
            fallback += 1
        if "AMBIGUOUS" in one.exit_reason or "AMBIGUOUS" in one.execution_source:
            ambiguous += 1
        conf_dist[one.execution_confidence] = conf_dist.get(one.execution_confidence, 0) + 1
        exec_rows.append({
            "name": t["name"],
            "five_minute": five.to_dict(),
            "intrabar": one.to_dict(),
            "exit_reason_changed": five.exit_reason != one.exit_reason,
            "net_pnl_diff": round(one.net_pnl - five.net_pnl, 6),
        })

    # ---- B. signal ranking: earliest-first vs composite ----
    cands = _sample_candidates()
    ef = earliest_first(cands, max_slots=max_slots, position_count=position_count)
    ranked = rank_signals(cands, max_slots=max_slots, position_count=position_count,
                          max_symbols_per_group=1)
    ef_syms = {d["symbol"] for d in ef}
    comp_syms = {d["symbol"] for d in ranked.selected}

    stop_first_count = sum(1 for r in exec_rows
                           if "STOP_FIRST" in r["intrabar"]["exit_reason"])

    return {
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "research_backtest_sample",
        # 1. baseline / 2. coverage
        "baseline_5m": {"trades": len(exec_rows),
                        "note": "5분봉 fallback 기준 (1분봉 미사용)"},
        "one_minute_coverage": {
            "symbols_with_1m": coverage["symbols_with_1m"],
            "symbol_count": coverage["symbol_count"],
            "replayable_trades": replayable,
            "fallback_trades": fallback,
            "ambiguous_trades": ambiguous,
            "confidence_distribution": conf_dist,
            "status": coverage["status"],
            "warning": coverage["warning"],
        },
        # 3~5. execution 비교 / ambiguous / stop-first
        "execution_comparison": exec_rows,
        "ambiguous_trade_count": ambiguous,
        "conservative_stop_first_count": stop_first_count,
        # 6~9. ranking 비교
        "earliest_first_selected": sorted(ef_syms),
        "composite_selected": sorted(comp_syms),
        "ranking_differs": ef_syms != comp_syms,
        "rejected_avg_score": ranked.rejected_avg_score,
        "selected_avg_score": ranked.selected_avg_score,
        "vetoed_count": ranked.vetoed_count,
        "ranking_detail": ranked.to_dict(),
        # 10. 비용
        "cost_model": {"commission_bps": cost.commission_bps, "tax_bps": cost.tax_bps,
                       "slippage_bps": cost.slippage_bps},
        # 11~13. 해석 / 다음 단계
        "interpretation": [
            "1분봉 replay 가 가능한 거래에서 5분봉 동시터치 착시를 순서로 해소.",
            "composite ranking 이 선착순 대비 품질 높은 신호를 슬롯에 배정.",
            ("1분봉 subset 데이터가 부족하면 결론이 약함 — coverage WARN 확인."
             if coverage["status"] == "WARN" else
             "1분봉 coverage 존재 — replay 신뢰도 상대적으로 높음."),
        ],
        "next_steps": [
            "data/market/robust_intraday_1m_subset/ 에 실제 1분봉 확보 → replay 비율↑",
            "실 데이터로 earliest-first vs composite PF/MDD/return 비교",
            "ranking weight 는 별도 검증 후에만 조정 (자동 적용 금지)",
        ],
        "disclaimer": "이 결과는 연구용 백테스트이며 실전매매 권고가 아닙니다. 수익을 보장하지 않습니다.",
        # 안전 불변.
        "is_live_authorization": False,
        "is_order_signal": False,
        "auto_apply_allowed": False,
        "no_profit_guarantee": True,
        "contains_secret": False,
    }
