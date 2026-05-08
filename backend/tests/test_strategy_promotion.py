"""Strategy Promotion Gate 테스트 (#27)."""

from app.governance.strategy_promotion import (
    PromotionDecision,
    PromotionInput,
    PromotionStage,
    evaluate_promotion,
    next_stage,
)


# 단계별로 모든 코드 기준을 통과시키는 보수적 입력 빌더 — 이걸 base로 두고
# 단일 필드만 변형해 각 fail 케이스를 만든다.
def _passing_input(
    *,
    strategy_name="test",
    current=PromotionStage.BACKTEST,
    target=PromotionStage.LIVE_SHADOW,
    **overrides,
) -> PromotionInput:
    base = dict(
        strategy_name=strategy_name,
        current_stage=current, target_stage=target,
        trade_count=200, expectancy=50.0, profit_factor=1.5,
        max_drawdown=500_000, max_consecutive_losses=3, win_rate=0.55,
        initial_cash=10_000_000,
        cost_adjusted=True, slippage_adjusted=True,
        walk_forward_passed=True, walk_forward_recommendation="PASS",
        positive_fold_ratio=0.75, holdout_pnl=100_000,
        single_best_fold_pnl_share=0.5,
        monte_carlo_run=True, monte_carlo_risk_of_ruin=0.0,
        monte_carlo_worst_5pct_mdd=200_000,
        monte_carlo_longest_losing_streak=4,
        data_quality_score=85.0, data_quality_grade="GOOD",
        shadow_days=30, paper_days=30, live_manual_days=30,
        daily_loss_limit_violations=0, risk_policy_violations=0,
        audit_log_missing_count=0, partial_fill_audit_ok=True,
        human_approved=True,
        ai_recommended=False,
        ai_recommendation_accuracy=0.75,
    )
    base.update(overrides)
    return PromotionInput(**base)


# ---------- next_stage 매핑 ----------


def test_next_stage_mapping():
    assert next_stage(PromotionStage.BACKTEST) == PromotionStage.LIVE_SHADOW
    assert next_stage(PromotionStage.LIVE_SHADOW) == PromotionStage.PAPER
    assert next_stage(PromotionStage.PAPER) == PromotionStage.LIVE_MANUAL_APPROVAL
    assert next_stage(PromotionStage.LIVE_MANUAL_APPROVAL) == PromotionStage.LIVE_AI_ASSIST
    assert next_stage(PromotionStage.LIVE_AI_ASSIST) == PromotionStage.LIVE_AI_EXECUTION
    assert next_stage(PromotionStage.LIVE_AI_EXECUTION) is None


def test_skipping_stages_blocks():
    """BACKTEST → PAPER 같은 다단 점프는 BLOCKED."""
    inp = _passing_input(target=PromotionStage.PAPER)
    r = evaluate_promotion(inp)
    assert r.decision == PromotionDecision.BLOCKED
    assert any("한 번에 한 단계" in c for c in r.failed_criteria)


# ---------- 기본 PASS ----------


def test_passing_input_to_shadow_returns_pass():
    inp = _passing_input(current=PromotionStage.BACKTEST,
                         target=PromotionStage.LIVE_SHADOW)
    r = evaluate_promotion(inp)
    assert r.decision == PromotionDecision.PASS
    assert r.failed_criteria == []


def test_passing_input_to_paper_returns_pass():
    inp = _passing_input(current=PromotionStage.LIVE_SHADOW,
                         target=PromotionStage.PAPER)
    r = evaluate_promotion(inp)
    assert r.decision == PromotionDecision.PASS


# ---------- 백테스트 기준 미달 ----------


def test_trade_count_below_min_fails():
    inp = _passing_input(trade_count=50)
    r = evaluate_promotion(inp)
    assert r.decision == PromotionDecision.FAIL
    assert any("거래 수" in c for c in r.failed_criteria)


def test_expectancy_zero_or_negative_fails():
    r = evaluate_promotion(_passing_input(expectancy=0.0))
    assert r.decision == PromotionDecision.FAIL
    assert any("expectancy" in c for c in r.failed_criteria)

    r2 = evaluate_promotion(_passing_input(expectancy=-10.0))
    assert r2.decision == PromotionDecision.FAIL


