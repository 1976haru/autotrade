"""ThemeFilter 단위 테스트 — BUY/SELL/HOLD 미반환 invariant 포함 (#22)."""

import inspect

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import ThemeSignal
from app.themes.filter import (
    CandidateSymbol,
    ThemeFilter,
    list_recent_signals,
    signals_summary,
)
from app.themes.providers import ManualProvider, MockThemeProvider, ThemeRecord


def _session_factory():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


# ---------- candidate_symbols ----------


def test_filter_returns_candidate_symbols_only():
    """ThemeFilter는 candidate symbol 리스트만 반환 — BUY/SELL 결정 없음."""
    filt = ThemeFilter(MockThemeProvider())
    cands = filt.candidate_symbols()
    for c in cands:
        assert isinstance(c, CandidateSymbol)
        # candidate에는 'side' / 'order_type' / 'BUY' 같은 주문 필드가 없다.
        keys = c.to_dict().keys()
        assert "side" not in keys
        assert "order_type" not in keys


def test_filter_respects_universe():
    filt = ThemeFilter(MockThemeProvider())
    cands = filt.candidate_symbols(universe=["005930"])
    for c in cands:
        assert c.symbol == "005930"


def test_filter_excludes_weak_and_ignore_grades_by_default():
    """STRONG / WATCH 만 candidate 후보 — WEAK / IGNORE 종목은 빠진다."""
    filt = ThemeFilter(MockThemeProvider())
    cands = filt.candidate_symbols()
    for c in cands:
        assert c.best_grade in {"STRONG", "WATCH"}


def test_filter_empty_universe_returns_empty_candidates():
    filt = ThemeFilter(MockThemeProvider())
    assert filt.candidate_symbols(universe=[]) == []
    assert filt.scan(universe=[]) == []


def test_filter_disabled_provider_returns_empty():
    """alpha 같이 disabled provider면 빈 결과."""
    class _Disabled(MockThemeProvider):
        def is_enabled(self):
            return False
    filt = ThemeFilter(_Disabled())
    assert filt.scan() == []
    assert filt.candidate_symbols() == []


def test_filter_aggregates_same_symbol_across_themes():
    """한 종목이 여러 테마에 등장하면 themes 리스트에 모이고 best_score 채택."""
    rec_a = ThemeRecord(theme="A", keywords=["k"], related_symbols=["005930"],
                        score=85, grade="STRONG", confidence=80,
                        source="trends", provider="manual")
    rec_b = ThemeRecord(theme="B", keywords=["k"], related_symbols=["005930"],
                        score=70, grade="WATCH", confidence=70,
                        source="news", provider="manual")
    filt = ThemeFilter(ManualProvider([rec_a, rec_b]))

    cands = filt.candidate_symbols()
    assert len(cands) == 1
    assert cands[0].symbol == "005930"
    assert set(cands[0].themes) == {"A", "B"}
    assert cands[0].best_score == 85
    assert cands[0].best_grade == "STRONG"


# ---------- BUY/SELL/HOLD 미반환 invariant ----------


def test_filter_class_does_not_expose_buy_sell_hold_methods():
    """class 표면에 BUY/SELL/HOLD 결정 메서드가 노출되지 않는지 확인."""
    public = [n for n in dir(ThemeFilter) if not n.startswith("_")]
    forbidden = {"buy", "sell", "hold", "decide_order", "place_order",
                 "submit_order", "make_order", "to_order"}
    intersection = forbidden & {n.lower() for n in public}
    assert intersection == set(), f"forbidden order-decision API: {intersection}"


def test_filter_source_does_not_import_broker_or_risk():
    """본 모듈은 broker / RiskManager / PermissionGate / OrderExecutor를
    import하지 않는다 — 코드 단 invariant 검증."""
    import app.themes.filter as mod
    src = inspect.getsource(mod)
    assert "from app.brokers" not in src
    assert "from app.risk" not in src
    assert "from app.permission" not in src
    assert "from app.execution" not in src


def test_provider_module_does_not_import_broker_or_risk():
    import app.themes.providers as mod
    src = inspect.getsource(mod)
    assert "from app.brokers" not in src
    assert "from app.risk" not in src
    assert "from app.permission" not in src
    assert "from app.execution" not in src


# ---------- persist + list_recent_signals + summary ----------


def test_persist_writes_used_for_order_false():
    Session = _session_factory()
    with Session() as db:
        records = MockThemeProvider().scan()
        filt = ThemeFilter(MockThemeProvider())
        n = filt.persist(db, records)
        db.commit()

        rows = db.execute(select(ThemeSignal)).scalars().all()
        assert n == len(records) > 0
        assert all(r.used_for_order is False for r in rows)


def test_list_recent_signals_filters_by_grade():
    Session = _session_factory()
    with Session() as db:
        ThemeFilter(MockThemeProvider()).persist(db, MockThemeProvider().scan())
        db.commit()

        strong = list_recent_signals(db, limit=50, grade="STRONG")
        for r in strong:
            assert r.grade == "STRONG"


def test_signals_summary_invariant_used_for_order_false():
    Session = _session_factory()
    with Session() as db:
        ThemeFilter(MockThemeProvider()).persist(db, MockThemeProvider().scan())
        db.commit()

        out = signals_summary(db)
    assert out["used_for_order"] is False
    assert "by_grade" in out
    assert "top_themes" in out
