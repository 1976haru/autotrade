"""#3-09: 실데이터 universe + symbol filter policy 테스트.

invariant:
- `UniverseKind` 5 종 lock — SAMPLE10 / LIQUIDITY_TOP50/100/300 / CUSTOM.
- `SymbolFilterPolicy` 8 필드 (`__post_init__` 범위 가드).
- `LIQUIDITY_TOP*` + liquidity_source None → `UniverseDataNotAvailableError`.
- `CUSTOM` + custom_symbols 없음 → ValueError.
- 필터 정책 적용 시 제외 사유별 카운트 정확.
- broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건 (정적 grep).
- schema 에 API key / 계좌번호 필드 0건.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.backtest.real_data.symbols import REPRESENTATIVE_SYMBOLS
from app.backtest.real_data.universe import (
    SAMPLE10_SIZE,
    SymbolFilterPolicy,
    SymbolMeta,
    UniverseDataNotAvailableError,
    UniverseKind,
    UniverseResolution,
    is_liquidity_kind,
    liquidity_top_n,
    resolve_universe,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. UniverseKind enum lock
# ─────────────────────────────────────────────────────────────────────────────


class TestUniverseKind:
    def test_five_kinds_present(self):
        values = {k.value for k in UniverseKind}
        assert values == {
            "sample10", "liquidity_top50", "liquidity_top100",
            "liquidity_top300", "custom",
        }

    def test_liquidity_kinds_mapped_to_top_n(self):
        assert liquidity_top_n(UniverseKind.LIQUIDITY_TOP50)  == 50
        assert liquidity_top_n(UniverseKind.LIQUIDITY_TOP100) == 100
        assert liquidity_top_n(UniverseKind.LIQUIDITY_TOP300) == 300
        assert liquidity_top_n(UniverseKind.SAMPLE10) is None
        assert liquidity_top_n(UniverseKind.CUSTOM)   is None

    def test_is_liquidity_kind(self):
        assert is_liquidity_kind(UniverseKind.LIQUIDITY_TOP50)
        assert is_liquidity_kind(UniverseKind.LIQUIDITY_TOP100)
        assert is_liquidity_kind(UniverseKind.LIQUIDITY_TOP300)
        assert not is_liquidity_kind(UniverseKind.SAMPLE10)
        assert not is_liquidity_kind(UniverseKind.CUSTOM)


# ─────────────────────────────────────────────────────────────────────────────
# 2. SymbolFilterPolicy invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestSymbolFilterPolicy:
    def test_default_values(self):
        p = SymbolFilterPolicy()
        assert p.min_avg_volume == 0
        assert p.min_avg_trading_value == 0
        assert p.exclude_suspended is True
        assert p.exclude_managed is True
        assert p.exclude_etf_etn is True
        assert p.exclude_spac is True
        assert p.min_listed_days == 180
        assert p.max_missing_ratio == 0.05

    def test_negative_volume_rejected(self):
        with pytest.raises(ValueError):
            SymbolFilterPolicy(min_avg_volume=-1)

    def test_negative_trading_value_rejected(self):
        with pytest.raises(ValueError):
            SymbolFilterPolicy(min_avg_trading_value=-100)

    def test_negative_listed_days_rejected(self):
        with pytest.raises(ValueError):
            SymbolFilterPolicy(min_listed_days=-5)

    def test_missing_ratio_range_check(self):
        SymbolFilterPolicy(max_missing_ratio=0.0)
        SymbolFilterPolicy(max_missing_ratio=1.0)
        with pytest.raises(ValueError):
            SymbolFilterPolicy(max_missing_ratio=1.5)
        with pytest.raises(ValueError):
            SymbolFilterPolicy(max_missing_ratio=-0.1)

    def test_to_dict_carries_all_fields(self):
        d = SymbolFilterPolicy().to_dict()
        required = {
            "min_avg_volume", "min_avg_trading_value",
            "exclude_suspended", "exclude_managed",
            "exclude_etf_etn", "exclude_spac",
            "min_listed_days", "max_missing_ratio",
        }
        assert set(d.keys()) == required


# ─────────────────────────────────────────────────────────────────────────────
# 3. resolve_universe — SAMPLE10 (즉시 사용 가능)
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveSample10:
    def test_sample10_returns_representative_symbols(self):
        r = resolve_universe(UniverseKind.SAMPLE10)
        assert r.symbol_count == SAMPLE10_SIZE == 10
        assert set(r.symbols) == {s.symbol for s in REPRESENTATIVE_SYMBOLS}
        # operator_note 에 "1차 기능 검증용" 명시.
        assert "1차" in (r.operator_note or "") or "SAMPLE10" in (r.operator_note or "")
        # advisory invariant.
        d = r.to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False

    def test_sample10_does_not_require_liquidity_source(self):
        # liquidity_source 없이도 동작 — sample10 은 hard-coded.
        r = resolve_universe(UniverseKind.SAMPLE10, liquidity_source=None)
        assert r.symbol_count == 10


# ─────────────────────────────────────────────────────────────────────────────
# 4. resolve_universe — CUSTOM
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveCustom:
    def test_custom_requires_symbols_list(self):
        with pytest.raises(ValueError):
            resolve_universe(UniverseKind.CUSTOM, custom_symbols=None)
        with pytest.raises(ValueError):
            resolve_universe(UniverseKind.CUSTOM, custom_symbols=[])

    def test_custom_accepts_valid_6digit_symbols(self):
        r = resolve_universe(
            UniverseKind.CUSTOM,
            custom_symbols=["005930", "000660", "035420"],
        )
        assert r.symbol_count == 3
        assert r.excluded_reasons == {}

    def test_custom_drops_invalid_format(self):
        r = resolve_universe(
            UniverseKind.CUSTOM,
            custom_symbols=["005930", "AAPL", "abc123", "000660"],
        )
        assert r.symbols == ["005930", "000660"]
        assert r.excluded_reasons.get("invalid_symbol_format") == 2


# ─────────────────────────────────────────────────────────────────────────────
# 5. resolve_universe — LIQUIDITY_TOP* (opt-in 정책)
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveLiquidity:
    @pytest.mark.parametrize("kind", [
        UniverseKind.LIQUIDITY_TOP50,
        UniverseKind.LIQUIDITY_TOP100,
        UniverseKind.LIQUIDITY_TOP300,
    ])
    def test_liquidity_without_source_raises(self, kind):
        """user spec: 실제 전체 종목 실행은 *별도 opt-in* — liquidity_source
        주입 없으면 에러."""
        with pytest.raises(UniverseDataNotAvailableError) as exc:
            resolve_universe(kind, liquidity_source=None)
        # 에러 메시지에 *별도 opt-in PR* 명시.
        assert "opt-in" in str(exc.value).lower() or "별도" in str(exc.value)

    def test_liquidity_with_source_applies_filter(self):
        """liquidity_source 주입 시 필터 적용 + top_n 매핑."""
        def fake_source(kind, top_n):
            return [
                SymbolMeta(symbol=f"00000{i}", avg_trading_value=10_000_000 - i,
                           listed_days=300, missing_ratio=0.0)
                for i in range(5)
            ] + [
                # 관리종목 1개 — exclude_managed=True default 로 제외.
                SymbolMeta(symbol="000010", is_managed=True),
                # 신규상장 1개 — min_listed_days=180 default 로 제외.
                SymbolMeta(symbol="000020", listed_days=30),
                # 결측 많음 1개 — max_missing_ratio=0.05 default 로 제외.
                SymbolMeta(symbol="000030", listed_days=300, missing_ratio=0.30),
            ]
        r = resolve_universe(
            UniverseKind.LIQUIDITY_TOP50, liquidity_source=fake_source,
        )
        # 5 종 통과, 3 종 제외.
        assert r.symbol_count == 5
        assert r.available_before_filter == 8
        # 사유별 카운트.
        assert r.excluded_reasons.get("managed", 0) == 1
        assert r.excluded_reasons.get("listed_less_than_min_days", 0) == 1
        assert r.excluded_reasons.get("missing_ratio_too_high", 0) == 1
        # operator_note 에 단계적 확장 정책 명시.
        assert "50" in (r.operator_note or "") or "100" in (r.operator_note or "")
        assert "확장" in (r.operator_note or "") or "단계" in (r.operator_note or "")

    def test_liquidity_empty_filter_result_returns_empty_list(self):
        """user spec: 후보가 없으면 *억지로 만들지 않는다*."""
        def empty_source(kind, top_n):
            return [
                SymbolMeta(symbol="000010", is_suspended=True),   # 모두 거래정지.
                SymbolMeta(symbol="000020", is_managed=True),
            ]
        r = resolve_universe(
            UniverseKind.LIQUIDITY_TOP50, liquidity_source=empty_source,
        )
        assert r.symbol_count == 0
        assert r.excluded_reasons.get("suspended") == 1
        assert r.excluded_reasons.get("managed") == 1

    def test_liquidity_respects_top_n_cap(self):
        """필터 통과가 top_n 초과면 trading_value desc 로 잘림."""
        def big_source(kind, top_n):
            # 60 종 — 모두 통과 가능한 정상 종목.
            return [
                SymbolMeta(
                    symbol=f"{i:06d}",
                    avg_trading_value=1_000_000_000 - i,
                    listed_days=300, missing_ratio=0.0,
                )
                for i in range(60)
            ]
        r = resolve_universe(
            UniverseKind.LIQUIDITY_TOP50, liquidity_source=big_source,
        )
        # top_n=50 으로 cap.
        assert r.symbol_count == 50
        # trading_value desc 정렬 → 첫 symbol 의 trading_value 가 가장 큼.
        assert r.symbols[0] == "000000"   # i=0 → trading_value=1_000_000_000

    def test_custom_filter_policy_relaxed_passes_more(self):
        def source(kind, top_n):
            return [
                SymbolMeta(symbol="000001", listed_days=30),    # 신규상장
                SymbolMeta(symbol="000002", listed_days=30),
            ]
        # default 정책 — 2종 모두 제외.
        r1 = resolve_universe(UniverseKind.LIQUIDITY_TOP50, liquidity_source=source)
        assert r1.symbol_count == 0
        # 정책 완화 (min_listed_days=0) — 2종 통과.
        relaxed = SymbolFilterPolicy(min_listed_days=0)
        r2 = resolve_universe(
            UniverseKind.LIQUIDITY_TOP50, policy=relaxed, liquidity_source=source,
        )
        assert r2.symbol_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# 6. UniverseResolution.to_dict — invariants carry
# ─────────────────────────────────────────────────────────────────────────────


class TestResolutionDict:
    def test_to_dict_carries_advisory_invariants(self):
        r = resolve_universe(UniverseKind.SAMPLE10)
        d = r.to_dict()
        assert d["is_order_signal"]       is False
        assert d["auto_apply_allowed"]    is False
        assert d["is_live_authorization"] is False
        # disclaimer 명시.
        assert "advisory" in d["advisory_disclaimer"] \
            or "주문 신호" in d["advisory_disclaimer"]

    def test_to_dict_has_no_secret_fields(self):
        r = resolve_universe(UniverseKind.SAMPLE10)
        d = r.to_dict()
        all_keys = " ".join(d.keys()).lower()
        for forbidden in ("api_key", "secret", "account", "anthropic", "openai"):
            assert forbidden not in all_keys


# ─────────────────────────────────────────────────────────────────────────────
# 7. Static guards — forbidden imports
# ─────────────────────────────────────────────────────────────────────────────


_MOD = (
    Path(__file__).resolve().parents[1] / "app" / "backtest" / "real_data"
    / "universe.py"
)


class TestNoForbiddenImports:
    def test_no_broker_or_executor_imports(self):
        src = _MOD.read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.kis\b",
            r"from\s+app\.brokers\.mock_broker\b",
            r"from\s+app\.execution\.executor\b",
            r"from\s+app\.execution\.order_router\b",
            r"broker\.place_order\(",
            r"route_order\s*\(",
            r"OrderExecutor\s*\(",
            r"KisClient\b",
        ]
        for pat in forbidden:
            assert not re.search(pat, src, re.MULTILINE), \
                f"forbidden in universe.py: {pat}"

    def test_no_external_http_or_ai_sdk(self):
        src = _MOD.read_text(encoding="utf-8")
        forbidden = [
            r"^import\s+anthropic\b",
            r"^import\s+openai\b",
            r"^import\s+requests\b",
            r"^import\s+httpx\b",
            r"^import\s+yfinance\b",
            r"^from\s+anthropic\b",
            r"^from\s+openai\b",
            r"^from\s+httpx\b",
            r"^from\s+requests\b",
        ]
        for pat in forbidden:
            assert not re.search(pat, src, re.MULTILINE), \
                f"forbidden http/AI in universe.py: {pat}"

    def test_no_safety_flag_mutation(self):
        src = _MOD.read_text(encoding="utf-8")
        bad = [
            r"ENABLE_LIVE_TRADING\s*=\s*['\"]?true",
            r"ENABLE_AI_EXECUTION\s*=\s*['\"]?true",
            r"settings\.enable_live_trading\s*=",
        ]
        for pat in bad:
            assert not re.search(pat, src, re.IGNORECASE), \
                f"safety flag mutation: {pat}"


class TestSchemaFieldLock:
    def test_policy_has_no_secret_fields(self):
        secret_names = [
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number", "anthropic_api_key",
            "openai_api_key", "kis_app_key", "kis_app_secret",
        ]
        actual = set(SymbolFilterPolicy.__dataclass_fields__.keys())
        for n in secret_names:
            assert n not in actual, f"policy has secret field: {n}"

    def test_resolution_has_no_secret_fields(self):
        secret_names = [
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number", "anthropic_api_key",
            "openai_api_key", "kis_app_key", "kis_app_secret",
        ]
        actual = set(UniverseResolution.__dataclass_fields__.keys())
        for n in secret_names:
            assert n not in actual, f"resolution has secret field: {n}"

    def test_symbol_meta_has_no_secret_fields(self):
        secret_names = [
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number",
        ]
        actual = set(SymbolMeta.__dataclass_fields__.keys())
        for n in secret_names:
            assert n not in actual, f"meta has secret field: {n}"