def test_profit_factor_below_threshold_fails():
    r = evaluate_promotion(_passing_input(profit_factor=1.0))
    assert r.decision == PromotionDecision.FAIL
    assert any("Profit Factor" in c for c in r.failed_criteria)


def test_profit_factor_none_fails():
    r = evaluate_promotion(_passing_input(profit_factor=None))
    assert r.decision == PromotionDecision.FAIL


def test_max_drawdown_above_limit_fails():
    """초기 자본 1000만, MDD 한도 15% = 150만. 200만이면 fail."""
    r = evaluate_promotion(_passing_input(max_drawdown=2_000_000))
    assert r.decision == PromotionDecision.FAIL
    assert any("MDD" in c for c in r.failed_criteria)


def test_consecutive_losses_above_limit_fails():
    r = evaluate_promotion(_passing_input(max_consecutive_losses=10))
    assert r.decision == PromotionDecision.FAIL


def test_cost_not_adjusted_fails():
    r = evaluate_promotion(_passing_input(cost_adjusted=False))
    assert r.decision == PromotionDecision.FAIL
    assert any("수수료" in c for c in r.failed_criteria)


def test_slippage_not_adjusted_fails():
    r = evaluate_promotion(_passing_input(slippage_adjusted=False))
    assert r.decision == PromotionDecision.FAIL
    assert any("슬리피지" in c for c in r.failed_criteria)


# ---------- Walk-forward 기준 ----------


def test_walk_forward_fail_blocks():
    r = evaluate_promotion(_passing_input(walk_forward_recommendation="FAIL"))
    assert r.decision == PromotionDecision.FAIL


def test_walk_forward_caution_does_not_fail_but_caution():
    r = evaluate_promotion(_passing_input(
        walk_forward_recommendation="CAUTION",
        single_best_fold_pnl_share=0.5,  # CAUTION 트리거 회피
    ))
    assert r.decision == PromotionDecision.CAUTION


def test_walk_forward_low_positive_fold_ratio_fails():
    r = evaluate_promotion(_passing_input(positive_fold_ratio=0.4))
    assert r.decision == PromotionDecision.FAIL


def test_walk_forward_holdout_loss_fails():
    r = evaluate_promotion(_passing_input(holdout_pnl=-1000))
    assert r.decision == PromotionDecision.FAIL


def test_walk_forward_high_single_fold_share_caution():
    r = evaluate_promotion(_passing_input(single_best_fold_pnl_share=0.85))
    assert r.decision == PromotionDecision.CAUTION


def test_walk_forward_missing_when_required_fails():
    """SHADOW 승격에는 walk-forward 결과 필수."""
    r = evaluate_promotion(_passing_input(
        walk_forward_passed=None, walk_forward_recommendation=None,
    ))
    assert r.decision == PromotionDecision.FAIL


# ---------- Monte Carlo 기준 ----------


def test_mc_high_ror_fails():
    r = evaluate_promotion(_passing_input(monte_carlo_risk_of_ruin=0.10))
    assert r.decision == PromotionDecision.FAIL
    assert any("파산위험" in c for c in r.failed_criteria)


def test_mc_worst_5pct_mdd_above_limit_fails():
    """초기 자본 1천만, 30% = 3백만. 4백만이면 FAIL."""
    r = evaluate_promotion(_passing_input(monte_carlo_worst_5pct_mdd=4_000_000))
    assert r.decision == PromotionDecision.FAIL


def test_mc_caution_for_intermediate_ror():
    r = evaluate_promotion(_passing_input(monte_carlo_risk_of_ruin=0.02))
    assert r.decision == PromotionDecision.CAUTION


def test_mc_required_at_live_manual_stage():
    """PAPER → LIVE_MANUAL은 MC 실행 필수."""
    r = evaluate_promotion(_passing_input(
        current=PromotionStage.PAPER,
        target=PromotionStage.LIVE_MANUAL_APPROVAL,
        monte_carlo_run=False,
    ))
    assert r.decision == PromotionDecision.FAIL
    assert any("Monte Carlo 미실행" in c for c in r.failed_criteria)


