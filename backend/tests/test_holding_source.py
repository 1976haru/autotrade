"""설계 B 조각 1 — BOT/MANUAL 보유 출처 분류 (read-only 집계) 테스트."""
from __future__ import annotations

from types import SimpleNamespace

from app.positions.holding_source import (
    SOURCE_BOT, SOURCE_MANUAL, SOURCE_MIXED, SOURCE_UNTAGGED,
    _net_by_source, classify,
)


def _row(symbol, side, qty, reason, status="FILLED"):
    return SimpleNamespace(symbol=symbol, side=side, quantity=qty,
                           trade_reason=reason, broker_status=status, executed=True)


def test_net_by_source_separates_bot_and_manual():
    rows = [
        _row("005930", "BUY", 10, "kis_paper_auto"),   # 봇 +10
        _row("005930", "SELL", 4, "kis_paper_auto"),   # 봇 -4 → bot_net 6
        _row("005930", "BUY", 20, "manual_buy"),       # 수동 +20 → manual_net 20
        _row("000660", "BUY", 5, "manual_buy"),
        _row("000660", "SELL", 5, "manual_sell_all"),  # 수동 net 0
    ]
    net = _net_by_source(rows)
    assert net["005930"] == {"bot_net": 6, "manual_net": 20}
    assert net["000660"] == {"bot_net": 0, "manual_net": 0}


def test_net_excludes_rejected():
    rows = [
        _row("005930", "BUY", 10, "manual_buy"),
        _row("005930", "BUY", 99, "manual_buy", status="REJECTED"),  # 제외
    ]
    assert _net_by_source(rows)["005930"]["manual_net"] == 10


def test_classify_mixed_bot_manual():
    c = classify(bot_net=60, manual_net=40, kis_qty=100)
    assert c["source"] == SOURCE_MIXED and c["bot_qty"] == 60 and c["manual_qty"] == 40


def test_classify_pure_bot_and_manual():
    assert classify(bot_net=30, manual_net=0, kis_qty=30)["source"] == SOURCE_BOT
    assert classify(bot_net=0, manual_net=40, kis_qty=40)["source"] == SOURCE_MANUAL


def test_untagged_is_treated_as_manual_side_conservative():
    # ★출처 미상(외부거래) → 봇이 안 건드리게 manual_qty 로(손실방어 안전측).
    c = classify(bot_net=0, manual_net=0, kis_qty=15)
    assert c["source"] == SOURCE_UNTAGGED
    assert c["manual_qty"] == 15 and c["bot_qty"] == 0
    assert c["bot_isolated"] is False   # 조각 2 전 — 격리 미작동 표시


def test_mixed_untagged_remainder_goes_to_manual():
    # 봇 50 + 수동 30, KIS 100 → untagged 20 은 manual 측(80) 으로.
    c = classify(bot_net=50, manual_net=30, kis_qty=100)
    assert c["bot_qty"] == 50 and c["manual_qty"] == 50 and c["untagged_qty"] == 20
