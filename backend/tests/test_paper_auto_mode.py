"""Paper Auto Loop V2 모드 분류 테스트 (CONNECT-KIS-REALTIME-...-V2 §1)."""

from __future__ import annotations

from types import SimpleNamespace

from app.auto_paper.paper_auto_mode import (
    PaperAutoMode,
    resolve_paper_auto_mode,
)


def _s(**kw):
    base = dict(
        market_data_provider="mock",
        enable_kis_paper_auto_trading=False,
        enable_live_trading=False,
        kis_paper_auto_order_dry_run=True,
        kis_paper_smoke_mode=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_mock_provider_is_virtual_only_even_if_auto_enabled():
    r = resolve_paper_auto_mode(_s(market_data_provider="mock",
                                   enable_kis_paper_auto_trading=True,
                                   kis_paper_auto_order_dry_run=False))
    assert r.mode == PaperAutoMode.VIRTUAL_ONLY.value
    assert r.broker_order_enabled is False
    assert r.kis_realtime is False


def test_kis_dryrun_mode():
    r = resolve_paper_auto_mode(_s(market_data_provider="kis",
                                   enable_kis_paper_auto_trading=True,
                                   kis_paper_auto_order_dry_run=True))
    assert r.mode == PaperAutoMode.KIS_REALTIME_DRYRUN.value
    assert r.broker_order_enabled is False
    assert r.kis_realtime is True
    assert r.price_source == "kis"


def test_kis_paper_auto_mode():
    r = resolve_paper_auto_mode(_s(market_data_provider="kis",
                                   enable_kis_paper_auto_trading=True,
                                   kis_paper_auto_order_dry_run=False,
                                   kis_paper_smoke_mode=False))
    assert r.mode == PaperAutoMode.KIS_REALTIME_PAPER_AUTO.value
    assert r.broker_order_enabled is True
    assert r.kis_realtime is True


def test_kis_smoke_mode():
    r = resolve_paper_auto_mode(_s(market_data_provider="kis",
                                   enable_kis_paper_auto_trading=True,
                                   kis_paper_auto_order_dry_run=False,
                                   kis_paper_smoke_mode=True))
    assert r.mode == PaperAutoMode.KIS_REALTIME_SMOKE_TEST.value
    assert r.broker_order_enabled is True
    assert r.smoke_mode is True


def test_live_trading_forces_virtual_only():
    r = resolve_paper_auto_mode(_s(market_data_provider="kis",
                                   enable_kis_paper_auto_trading=True,
                                   enable_live_trading=True,
                                   kis_paper_auto_order_dry_run=False))
    assert r.mode == PaperAutoMode.VIRTUAL_ONLY.value
    assert r.broker_order_enabled is False


def test_auto_disabled_is_virtual_only():
    r = resolve_paper_auto_mode(_s(market_data_provider="kis",
                                   enable_kis_paper_auto_trading=False))
    assert r.mode == PaperAutoMode.VIRTUAL_ONLY.value


def test_invariants_always_false():
    for prov in ("mock", "yfinance", "kis"):
        r = resolve_paper_auto_mode(_s(market_data_provider=prov,
                                       enable_kis_paper_auto_trading=True,
                                       kis_paper_auto_order_dry_run=False))
        d = r.to_dict()
        assert d["is_live_authorization"] is False
        assert d["is_order_signal"] is False
        assert d["broker_order_type"] == "KIS_PAPER"
