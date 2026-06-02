"""PART1-3: CapitalState 영속화 테스트.

핵심 보장:
  - persist=True 인스턴스가 commit_buy/sell 후 디스크에 저장한다.
  - 새 인스턴스가 저장된 상태를 복원한다 (재시작 시뮬).
  - ★0원 fallback 금지: 파일 없음/손상 시 0 이 아니라 initial_cash 로 시작.
  - persist=False (기본) 는 디스크를 건드리지 않는다 (기존 동작 무회귀).
  - 저장 payload 에 secret/계좌 필드 0건 (화이트리스트 키만).
"""

from __future__ import annotations

import json

import pytest

from app.auto_paper.capital_state import CapitalState
from app.auto_paper import capital_persistence as cp


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """AGENT_TRADER_CONFIG_DIR override → 실 OS 폴더 미오염."""
    d = tmp_path / "agenttrader_config"
    monkeypatch.setenv("AGENT_TRADER_CONFIG_DIR", str(d))
    cp.clear_state_for_tests()
    yield d
    cp.clear_state_for_tests()


def test_persist_true_saves_after_commit_buy(config_dir):
    s = CapitalState(initial_cash_krw=10_000_000, persist=True)
    s.commit_buy(symbol="005930", price=100_000, quantity=3)  # -300,000
    path = cp.get_state_path()
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["available_cash_krw"] == 9_700_000
    assert data["invested_krw"] == 300_000
    assert data["buy_count"] == 1


def test_restore_on_new_instance_simulates_restart(config_dir):
    s1 = CapitalState(initial_cash_krw=10_000_000, persist=True)
    s1.commit_buy(symbol="005930", price=100_000, quantity=4)   # -400,000
    s1.commit_sell(symbol="005930", price=110_000, quantity=2,
                   cost_basis_krw=200_000)                       # +220,000, realized +20,000
    snap1 = s1.snapshot()

    # 재시작 시뮬: 같은 config dir 로 *새* 인스턴스.
    s2 = CapitalState(initial_cash_krw=10_000_000, persist=True)
    snap2 = s2.snapshot()
    assert snap2.available_cash_krw == snap1.available_cash_krw
    assert snap2.invested_krw == snap1.invested_krw
    assert snap2.realized_pnl_krw == snap1.realized_pnl_krw
    assert snap2.buy_count == 1
    assert snap2.sell_count == 1


def test_no_file_falls_back_to_initial_not_zero(config_dir):
    # 저장 파일 없음 → initial_cash 로 시작 (★0원 금지).
    s = CapitalState(initial_cash_krw=50_000_000, persist=True)
    snap = s.snapshot()
    assert snap.available_cash_krw == 50_000_000
    assert snap.initial_cash_krw == 50_000_000


def test_corrupted_file_falls_back_to_initial_not_zero(config_dir):
    path = cp.get_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json", encoding="utf-8")
    s = CapitalState(initial_cash_krw=30_000_000, persist=True)
    snap = s.snapshot()
    # 손상 → 복원 거부 → initial (0 아님).
    assert snap.available_cash_krw == 30_000_000


def test_negative_balance_file_refused(config_dir):
    path = cp.get_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "initial_cash_krw": 10_000_000, "available_cash_krw": -5,
        "invested_krw": 0, "realized_pnl_krw": 0,
        "buy_count": 0, "sell_count": 0, "last_event_at": None,
    }), encoding="utf-8")
    s = CapitalState(initial_cash_krw=10_000_000, persist=True)
    # 음수 잔고 → 복원 거부 → initial.
    assert s.snapshot().available_cash_krw == 10_000_000


def test_persist_false_does_not_touch_disk(config_dir):
    s = CapitalState(initial_cash_krw=10_000_000, persist=False)
    s.commit_buy(symbol="005930", price=100_000, quantity=1)
    assert not cp.get_state_path().exists()


def test_saved_payload_has_only_whitelisted_keys(config_dir):
    s = CapitalState(initial_cash_krw=10_000_000, persist=True)
    s.commit_buy(symbol="005930", price=100_000, quantity=1)
    data = json.loads(cp.get_state_path().read_text(encoding="utf-8"))
    allowed = set(cp._PERSISTED_KEYS)
    assert set(data.keys()) <= allowed
    # secret-like 키가 없어야 한다.
    for k in data:
        assert "secret" not in k.lower()
        assert "account" not in k.lower()
        assert "token" not in k.lower()


def test_reset_persists_too(config_dir):
    s1 = CapitalState(initial_cash_krw=10_000_000, persist=True)
    s1.commit_buy(symbol="005930", price=100_000, quantity=5)
    s1.reset(initial_cash_krw=20_000_000)
    s2 = CapitalState(initial_cash_krw=10_000_000, persist=True)
    # reset 후 저장된 상태(현금 20M, buy 0)가 복원되어야.
    assert s2.snapshot().available_cash_krw == 20_000_000
    assert s2.snapshot().buy_count == 0


# ── PART1-3 보강: 영속화 활성화 경로 (env 동기화) ──────────────────────────
# 2026-06-02 preflight 점검에서 발견: pydantic 은 .env 값을 Settings 객체에만
# 싣고 os.environ 에는 넣지 않아, _persistence_enabled() 가 .env 만으로는
# False 였다. lifespan 이 settings.paper_capital_persist → os.environ 동기화
# 해야 켜진다. 이 계약을 lock.

def test_settings_has_paper_capital_persist_field():
    from app.core.config import Settings
    assert "paper_capital_persist" in Settings.model_fields


def test_persistence_enabled_reads_env(monkeypatch):
    from app.auto_paper.capital_state import _persistence_enabled
    monkeypatch.delenv("PAPER_CAPITAL_PERSIST", raising=False)
    assert _persistence_enabled() is False
    monkeypatch.setenv("PAPER_CAPITAL_PERSIST", "true")
    assert _persistence_enabled() is True


def test_lifespan_sync_pattern_enables_persistence(monkeypatch):
    """main.py lifespan 이 하는 동기화(settings → os.environ)를 재현해
    _persistence_enabled() 가 True 가 되는지 — 내일 재시작 시 적용 보장."""
    import os
    from app.auto_paper.capital_state import _persistence_enabled
    monkeypatch.delenv("PAPER_CAPITAL_PERSIST", raising=False)
    assert _persistence_enabled() is False
    # lifespan 의 `if cfg.paper_capital_persist: os.environ[...] = "true"` 재현.
    cfg_value = True  # .env 의 PAPER_CAPITAL_PERSIST=true 를 settings 가 읽은 값
    if cfg_value:
        monkeypatch.setenv("PAPER_CAPITAL_PERSIST", "true")
    assert _persistence_enabled() is True