# ---------- Data Quality 기준 ----------


def test_data_quality_exclude_grade_fails():
    r = evaluate_promotion(_passing_input(data_quality_grade="EXCLUDE"))
    assert r.decision == PromotionDecision.FAIL


def test_data_quality_low_score_fails():
    r = evaluate_promotion(_passing_input(data_quality_score=50.0))
    assert r.decision == PromotionDecision.FAIL


def test_data_quality_caution_score_caution():
    r = evaluate_promotion(_passing_input(data_quality_score=70.0))
    assert r.decision == PromotionDecision.CAUTION


# ---------- Paper / Shadow 운영 ----------


def test_shadow_days_below_min_fails():
    r = evaluate_promotion(_passing_input(
        current=PromotionStage.LIVE_SHADOW, target=PromotionStage.PAPER,
        shadow_days=10,
    ))
    assert r.decision == PromotionDecision.FAIL


def test_paper_daily_loss_violations_fails():
    r = evaluate_promotion(_passing_input(
        current=PromotionStage.PAPER, target=PromotionStage.LIVE_MANUAL_APPROVAL,
        daily_loss_limit_violations=1,
    ))
    assert r.decision == PromotionDecision.FAIL
    assert any("일일 손실한도" in c for c in r.failed_criteria)


def test_paper_risk_policy_violations_fails():
    r = evaluate_promotion(_passing_input(
        current=PromotionStage.PAPER, target=PromotionStage.LIVE_MANUAL_APPROVAL,
        risk_policy_violations=1,
    ))
    assert r.decision == PromotionDecision.FAIL


def test_audit_log_missing_fails_at_live_stages():
    r = evaluate_promotion(_passing_input(
        current=PromotionStage.PAPER, target=PromotionStage.LIVE_MANUAL_APPROVAL,
        audit_log_missing_count=1,
    ))
    assert r.decision == PromotionDecision.FAIL


def test_partial_fill_audit_not_ok_fails():
    r = evaluate_promotion(_passing_input(
        current=PromotionStage.PAPER, target=PromotionStage.LIVE_MANUAL_APPROVAL,
        partial_fill_audit_ok=False,
    ))
    assert r.decision == PromotionDecision.FAIL


# ---------- Human / AI approval ----------


def test_live_stage_without_human_approval_blocked():
    """코드 기준 모두 충족이라도 LIVE 단계에 사람 승인 부재면 BLOCKED."""
    r = evaluate_promotion(_passing_input(
        current=PromotionStage.PAPER, target=PromotionStage.LIVE_MANUAL_APPROVAL,
        human_approved=False,
    ))
    assert r.decision == PromotionDecision.BLOCKED
    assert any("사람 승인" in c for c in r.failed_criteria)


def test_ai_recommended_alone_does_not_promote():
    """AI 추천만 있고 human_approved=False면 LIVE 단계 BLOCKED."""
    r = evaluate_promotion(_passing_input(
        current=PromotionStage.PAPER, target=PromotionStage.LIVE_MANUAL_APPROVAL,
        ai_recommended=True, human_approved=False,
    ))
    assert r.decision == PromotionDecision.BLOCKED
    assert any("AI 추천" in w for w in r.warnings)


def test_human_approved_with_failed_criteria_still_fail():
    """human_approved=True여도 코드 기준 미달은 FAIL."""
    r = evaluate_promotion(_passing_input(
        current=PromotionStage.PAPER, target=PromotionStage.LIVE_MANUAL_APPROVAL,
        human_approved=True, profit_factor=0.8,  # 코드 기준 미달
    ))
    assert r.decision == PromotionDecision.FAIL


def test_ai_recommendation_accuracy_required_at_ai_assist():
    """LIVE_MANUAL → LIVE_AI_ASSIST는 AI accuracy 보고 필수."""
    r = evaluate_promotion(_passing_input(
        current=PromotionStage.LIVE_MANUAL_APPROVAL,
        target=PromotionStage.LIVE_AI_ASSIST,
        ai_recommendation_accuracy=None,
    ))
    assert r.decision == PromotionDecision.FAIL


