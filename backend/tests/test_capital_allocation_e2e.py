"""P-19: 자금/포지션 설정 E2E 테스트.

시드머니 10,000,000원 / 종목당 투자금 1,000,000원 기준으로, *지금까지 만든
자금·포지션 정책 함수들* 이 실제 AI Paper BUY 판단 흐름에서 함께 동작하는지
검증한다.

본 테스트의 `evaluate_buy_candidate` 는 **실제 production 순수 함수들** 을
사용자 요청서 §2 순서대로 조합한다 (가짜 구현 0건):

  1. check_price_freshness          (P-14, app.market.freshness)
  2. compute_paper_quantity_by_price (P-08, app.auto_paper.position_sizer)
  3. check_duplicate_position_buy    (P-12, app.auto_paper.capital_state)
  4. check_buy_cash_sufficient       (P-07, app.auto_paper.capital_state)
  5. check_daily_buy_limit           (P-10, app.risk.loss_limits)
  6. check_symbol_weight_limit       (P-11, app.risk.position_limits)

**실거래 경로 0건** — broker / OrderExecutor / route_order / RiskManager.live /
PermissionGate.live 를 호출하지 않으며, 위 함수들은 모두 *pure advisory* 다
(상태 변경 0건). 따라서 차단 케이스가 cash / daily used / exposure 를 증가시킬
수 없다 (정의상 mutation 0건).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── 실제 production 정책 함수 (가짜 구현 아님) ──
from app.auto_paper.capital_config import (
    DEFAULT_DAILY_BUY_LIMIT_KRW,
    DEFAULT_MAX_CONCURRENT_POSITIONS,
    DEFAULT_MAX_SYMBOL_WEIGHT_PCT,
    DEFAULT_PAPER_INITIAL_CASH,
    DEFAULT_PER_SYMBOL_MAX_KRW,
)
from app.auto_paper.blocked_reasons import normalize_reason_code, title_for
from app.auto_paper.capital_state import (
    check_buy_cash_sufficient,
    check_duplicate_position_buy,
)
from app.auto_paper.position_sizer import compute_paper_quantity_by_price
from app.market.freshness import check_price_freshness
from app.risk.loss_limits import check_daily_buy_limit
from app.risk.position_limits import check_symbol_weight_limit


# ─────────────────────────────────────────────────────────────────────────────
# E2E helper — 실제 정책 함수 조합
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CapitalSettings:
    initial_cash:          int = DEFAULT_PAPER_INITIAL_CASH        # 10,000,000
    per_symbol_cap_krw:    int = DEFAULT_PER_SYMBOL_MAX_KRW        # 1,000,000
    max_positions:         int = DEFAULT_MAX_CONCURRENT_POSITIONS  # 3
    max_daily_buy_amount:  int = DEFAULT_DAILY_BUY_LIMIT_KRW       # 3,000,000
    max_symbol_weight_pct: float = DEFAULT_MAX_SYMBOL_WEIGHT_PCT   # 0.20
    allow_additional_buy:  bool = False


@dataclass(frozen=True)
class PriceSnapshot:
    price:           float | None
    price_timestamp: datetime | None = None
    now:             datetime | None = None
    max_age_seconds: int = 60
    reference_price: float | None = None
    max_change_pct:  float = 10.0


@dataclass(frozen=True)
class PositionState:
    available_cash_krw:             int = DEFAULT_PAPER_INITIAL_CASH
    today_buy_used_amount:          int = 0
    current_symbol_exposure_amount: int = 0
    current_position_quantity:      int = 0
    total_paper_equity:             int = DEFAULT_PAPER_INITIAL_CASH


def make_capital_settings(**kw) -> CapitalSettings:
    return CapitalSettings(**kw)


def make_price_snapshot(price, **kw) -> PriceSnapshot:
    return PriceSnapshot(price=price, **kw)


def make_position_state(**kw) -> PositionState:
    return PositionState(**kw)


def evaluate_buy_candidate(
    *,
    symbol: str,
    action: str,
    settings: CapitalSettings,
    price: PriceSnapshot,
    state: PositionState,
) -> dict:
    """사용자 요청서 §2 순서대로 production 정책 함수 조합 → 단일 verdict.

    반환: {allowed, symbol, quantity, notional, reason_code, reason_message,
    reason_title, stage}. `reason_title` 은 P-17 표시 어휘(blocked_reasons)의
    한국어 제목 — 사용자 화면에 노출되는 canonical 문구.
    실거래 경로 호출 0건 (모든 함수가 pure advisory).
    """
    def _verdict(*, allowed, quantity, notional, reason_code, reason_message, stage):
        return {
            "allowed": allowed, "symbol": symbol,
            "quantity": quantity, "notional": notional,
            "reason_code": reason_code, "reason_message": reason_message,
            "reason_title": title_for(normalize_reason_code(reason_code)),
            "stage": stage,
        }

    # 1. price freshness / abnormal (P-14).
    fresh = check_price_freshness(
        symbol=symbol, price=price.price, price_timestamp=price.price_timestamp,
        now=price.now, max_age_seconds=price.max_age_seconds,
        reference_price=price.reference_price, max_change_pct=price.max_change_pct,
        action=action,
    )
    if not fresh.allowed:
        return _verdict(
            allowed=False, quantity=0, notional=0,
            reason_code=fresh.reason_code, reason_message=fresh.reason_message,
            stage="price_freshness")

    # 2. position sizing + 고가주/최소 1주 (P-08/P-06).
    sized = compute_paper_quantity_by_price(
        action=action, symbol=symbol, price=price.price,
        max_amount_krw=settings.per_symbol_cap_krw,
    )
    if sized.reason_code == "SKIP_NON_BUY":
        # BUY 가 아니면 자금/포지션 BUY 제한 적용 안 함 — order_candidate 로 통과.
        return _verdict(
            allowed=True, quantity=0, notional=0,
            reason_code="SKIP_NON_BUY", reason_message=sized.reason_ko,
            stage="order_candidate")
    if sized.quantity < 1:
        return _verdict(
            allowed=False, quantity=0, notional=0,
            reason_code=sized.reason_code, reason_message=sized.reason_ko,
            stage="affordability")
    qty = sized.quantity
    notional = sized.notional_krw

    # 3. 중복 보유 (P-12).
    dup = check_duplicate_position_buy(
        side=action, symbol=symbol,
        current_position_quantity=state.current_position_quantity,
        allow_additional_buy=settings.allow_additional_buy,
    )
    if not dup.allowed:
        return _verdict(
            allowed=False, quantity=qty, notional=notional,
            reason_code=dup.reason_code, reason_message=dup.reason_message,
            stage="duplicate_position")

    # 4. Paper 현금 (P-07).
    cash = check_buy_cash_sufficient(
        action=action, symbol=symbol, price=price.price, quantity=qty,
        available_cash_krw=state.available_cash_krw,
    )
    if cash.verdict.value == "INSUFFICIENT_PAPER_CASH":
        return _verdict(
            allowed=False, quantity=qty, notional=notional,
            reason_code="INSUFFICIENT_PAPER_CASH", reason_message=cash.reason_ko,
            stage="paper_cash")

    # 5. 일일 매수 한도 (P-10).
    daily = check_daily_buy_limit(
        side=action, price=price.price, quantity=qty,
        today_buy_used_amount=state.today_buy_used_amount,
        max_daily_buy_amount=settings.max_daily_buy_amount, symbol=symbol,
    )
    if not daily.allowed:
        return _verdict(
            allowed=False, quantity=qty, notional=notional,
            reason_code=daily.reason_code, reason_message=daily.reason_message,
            stage="daily_buy_limit")

    # 6. 종목별 최대 비중 (P-11).
    weight = check_symbol_weight_limit(
        side=action, symbol=symbol, price=price.price, quantity=qty,
        total_paper_equity=state.total_paper_equity,
        current_symbol_exposure_amount=state.current_symbol_exposure_amount,
        max_symbol_weight_pct=settings.max_symbol_weight_pct,
    )
    if not weight.allowed:
        return _verdict(
            allowed=False, quantity=qty, notional=notional,
            reason_code=weight.reason_code, reason_message=weight.reason_message,
            stage="symbol_weight_limit")

    # 7. 모두 통과 → order candidate 생성 가능.
    return _verdict(
        allowed=True, quantity=qty, notional=notional,
        reason_code="OK", reason_message=sized.reason_ko,
        stage="order_candidate")


# 표준 fresh price snapshot (stale/abnormal 아님).
def _fresh_price(price, *, reference_price=None, max_change_pct=10.0):
    now = datetime(2026, 5, 23, 5, 0, 0, tzinfo=timezone.utc)
    return make_price_snapshot(
        price=price, price_timestamp=now - timedelta(seconds=5), now=now,
        max_age_seconds=60, reference_price=reference_price,
        max_change_pct=max_change_pct,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. 기본 설정 / 정상 종목
# ─────────────────────────────────────────────────────────────────────────────


class TestBaselineAndNormal:
    def test_balanced_defaults(self):
        s = make_capital_settings()
        assert s.initial_cash == 10_000_000
        assert s.per_symbol_cap_krw == 1_000_000
        assert s.max_positions == 3
        assert s.max_daily_buy_amount == 3_000_000
        assert s.max_symbol_weight_pct == 0.20
        assert s.allow_additional_buy is False

    def test_normal_quantity_13(self):
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(75_000), state=make_position_state(),
        )
        assert r["quantity"] == 13

    def test_normal_notional_975000(self):
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(75_000), state=make_position_state(),
        )
        assert r["notional"] == 975_000

    def test_normal_allowed(self):
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(75_000), state=make_position_state(),
        )
        assert r["allowed"] is True
        assert r["stage"] == "order_candidate"
        assert r["reason_code"] == "OK"


# ─────────────────────────────────────────────────────────────────────────────
# 2. 고가주 / 저가주
# ─────────────────────────────────────────────────────────────────────────────


class TestHighLowPrice:
    def test_high_price_quantity_zero(self):
        r = evaluate_buy_candidate(
            symbol="373220", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(1_200_000), state=make_position_state(),
        )
        assert r["quantity"] == 0

    def test_high_price_min_lot_not_affordable(self):
        r = evaluate_buy_candidate(
            symbol="373220", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(1_200_000), state=make_position_state(),
        )
        assert r["allowed"] is False
        assert r["reason_code"] == "MIN_LOT_NOT_AFFORDABLE"
        assert r["stage"] == "affordability"

    def test_low_price_quantity_83(self):
        r = evaluate_buy_candidate(
            symbol="123456", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(12_000), state=make_position_state(),
        )
        assert r["quantity"] == 83

    def test_low_price_notional_996000(self):
        r = evaluate_buy_candidate(
            symbol="123456", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(12_000), state=make_position_state(),
        )
        assert r["notional"] == 996_000
        assert r["allowed"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. 차단 사유들
# ─────────────────────────────────────────────────────────────────────────────


class TestBlocks:
    def test_insufficient_paper_cash(self):
        # 13주 @ 75,000 = 975,000 필요하지만 현금 300,000.
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(75_000),
            state=make_position_state(available_cash_krw=300_000),
        )
        assert r["allowed"] is False
        assert r["reason_code"] == "INSUFFICIENT_PAPER_CASH"
        assert r["stage"] == "paper_cash"

    def test_daily_buy_limit_exceeded(self):
        # 오늘 이미 2,900,000 사용 + 신규 975,000 → 3,875,000 > 3,000,000.
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(75_000),
            state=make_position_state(today_buy_used_amount=2_900_000),
        )
        assert r["allowed"] is False
        assert r["reason_code"] == "DAILY_BUY_LIMIT_EXCEEDED"
        assert r["stage"] == "daily_buy_limit"

    def test_symbol_weight_limit_exceeded(self):
        # 종목 비중 5% 한도인데 975,000 / 10,000,000 = 9.75% → 초과.
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY",
            settings=make_capital_settings(max_symbol_weight_pct=0.05),
            price=_fresh_price(75_000),
            state=make_position_state(),
        )
        assert r["allowed"] is False
        assert r["reason_code"] == "SYMBOL_WEIGHT_LIMIT_EXCEEDED"
        assert r["stage"] == "symbol_weight_limit"

    def test_duplicate_position_blocked(self):
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(75_000),
            state=make_position_state(current_position_quantity=10),
        )
        assert r["allowed"] is False
        assert r["reason_code"] == "DUPLICATE_POSITION_BUY_BLOCKED"
        assert r["stage"] == "duplicate_position"

    def test_price_stale(self):
        now = datetime(2026, 5, 23, 5, 0, 0, tzinfo=timezone.utc)
        snap = make_price_snapshot(
            price=75_000, price_timestamp=now - timedelta(seconds=180),
            now=now, max_age_seconds=60,
        )
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=snap, state=make_position_state(),
        )
        assert r["allowed"] is False
        assert r["reason_code"] == "PRICE_STALE"
        assert r["stage"] == "price_freshness"

    def test_abnormal_price_move(self):
        # ref 75,000 → 85,000 = +13.33% > 10%.
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(85_000, reference_price=75_000, max_change_pct=10.0),
            state=make_position_state(),
        )
        assert r["allowed"] is False
        assert r["reason_code"] == "ABNORMAL_PRICE_MOVE"
        assert r["stage"] == "price_freshness"


# ─────────────────────────────────────────────────────────────────────────────
# 4. 한국어 reason_message
# ─────────────────────────────────────────────────────────────────────────────


class TestKoreanMessages:
    def test_all_block_messages_korean(self):
        cases = [
            (evaluate_buy_candidate(
                symbol="373220", action="BUY", settings=make_capital_settings(),
                price=_fresh_price(1_200_000), state=make_position_state()),
             "1주 가격이 투자한도 초과로 제외", None),  # min-lot 메시지(자연어)
            (evaluate_buy_candidate(
                symbol="005930", action="BUY", settings=make_capital_settings(),
                price=_fresh_price(75_000),
                state=make_position_state(available_cash_krw=300_000)),
             "남은 Paper 현금이 부족하여 매수 차단", "INSUFFICIENT_PAPER_CASH"),
            (evaluate_buy_candidate(
                symbol="005930", action="BUY", settings=make_capital_settings(),
                price=_fresh_price(75_000),
                state=make_position_state(today_buy_used_amount=2_900_000)),
             "일일 최대 매수금액을 초과하여 매수 차단", "DAILY_BUY_LIMIT_EXCEEDED"),
            (evaluate_buy_candidate(
                symbol="005930", action="BUY",
                settings=make_capital_settings(max_symbol_weight_pct=0.05),
                price=_fresh_price(75_000), state=make_position_state()),
             "종목별 최대 비중을 초과하여 매수 차단", "SYMBOL_WEIGHT_LIMIT_EXCEEDED"),
            (evaluate_buy_candidate(
                symbol="005930", action="BUY", settings=make_capital_settings(),
                price=_fresh_price(75_000),
                state=make_position_state(current_position_quantity=10)),
             "이미 보유 중인 종목이라 추가 매수 차단", "DUPLICATE_POSITION_BUY_BLOCKED"),
        ]
        for result, expected_title, expected_code in cases:
            assert result["allowed"] is False
            # canonical 표시 제목(reason_title) 이 사용자 요청서 예상 문구와 일치.
            assert result["reason_title"] == expected_title, (
                f"{result['reason_code']}: title={result['reason_title']!r}"
            )
            if expected_code:
                assert result["reason_code"] == expected_code
            # production reason_message 도 비어있지 않은 한국어.
            assert result["reason_message"]

    def test_stale_and_abnormal_messages(self):
        now = datetime(2026, 5, 23, 5, 0, 0, tzinfo=timezone.utc)
        stale = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=make_price_snapshot(price=75_000,
                                      price_timestamp=now - timedelta(seconds=180),
                                      now=now, max_age_seconds=60),
            state=make_position_state())
        assert "현재가가 오래되어 매수 차단" in stale["reason_message"]
        abn = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(85_000, reference_price=75_000, max_change_pct=10.0),
            state=make_position_state())
        assert "가격 급등락이 감지되어 매수 차단" in abn["reason_message"]


# ─────────────────────────────────────────────────────────────────────────────
# 5. 우선순위 / not-applicable / 경계값 / 상태 불변
# ─────────────────────────────────────────────────────────────────────────────


class TestOrderingAndEdges:
    def test_freshness_blocks_before_sizing_and_cash(self):
        # stale + 현금 0 + 고가주여도 가장 먼저 freshness 가 차단.
        now = datetime(2026, 5, 23, 5, 0, 0, tzinfo=timezone.utc)
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=make_price_snapshot(price=1_200_000,
                                      price_timestamp=now - timedelta(seconds=300),
                                      now=now, max_age_seconds=60),
            state=make_position_state(available_cash_krw=0))
        assert r["stage"] == "price_freshness"
        assert r["reason_code"] == "PRICE_STALE"

    def test_high_price_blocks_before_cash(self):
        # 고가주 + 현금 0 → cash 단계 전에 affordability 차단.
        r = evaluate_buy_candidate(
            symbol="373220", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(1_200_000),
            state=make_position_state(available_cash_krw=0))
        assert r["stage"] == "affordability"
        assert r["reason_code"] == "MIN_LOT_NOT_AFFORDABLE"

    def test_allow_additional_buy_still_applies_cash_limit(self):
        # 추가매수 허용이어도 현금 부족이면 여전히 차단.
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY",
            settings=make_capital_settings(allow_additional_buy=True),
            price=_fresh_price(75_000),
            state=make_position_state(available_cash_krw=300_000,
                                      current_position_quantity=10))
        assert r["allowed"] is False
        assert r["reason_code"] == "INSUFFICIENT_PAPER_CASH"

    def test_allow_additional_buy_still_applies_daily_limit(self):
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY",
            settings=make_capital_settings(allow_additional_buy=True),
            price=_fresh_price(75_000),
            state=make_position_state(today_buy_used_amount=2_900_000,
                                      current_position_quantity=10))
        assert r["allowed"] is False
        assert r["reason_code"] == "DAILY_BUY_LIMIT_EXCEEDED"

    def test_daily_limit_exactly_at_limit_allowed(self):
        # 오늘 2,025,000 사용 + 975,000 = 정확히 3,000,000 → 허용.
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(75_000),
            state=make_position_state(today_buy_used_amount=2_025_000))
        assert r["allowed"] is True
        assert r["stage"] == "order_candidate"

    def test_symbol_exposure_exactly_at_limit_allowed(self):
        # 종목 비중 한도 = 10,000,000 * 0.0975 = 975,000. 신규 노출 975,000 →
        # projected 정확히 한도 → 허용 (projected > max 만 차단).
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY",
            settings=make_capital_settings(max_symbol_weight_pct=0.0975),
            price=_fresh_price(75_000),
            state=make_position_state(current_symbol_exposure_amount=0))
        assert r["allowed"] is True

    def test_sell_not_applicable_to_buy_limits(self):
        # SELL 은 cash/daily/symbol/duplicate BUY 제한 미적용 → order_candidate.
        r = evaluate_buy_candidate(
            symbol="005930", action="SELL", settings=make_capital_settings(),
            price=_fresh_price(75_000),
            state=make_position_state(available_cash_krw=0,
                                      today_buy_used_amount=99_999_999,
                                      current_position_quantity=10))
        assert r["allowed"] is True
        assert r["reason_code"] == "SKIP_NON_BUY"

    def test_hold_not_applicable(self):
        r = evaluate_buy_candidate(
            symbol="005930", action="HOLD", settings=make_capital_settings(),
            price=_fresh_price(75_000), state=make_position_state(available_cash_krw=0))
        assert r["allowed"] is True
        assert r["reason_code"] == "SKIP_NON_BUY"

    def test_blocked_case_does_not_mutate_state(self):
        # 차단 케이스가 입력 state / 전역 capital_state 를 변경하지 않음.
        from app.auto_paper.capital_state import (
            get_capital_state,
            reset_capital_state_for_tests,
        )
        reset_capital_state_for_tests(initial_cash_krw=10_000_000)
        before = get_capital_state().snapshot().to_dict()
        state = make_position_state(available_cash_krw=300_000)
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(75_000), state=state)
        after = get_capital_state().snapshot().to_dict()
        assert r["allowed"] is False
        # 입력 state frozen — 변경 불가. 전역 capital_state snapshot 불변.
        assert state.available_cash_krw == 300_000
        assert before == after
        reset_capital_state_for_tests(initial_cash_krw=10_000_000)

    def test_allowed_case_produces_order_candidate(self):
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(75_000), state=make_position_state())
        assert r["allowed"] is True
        assert r["stage"] == "order_candidate"
        assert r["quantity"] > 0 and r["notional"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# 6. 안전 가드 — 실거래 경로 / secret 미포함
# ─────────────────────────────────────────────────────────────────────────────


class TestSafetyGuards:
    def test_e2e_file_has_no_live_order_paths(self):
        text = Path(__file__).read_text(encoding="utf-8")
        for pat in (
            r"\bbroker\.place_order\s*\(",
            r"\broute_order\s*\(",
            r"OrderExecutor\s*\(",
            r"ENABLE_LIVE_TRADING\s*=\s*[\"']?true",
            r"ENABLE_AI_EXECUTION\s*=\s*[\"']?true",
        ):
            assert not re.search(pat, text, re.IGNORECASE), (
                f"E2E 테스트가 실거래 경로 사용 의심: /{pat}/"
            )

    def test_result_dicts_have_no_secret_fields(self):
        r = evaluate_buy_candidate(
            symbol="005930", action="BUY", settings=make_capital_settings(),
            price=_fresh_price(75_000), state=make_position_state())
        joined = " ".join(f"{k}={v}" for k, v in r.items()).lower()
        for banned in ("api_key", "app_secret", "account_no", "access_token",
                       "secret_token", "password"):
            assert banned not in joined
