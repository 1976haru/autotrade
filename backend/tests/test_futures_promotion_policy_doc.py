"""Futures Promotion Policy (#76) — doc-only invariants.

본 테스트는 *문서*에 대한 정적 가드만 수행한다. 코드 / Secret / live flag
mutate / 실제 broker 호출 형식이 본 문서에 들어가지 않음을 확인.
"""

from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH       = PROJECT_ROOT / "docs" / "futures_promotion_policy.md"
README_PATH    = PROJECT_ROOT / "README.md"
CLAUDE_PATH    = PROJECT_ROOT / "CLAUDE.md"
BLOCKERS_PATH  = PROJECT_ROOT / "docs" / "live_activation_blockers.md"
SCOPE_PATH     = PROJECT_ROOT / "docs" / "futures_scope.md"
MARGIN_PATH    = PROJECT_ROOT / "docs" / "futures_margin_risk.md"
STRATEGY_PATH  = PROJECT_ROOT / "docs" / "futures_strategy_contract.md"
PROMOTION_PATH = PROJECT_ROOT / "docs" / "promotion_policy.md"


# ---------- existence ----------


def test_futures_promotion_policy_md_exists():
    assert DOC_PATH.exists(), "docs/futures_promotion_policy.md should exist"


# ---------- content invariants ----------


def _read() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def test_doc_declares_futures_is_last_stage():
    text = _read()
    assert "가장 마지막" in text


def test_doc_lists_seven_stage_ladder():
    text = _read()
    for stage in (
        "FUTURES_DISABLED",
        "FUTURES_SIMULATION",
        "FUTURES_SHADOW",
        "FUTURES_PAPER",
        "FUTURES_MANUAL_APPROVAL",
        "FUTURES_AI_ASSIST",
        "FUTURES_AI_EXECUTION_BLOCKED",
    ):
        assert stage in text, f"stage missing: {stage}"


def test_doc_marks_ai_execution_blocked_permanently():
    text = _read()
    assert "영구 BLOCKED" in text or "영구 BLOCKED" in text.replace("\n", " ")


def test_doc_references_futures_allowed_false_invariant():
    text = _read()
    assert "futures_allowed=False" in text


def test_doc_forbids_auto_rollover_and_expiry_ai():
    text = _read()
    assert "자동 롤오버" in text
    assert "만기일 근처 AI 자동매매" in text or "만기일 근처 AI" in text


def test_doc_lists_paper_pass_criteria():
    text = _read()
    # PAPER 단계 PASS 기준 핵심 키워드.
    for needle in ["expectancy", "PF", "MDD", "Monte Carlo", "MarginRule"]:
        assert needle in text, f"missing PAPER criteria keyword: {needle}"


def test_doc_lists_loss_limit_margin_leverage_table():
    text = _read()
    # 손실한도 / 증거금 / 레버리지 / 청산거리 매트릭스 핵심 키워드.
    for needle in ["max_contracts", "max_leverage", "max_daily_futures_loss",
                   "청산 거리"]:
        assert needle in text, f"missing risk policy keyword: {needle}"


def test_doc_states_required_preconditions_to_start_futures():
    """주식 단계 안정화 *이후* 선물을 시작한다는 정책 명시 확인."""
    text = _read()
    # 주식 AI Assist 이후 / 주식 MVP 완료 / 사용자 옵트인 등 핵심 어휘.
    assert "주식 MVP" in text
    assert "주식 AI Assist" in text or "AI Assist까지" in text


# ---------- forbidden mutations / leaks ----------


_SECRET_PATTERNS = [
    r"KIS_APP_KEY\s*=\s*[A-Za-z0-9\-]{8,}",
    r"KIS_APP_SECRET\s*=\s*[A-Za-z0-9\-/+=]{16,}",
    r"ANTHROPIC_API_KEY\s*=\s*sk-[A-Za-z0-9\-]{8,}",
    r"OPENAI_API_KEY\s*=\s*sk-[A-Za-z0-9\-]{8,}",
    r"TELEGRAM_BOT_TOKEN\s*=\s*\d{8,}:[A-Za-z0-9_-]{8,}",
    r"\b\d{8,10}-\d{2}-\d{4,}\b",
    r"Bearer\s+[A-Za-z0-9\-_=\.]{16,}",
    r"\bsk-[A-Za-z0-9]{20,}\b",
]


def test_no_secret_patterns_in_doc():
    text = _read()
    for pat in _SECRET_PATTERNS:
        m = re.search(pat, text)
        assert m is None, (
            f"secret-shaped pattern leaked into doc: {pat!r} -> {m.group(0)!r}"
        )


def test_no_python_assignments_that_mutate_safety_flags():
    """문서에서 안전 플래그를 *값*으로 설정하는 코드 패턴이 들어가지 않음."""
    text = _read()
    # 본 문서는 정책 설명만 — Python 대입 어휘는 *코드* 가이드에서만 등장해야 한다.
    forbidden = [
        "settings.enable_futures_live_trading = True",
        "os.environ[\"ENABLE_FUTURES_LIVE_TRADING\"] = \"true\"",
        "setattr(settings, \"enable_futures_live_trading\", True)",
    ]
    for needle in forbidden:
        assert needle not in text, (
            f"forbidden Python mutation pattern in doc: {needle!r}"
        )


def test_no_broker_call_code_in_doc():
    """본 문서는 정책만 — 실제 broker 호출 코드 예제가 들어가서는 안 된다."""
    text = _read()
    forbidden = [
        "broker.place_order(",
        "broker.cancel_order(",
        "route_order(",
        "OrderExecutor(",
        "from anthropic import",
        "import openai",
        "httpx.post(",
        "requests.post(",
    ]
    for needle in forbidden:
        assert needle not in text, (
            f"broker / external HTTP / AI SDK code in doc: {needle!r}"
        )


# ---------- cross-link checks ----------


def test_readme_links_to_futures_promotion_policy():
    text = README_PATH.read_text(encoding="utf-8")
    assert "futures_promotion_policy.md" in text


def test_claude_md_mentions_76_and_blocked():
    text = CLAUDE_PATH.read_text(encoding="utf-8")
    assert "#76" in text
    # AI 자동 실행이 영구 BLOCKED 라는 문구.
    assert "FUTURES_AI_EXECUTION" in text or "futures_promotion_policy" in text


def test_live_activation_blockers_references_76():
    text = BLOCKERS_PATH.read_text(encoding="utf-8")
    assert "futures_promotion_policy.md" in text
    assert "#76" in text


def test_futures_scope_links_to_promotion_policy():
    text = SCOPE_PATH.read_text(encoding="utf-8")
    assert "futures_promotion_policy.md" in text


def test_futures_margin_risk_links_to_promotion_policy():
    text = MARGIN_PATH.read_text(encoding="utf-8")
    assert "futures_promotion_policy.md" in text


def test_futures_strategy_contract_links_to_promotion_policy():
    text = STRATEGY_PATH.read_text(encoding="utf-8")
    assert "futures_promotion_policy.md" in text


def test_promotion_policy_links_to_futures_promotion_policy():
    text = PROMOTION_PATH.read_text(encoding="utf-8")
    assert "futures_promotion_policy.md" in text