def test_ai_recommendation_accuracy_below_threshold_fails():
    r = evaluate_promotion(_passing_input(
        current=PromotionStage.LIVE_MANUAL_APPROVAL,
        target=PromotionStage.LIVE_AI_ASSIST,
        ai_recommendation_accuracy=0.4,
    ))
    assert r.decision == PromotionDecision.FAIL


# ---------- LIVE_AI_EXECUTION 영구 BLOCKED ----------


def test_live_ai_execution_blocked_even_when_all_criteria_pass():
    """모든 코드 기준 + 사람 승인이 PASS여도 본 모듈은 BLOCKED 반환 — CLAUDE.md."""
    r = evaluate_promotion(_passing_input(
        current=PromotionStage.LIVE_AI_ASSIST,
        target=PromotionStage.LIVE_AI_EXECUTION,
        monte_carlo_risk_of_ruin=0.005,  # AI Exec 단계 1% 한도 통과
    ))
    assert r.decision == PromotionDecision.BLOCKED
    # 별도 옵트인 PR 필요 명시.
    assert any("ENABLE_AI_EXECUTION" in a for a in r.required_actions)


def test_live_ai_execution_with_high_ror_fails():
    """AI Execution 단계는 ROR 한도가 1% — 2%면 FAIL."""
    r = evaluate_promotion(_passing_input(
        current=PromotionStage.LIVE_AI_ASSIST,
        target=PromotionStage.LIVE_AI_EXECUTION,
        monte_carlo_risk_of_ruin=0.02,
    ))
    assert r.decision == PromotionDecision.FAIL


# ---------- mode_changed / live_flag_changed invariant ----------


def test_result_to_dict_invariant_no_mode_change():
    r = evaluate_promotion(_passing_input())
    d = r.to_dict()
    assert d["mode_changed"] is False
    assert d["live_flag_changed"] is False


def test_result_is_json_serializable():
    import json
    r = evaluate_promotion(_passing_input())
    json.dumps(r.to_dict())


# ---------- API smoke ----------


def _basic_payload(**overrides):
    base = {
        "strategy_name": "sma_crossover",
        "current_stage": "BACKTEST",
        "target_stage":  "LIVE_SHADOW",
        "trade_count": 200, "expectancy": 50.0, "profit_factor": 1.5,
        "max_drawdown": 500_000, "max_consecutive_losses": 3,
        "initial_cash": 10_000_000,
        "cost_adjusted": True, "slippage_adjusted": True,
        "walk_forward_passed": True, "walk_forward_recommendation": "PASS",
        "positive_fold_ratio": 0.75, "holdout_pnl": 100_000,
        "single_best_fold_pnl_share": 0.5,
        "monte_carlo_run": True, "monte_carlo_risk_of_ruin": 0.0,
        "monte_carlo_worst_5pct_mdd": 200_000,
        "data_quality_score": 85.0, "data_quality_grade": "GOOD",
        "shadow_days": 30, "human_approved": True,
        "partial_fill_audit_ok": True,
    }
    base.update(overrides)
    return base


def test_route_evaluate_pass_smoke(client):
    res = client.post("/api/governance/strategy-promotion/evaluate", json=_basic_payload())
    assert res.status_code == 200
    body = res.json()
    assert body["decision"] in ("PASS", "CAUTION", "FAIL", "BLOCKED")
    assert body["mode_changed"] is False
    assert body["live_flag_changed"] is False


def test_route_evaluate_fail_smoke(client):
    res = client.post(
        "/api/governance/strategy-promotion/evaluate",
        json=_basic_payload(profit_factor=0.8),
    )
    assert res.status_code == 200
    assert res.json()["decision"] == "FAIL"


def test_route_evaluate_unknown_stage_returns_400(client):
    res = client.post(
        "/api/governance/strategy-promotion/evaluate",
        json=_basic_payload(target_stage="VIBES"),
    )
    assert res.status_code == 400


def test_route_evaluate_skipping_stage_blocks(client):
    res = client.post(
        "/api/governance/strategy-promotion/evaluate",
        json=_basic_payload(current_stage="BACKTEST", target_stage="PAPER"),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["decision"] == "BLOCKED"
