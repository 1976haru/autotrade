"""KIS-PAPER-FULL-LIFECYCLE-V1 (A2): KIS 모의 계좌 잔고 fetcher 와이어링 테스트.

대상: ``GET /api/auto-paper/portfolio-source``. 자격(presence) 이 있을 때 *KIS 모의*
broker 의 read-only get_balance / get_positions 가 호출되어 snapshot 이 NOT_CONFIGURED
→ OK 로 전환되는지 검증한다.

안전 invariants (테스트로 lock):
- live(is_paper=False) broker 는 호출 0건(safety guard) — fetcher=None 유지.
- broker 예외는 흡수 → fetcher=None → snapshot 이 0원으로 안 채워짐.
- 응답에 KIS App Key / Secret / 계좌번호 원문 0건.
- broker.place_order 호출 0건 (잔고 조회만).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app


@dataclass
class _Bal:
    cash: int
    equity: int
    buying_power: int = 0
    currency: str = "KRW"


@dataclass
class _Pos:
    symbol: str
    quantity: int
    avg_price: int
    market_price: int


class _FakeKisPaperBroker:
    """KisBrokerAdapter 흉내 — is_paper=True 한정."""

    __class__name__ = "KisBrokerAdapter"  # for any class-name probes
    is_paper = True
    place_order_called = False  # invariant lock

    def __init__(self, *, cash=10_000_000, equity=10_500_000, positions=None):
        self._cash = cash
        self._equity = equity
        self._positions = positions or []

    async def get_balance(self) -> _Bal:
        return _Bal(cash=self._cash, equity=self._equity, buying_power=self._cash)

    async def get_positions(self) -> list[_Pos]:
        return list(self._positions)

    async def place_order(self, *a, **kw):  # noqa: D401
        type(self).place_order_called = True
        raise AssertionError("place_order MUST NOT be called by portfolio-source")


# 'KisBrokerAdapter' 이름 매칭이 type(b).__name__ 으로 일어남 → 클래스명 자체를 맞춰야 함.
_FakeKisPaperBroker.__name__ = "KisBrokerAdapter"


class _FakeLiveBroker:
    """is_paper=False → fetcher 가 *호출되지 말아야* 함 (safety guard)."""

    is_paper = False
    called = False

    async def get_balance(self):  # noqa: D401
        type(self).called = True
        raise AssertionError("live broker MUST NOT be called by portfolio-source")

    async def get_positions(self):
        type(self).called = True
        raise AssertionError("live broker MUST NOT be called by portfolio-source")


_FakeLiveBroker.__name__ = "KisBrokerAdapter"   # name match but is_paper=False


class _FakeNotKisBroker:
    """KisBrokerAdapter 가 아닌 broker — 호출 안 됨 (e.g. Mock)."""

    is_paper = True   # paper 이지만 class name 다름

    async def get_balance(self):
        raise AssertionError("non-Kis broker MUST NOT be called")


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _override_broker(broker_obj, monkeypatch):
    """route 가 함수 내부에서 ``from app.api.deps import get_broker`` 하므로 source
    모듈 자체를 monkeypatch 한다 (FastAPI Depends override 가 안 통함)."""
    monkeypatch.setattr("app.api.deps.get_broker", lambda: broker_obj)


# ─────────── A2 main: 자격 + KIS paper broker → OK ───────────


def test_kis_paper_balance_wired_when_credentials_and_paper_broker(client, monkeypatch):
    """fetcher 가 주입되어 KIS snapshot 이 NOT_CONFIGURED 가 아니게 됨."""
    # readiness 가 credentials_present=True 라고 가정.
    import app.kis_paper.readiness as _r
    monkeypatch.setattr(_r, "evaluate_readiness",
                        lambda s: type("R", (), {"credentials_present": True})())
    broker = _FakeKisPaperBroker(
        cash=12_345_678, equity=15_000_000,
        positions=[_Pos("005930", 10, 70_000, 71_500),
                   _Pos("035420", 5, 200_000, 205_000)],
    )
    _override_broker(broker, monkeypatch)
    r = client.get("/api/auto-paper/portfolio-source")
    assert r.status_code == 200
    d = r.json()
    kis = d.get("kis_paper_account", {})
    assert kis.get("status") in ("OK",), f"expected OK, got {kis.get('status')!r}"
    assert kis.get("cash") == 12_345_678
    assert kis.get("total_asset") == 15_000_000
    assert kis.get("position_count") == 2
    syms = sorted(p.get("symbol") for p in kis.get("positions", []))
    assert syms == ["005930", "035420"]
    # 안전 invariant.
    assert d["is_live_authorization"] is False
    assert d["contains_secret"] is False
    assert _FakeKisPaperBroker.place_order_called is False


def test_kis_paper_balance_skipped_when_no_credentials(client, monkeypatch):
    """자격 없으면 fetcher 미주입 → CREDENTIALS_MISSING (NOT 0원)."""
    import app.kis_paper.readiness as _r
    monkeypatch.setattr(_r, "evaluate_readiness",
                        lambda s: type("R", (), {"credentials_present": False})())
    broker = _FakeKisPaperBroker()
    _override_broker(broker, monkeypatch)
    r = client.get("/api/auto-paper/portfolio-source")
    d = r.json()
    kis = d.get("kis_paper_account", {})
    assert kis.get("status") in ("CREDENTIALS_MISSING", "NOT_CONFIGURED")
    assert kis.get("cash") is None       # 0 아님 — 조회 실패는 None.
    assert kis.get("total_asset") is None


def test_kis_paper_balance_safety_guard_on_live_broker(client, monkeypatch):
    """is_paper=False broker 는 *호출되지 않아야* 함 → fetcher=None → NOT_CONFIGURED."""
    import app.kis_paper.readiness as _r
    monkeypatch.setattr(_r, "evaluate_readiness",
                        lambda s: type("R", (), {"credentials_present": True})())
    _FakeLiveBroker.called = False
    _override_broker(_FakeLiveBroker(), monkeypatch)
    r = client.get("/api/auto-paper/portfolio-source")
    assert r.status_code == 200
    assert _FakeLiveBroker.called is False, "live broker MUST NOT be called"
    kis = r.json().get("kis_paper_account", {})
    assert kis.get("status") in ("NOT_CONFIGURED", "API_UNAVAILABLE",
                                 "CREDENTIALS_MISSING")
    assert kis.get("cash") is None


def test_kis_paper_balance_exception_is_absorbed(client, monkeypatch):
    """broker.get_balance 예외 → fetcher=None → API_UNAVAILABLE/NOT_CONFIGURED (0원 아님)."""
    import app.kis_paper.readiness as _r
    monkeypatch.setattr(_r, "evaluate_readiness",
                        lambda s: type("R", (), {"credentials_present": True})())

    class _Boom(_FakeKisPaperBroker):
        async def get_balance(self):
            raise RuntimeError("simulated KIS timeout")

    _Boom.__name__ = "KisBrokerAdapter"
    _override_broker(_Boom(), monkeypatch)
    r = client.get("/api/auto-paper/portfolio-source")
    assert r.status_code == 200
    kis = r.json().get("kis_paper_account", {})
    assert kis.get("cash") is None       # 0 fallback 금지
    assert kis.get("status") in ("NOT_CONFIGURED", "API_UNAVAILABLE")


# ─────────── 안전: secret 원문 노출 0건 ───────────


def test_response_has_no_secret_strings(client, monkeypatch):
    import re
    import app.kis_paper.readiness as _r
    monkeypatch.setattr(_r, "evaluate_readiness",
                        lambda s: type("R", (), {"credentials_present": True})())
    _override_broker(_FakeKisPaperBroker(), monkeypatch)
    r = client.get("/api/auto-paper/portfolio-source")
    txt = r.text
    # KIS App Key/Secret 패턴 (대문자 + 숫자 30+자) / 한국 계좌번호(8-2) / Bearer.
    assert not re.search(r"\b[A-Z0-9]{30,}\b", txt), "looks like a KIS app key"
    assert not re.search(r"\b\d{8}-\d{2}\b", txt), "looks like an account number"
    assert "Bearer " not in txt
    assert "appsecret" not in txt.lower() or '"appsecret":' not in txt.lower()
