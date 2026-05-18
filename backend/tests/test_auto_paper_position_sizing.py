"""#4-08: Tests for `app.auto_paper.position_sizer`.

Covers:
* PolicyValidation — invalid threshold raises.
* InputValidation — invalid confidence / risk_flag_count raises.
* Result invariants — is_order_signal / auto_apply / live_authz must be False,
  quantity >= 0.
* Cap priority — EMERGENCY_STOP / UNKNOWN / low_confidence / risk_flags /
  invalid price / invalid equity → quantity=0.
* Multipliers — confidence high → larger size, confidence low → smaller,
  risk_flag rises → smaller, stop_loss rises → smaller, max_position_pct /
  max_position_krw cap engaged when notional exceeds them.
* Regime sensitivity — LOW_LIQUIDITY / HIGH_VOLATILITY / TREND_DOWN / CHOPPY
  reduce vs TREND_UP baseline; UNKNOWN ≡ blocked.
* Static guard — module imports no broker / OrderExecutor / route_order /
  AI SDK / external HTTP / DB write surface.
* No secret fields — SizingInput / Result schema has no api_key / secret /
  account_number column.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.auto_paper.position_sizer import (
    PositionSizingPolicy,
    SizingInput,
    SizingResult,
    SizingVerdict,
    compute_position_size,
)


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "auto_paper" / "position_sizer.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# Policy + Input validation
# ─────────────────────────────────────────────────────────────────────────────


class TestPolicyValidation:

    def test_default_policy_valid(self):
        pol = PositionSizingPolicy()
        assert pol.max_risk_per_trade_pct == 0.01
        assert pol.default_stop_loss_pct == 0.03
        assert pol.max_position_pct == 0.20

    @pytest.mark.parametrize("kwargs", [
        {"max_risk_per_trade_pct": 0.0},
        {"max_risk_per_trade_pct": -0.1},
        {"max_risk_per_trade_pct": 1.1},
        {"default_stop_loss_pct": 0.0},
        {"default_stop_loss_pct": 1.5},
        {"max_position_pct": 0.0},
        {"max_position_krw": 0},
        {"max_position_krw": -10},
        {"min_confidence_threshold": -0.01},
        {"min_confidence_threshold": 1.01},
        {"max_risk_flags": -1},
        {"min_unit_quantity": 0},
    ])
    def test_invalid_policy_raises(self, kwargs):
        with pytest.raises(ValueError):
            PositionSizingPolicy(**kwargs)


class TestInputValidation:

    def test_minimal_valid_input(self):
        inp = SizingInput(
            strategy="sma_crossover", symbol="005930",
            price=70_000.0, account_equity=10_000_000.0,
        )
        assert inp.confidence == 0.5
        assert inp.market_regime == "UNKNOWN"

    @pytest.mark.parametrize("kwargs", [
        {"strategy": ""},
        {"symbol": ""},
        {"confidence": -0.1},
        {"confidence": 1.1},
        {"risk_flag_count": -1},
        {"stop_loss_pct": 0.0},
        {"stop_loss_pct": 1.5},
    ])
    def test_invalid_input_raises(self, kwargs):
        base = dict(
            strategy="s", symbol="x",
            price=100.0, account_equity=1_000_000.0,
        )
        base.update(kwargs)
        with pytest.raises(ValueError):
            SizingInput(**base)


# ─────────────────────────────────────────────────────────────────────────────
# Result invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestResultInvariants:

    def test_result_default_invariants(self):
        r = SizingResult(
            strategy="a", symbol="b",
            verdict=SizingVerdict.SIZED,
            quantity=1, notional_krw=100.0, risk_krw=3.0, multiplier=1.0,
        )
        assert r.is_order_signal is False
        assert r.auto_apply_allowed is False
        assert r.is_live_authorization is False

    @pytest.mark.parametrize("override", [
        {"is_order_signal": True},
        {"auto_apply_allowed": True},
        {"is_live_authorization": True},
    ])
    def test_result_invariant_violation_raises(self, override):
        base = dict(
            strategy="a", symbol="b",
            verdict=SizingVerdict.SIZED,
            quantity=1, notional_krw=100.0, risk_krw=3.0, multiplier=1.0,
        )
        base.update(override)
        with pytest.raises(ValueError):
            SizingResult(**base)

    def test_result_negative_quantity_raises(self):
        with pytest.raises(ValueError):
            SizingResult(
                strategy="a", symbol="b",
                verdict=SizingVerdict.SIZED,
                quantity=-1, notional_krw=0.0, risk_krw=0.0, multiplier=0.0,
            )

    def test_result_verdict_must_be_enum(self):
        with pytest.raises(ValueError):
            SizingResult(
                strategy="a", symbol="b",
                verdict="SIZED",  # type: ignore[arg-type]
                quantity=0, notional_krw=0.0, risk_krw=0.0, multiplier=0.0,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Cap priority — blocking conditions
# ─────────────────────────────────────────────────────────────────────────────


def _inp(**overrides):
    base = dict(
        strategy="sma_crossover", symbol="005930",
        price=70_000.0, account_equity=10_000_000.0,
        confidence=0.8, risk_flag_count=0,
        market_regime="TREND_UP", loop_state="RUNNING",
    )
    base.update(overrides)
    return SizingInput(**base)


class TestBlocking:

    def test_emergency_stop_blocks_all(self):
        r = compute_position_size(_inp(loop_state="EMERGENCY_STOP"))
        assert r.quantity == 0
        assert r.verdict == SizingVerdict.BLOCKED_EMERGENCY
        assert r.notional_krw == 0.0
        assert any("EMERGENCY_STOP" in s for s in r.reasons)

    def test_unknown_regime_blocks(self):
        r = compute_position_size(_inp(market_regime="UNKNOWN"))
        assert r.quantity == 0
        assert r.verdict == SizingVerdict.BLOCKED_UNKNOWN

    def test_low_confidence_blocks(self):
        r = compute_position_size(_inp(confidence=0.30))
        assert r.quantity == 0
        assert r.verdict == SizingVerdict.BLOCKED_LOW_CONFIDENCE

    def test_too_many_risk_flags_blocks(self):
        # default max_risk_flags=3 → flag_count >= 3 blocks.
        r = compute_position_size(_inp(risk_flag_count=3))
        assert r.quantity == 0
        assert r.verdict == SizingVerdict.BLOCKED_RISK_FLAGS

    def test_invalid_price_blocks(self):
        r = compute_position_size(_inp(price=0.0))
        assert r.quantity == 0
        assert r.verdict == SizingVerdict.INSUFFICIENT_DATA

    def test_invalid_equity_blocks(self):
        r = compute_position_size(_inp(account_equity=0.0))
        assert r.quantity == 0
        assert r.verdict == SizingVerdict.INSUFFICIENT_DATA

    def test_priority_emergency_before_low_confidence(self):
        # both EMERGENCY_STOP + low confidence — emergency must dominate.
        r = compute_position_size(
            _inp(loop_state="EMERGENCY_STOP", confidence=0.1),
        )
        assert r.verdict == SizingVerdict.BLOCKED_EMERGENCY


# ─────────────────────────────────────────────────────────────────────────────
# Multiplier behaviour
# ─────────────────────────────────────────────────────────────────────────────


# Permissive policy that effectively disables the position caps so that the
# multiplier behaviour is observable end-to-end (default policy clamps risk-
# scaled quantity at 20% of equity / 5M KRW, which would otherwise flatten
# small differences between confidence levels).
_NO_CAP_POLICY = PositionSizingPolicy(
    max_risk_per_trade_pct=0.01,
    default_stop_loss_pct=0.03,
    max_position_pct=1.0,
    max_position_krw=10_000_000_000,
)


class TestMultipliers:

    def test_high_confidence_larger_than_mid(self):
        high = compute_position_size(_inp(confidence=0.95), _NO_CAP_POLICY)
        mid  = compute_position_size(_inp(confidence=0.55), _NO_CAP_POLICY)
        assert high.quantity > mid.quantity > 0

    def test_risk_flags_reduces_quantity(self):
        zero_flag = compute_position_size(_inp(risk_flag_count=0), _NO_CAP_POLICY)
        one_flag  = compute_position_size(_inp(risk_flag_count=1), _NO_CAP_POLICY)
        two_flag  = compute_position_size(_inp(risk_flag_count=2), _NO_CAP_POLICY)
        assert zero_flag.quantity > one_flag.quantity > two_flag.quantity
        assert two_flag.quantity >= 0

    def test_stop_loss_larger_reduces_quantity(self):
        small_sl = compute_position_size(_inp(stop_loss_pct=0.01), _NO_CAP_POLICY)
        large_sl = compute_position_size(_inp(stop_loss_pct=0.10), _NO_CAP_POLICY)
        assert small_sl.quantity > large_sl.quantity

    def test_regime_trend_up_larger_than_trend_down(self):
        up   = compute_position_size(_inp(market_regime="TREND_UP"), _NO_CAP_POLICY)
        down = compute_position_size(_inp(market_regime="TREND_DOWN"), _NO_CAP_POLICY)
        assert up.quantity >= down.quantity
        # down should be ~ 0.5x of up (1.0 vs 0.5 regime multiplier)
        assert down.quantity < up.quantity

    def test_low_liquidity_reduces(self):
        baseline = compute_position_size(_inp(market_regime="TREND_UP"), _NO_CAP_POLICY)
        liq      = compute_position_size(_inp(market_regime="LOW_LIQUIDITY"), _NO_CAP_POLICY)
        assert liq.quantity < baseline.quantity

    def test_high_volatility_reduces(self):
        baseline = compute_position_size(_inp(market_regime="TREND_UP"), _NO_CAP_POLICY)
        hivol    = compute_position_size(_inp(market_regime="HIGH_VOLATILITY"), _NO_CAP_POLICY)
        assert hivol.quantity < baseline.quantity

    def test_choppy_reduces(self):
        baseline = compute_position_size(_inp(market_regime="TREND_UP"), _NO_CAP_POLICY)
        chop     = compute_position_size(_inp(market_regime="CHOPPY"), _NO_CAP_POLICY)
        assert chop.quantity < baseline.quantity


# ─────────────────────────────────────────────────────────────────────────────
# Cap engagement
# ─────────────────────────────────────────────────────────────────────────────


class TestCaps:

    def test_max_position_pct_cap_engaged(self):
        # very large equity + small price + huge risk_per_trade → uncapped
        # base_quantity blows past 20% cap.
        pol = PositionSizingPolicy(
            max_risk_per_trade_pct=1.0,    # 100% of equity at risk
            default_stop_loss_pct=0.01,    # tiny stop → huge raw qty
            max_position_pct=0.10,         # 10% cap
            max_position_krw=10_000_000_000,  # disable krw cap
        )
        r = compute_position_size(
            SizingInput(
                strategy="s", symbol="x",
                price=1_000.0, account_equity=100_000_000.0,
                confidence=0.95, market_regime="TREND_UP",
                loop_state="RUNNING",
            ),
            pol,
        )
        # 10% cap → 100,000,000 * 0.10 / 1000 = 10000 shares max.
        assert r.quantity <= 10_000
        assert any("cap_applied" in s for s in r.reasons)

    def test_max_position_krw_cap_engaged(self):
        pol = PositionSizingPolicy(
            max_risk_per_trade_pct=1.0,
            default_stop_loss_pct=0.01,
            max_position_pct=1.0,   # disable pct cap
            max_position_krw=1_000_000,
        )
        r = compute_position_size(
            SizingInput(
                strategy="s", symbol="x",
                price=1_000.0, account_equity=100_000_000.0,
                confidence=0.95, market_regime="TREND_UP",
                loop_state="RUNNING",
            ),
            pol,
        )
        assert r.quantity <= 1_000  # 1,000,000 / 1,000
        assert any("cap_applied" in s for s in r.reasons)


# ─────────────────────────────────────────────────────────────────────────────
# Verdict / reasons
# ─────────────────────────────────────────────────────────────────────────────


class TestVerdictAndReasons:

    def test_sized_verdict_full_size(self):
        r = compute_position_size(_inp(
            confidence=0.95, risk_flag_count=0, market_regime="TREND_UP",
        ))
        assert r.verdict in (SizingVerdict.SIZED, SizingVerdict.REDUCED)
        assert r.quantity > 0

    def test_reduced_verdict_when_multiplier_less_than_1(self):
        r = compute_position_size(_inp(
            confidence=0.6, market_regime="SIDEWAYS",
        ))
        assert r.verdict == SizingVerdict.REDUCED
        assert r.quantity > 0
        assert r.multiplier < 1.0

    def test_reasons_carries_breakdown(self):
        r = compute_position_size(_inp())
        text = "\n".join(r.reasons)
        assert "base" in text
        assert "multiplier" in text

    def test_below_min_unit_falls_back_to_insufficient(self):
        # huge price + tiny risk → raw qty floors to 0.
        pol = PositionSizingPolicy(
            max_risk_per_trade_pct=0.001,    # 0.1% of equity
            default_stop_loss_pct=0.05,
            max_position_pct=0.20,
            max_position_krw=5_000_000,
        )
        r = compute_position_size(
            SizingInput(
                strategy="s", symbol="x",
                price=1_000_000.0, account_equity=1_000_000.0,
                confidence=0.95, market_regime="TREND_UP",
                loop_state="RUNNING",
            ),
            pol,
        )
        # base = 1,000,000 * 0.001 / (1,000,000 * 0.05) = 0.02 → floor 0
        assert r.quantity == 0
        assert r.verdict == SizingVerdict.INSUFFICIENT_DATA


# ─────────────────────────────────────────────────────────────────────────────
# Schema sanity — no secret fields, dict serialization works
# ─────────────────────────────────────────────────────────────────────────────


class TestSchemaSanity:

    def test_to_dict_round_trippable_keys(self):
        r = compute_position_size(_inp())
        d = r.to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False
        assert "verdict" in d and "verdict_label_ko" in d
        assert "policy_snapshot" in d and "input_snapshot" in d

    def test_no_secret_fields_in_input_or_result(self):
        forbidden = {
            "api_key", "app_key", "app_secret", "secret", "access_token",
            "kis_app_key", "kis_app_secret", "account_number",
            "anthropic_api_key", "openai_api_key",
        }
        for field_name in SizingInput.__dataclass_fields__:
            assert field_name.lower() not in forbidden, field_name
        for field_name in SizingResult.__dataclass_fields__:
            assert field_name.lower() not in forbidden, field_name

    def test_policy_to_dict_no_secret_fields(self):
        d = PositionSizingPolicy().to_dict()
        forbidden = {"api_key", "secret", "token", "account_number"}
        for k in d.keys():
            assert all(f not in k.lower() for f in forbidden), k


# ─────────────────────────────────────────────────────────────────────────────
# Static guard — module imports no broker / OrderExecutor / route_order
# ─────────────────────────────────────────────────────────────────────────────


_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "app.brokers.kis",
    "app.brokers.mock_broker",
    "app.execution.order_router",
    "app.execution.executor",
    "app.execution.order_executor",
    "app.permission.gate",
    "app.ai.assist",
    "app.ai.client",
    "anthropic",
    "openai",
    "httpx",
    "requests",
)

_FORBIDDEN_CALL_SUBSTRINGS = (
    "broker.place_order",
    "broker.cancel_order",
    "route_order(",
    "OrderExecutor",
    "OrderRequest",
)


class TestStaticGuards:

    def _source(self) -> str:
        return _MODULE_PATH.read_text(encoding="utf-8")

    def test_no_forbidden_imports(self):
        tree = ast.parse(self._source())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name or ""
                    for bad in _FORBIDDEN_IMPORT_SUBSTRINGS:
                        assert bad not in name, f"{name} contains forbidden {bad}"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for bad in _FORBIDDEN_IMPORT_SUBSTRINGS:
                    assert bad not in module, f"{module} contains forbidden {bad}"

    def test_no_forbidden_calls(self):
        src = self._source()
        # strip docstrings to avoid false positives.
        tree = ast.parse(src)
        # walk function bodies to inspect actual call expressions.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = ast.unparse(node.func)
                for bad in _FORBIDDEN_CALL_SUBSTRINGS:
                    assert bad not in callee, (
                        f"forbidden call '{bad}' found in: {callee}"
                    )

    def test_no_db_write_surface(self):
        src = self._source()
        for bad in ("session.commit", "session.add", "session.delete",
                    "db.commit(", "db.add(", "db.delete("):
            assert bad not in src, f"DB write surface '{bad}' detected"

    def test_no_settings_mutation(self):
        src = self._source()
        assert not re.search(r"settings\.enable_[a-z_]+\s*=", src), \
            "settings.enable_* mutation detected"
