/**
 * #52 / 6-07 — AI 판단 설명 유도 (client-side, read-only).
 *
 * 백엔드 `app/agents/decision_explanation.py::build_decision_explanation` 의 미러.
 * episode.council(또는 council dict)에 *이미 있는* 필드에서 사람이 읽는 설명을
 * 유도한다. 누락 필드는 fallback 문구로 대체(구버전 episode 호환, 에러 0).
 *
 * 설명 전용 — 주문 신호가 아니며 실거래 권한이 아니다.
 */

export const ENTRY_REASON_FALLBACK = "진입 근거 미기록";
export const COUNTER_REASON_FALLBACK = "반대 근거 미기록";
export const EXIT_PLAN_FALLBACK = "청산 계획 미기록";
export const RISK_FLAGS_FALLBACK = "위험 플래그 없음";
export const RISK_VETO_FALLBACK = "RiskOfficer veto 없음";
export const SELL_REASON_FALLBACK = "매도 사유 미기록";
export const FINAL_REASON_FALLBACK = "최종 판단 이유 미기록";

const _ACTION_KO = { BUY: "매수", SELL: "매도", HOLD: "보류" };

function _num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function _votesOf(votes, signal) {
  return (Array.isArray(votes) ? votes : []).filter(
    (v) => v && String(v.signal) === signal && (_num(v.score) ?? 0) > 0,
  );
}

function _entryReason(finalAction, votes, reason) {
  if (finalAction === "HOLD") {
    return reason ? `보류 근거: ${String(reason).slice(0, 120)}` : "보류 — 강한 진입 신호 없음";
  }
  const supporting = _votesOf(votes, finalAction);
  if (supporting.length === 0) return ENTRY_REASON_FALLBACK;
  const names = supporting.map((v) => String(v.strategy)).join(", ");
  const actKo = _ACTION_KO[finalAction] || finalAction;
  const top = supporting.reduce((a, b) => ((_num(b.score) ?? 0) > (_num(a.score) ?? 0) ? b : a));
  const topReason = String(top.reason || "").trim();
  const base = `진입 근거: ${names} 가 같은 방향(${actKo}) 신호`;
  return topReason ? `${base} — ${topReason}` : base;
}

function _counterReason(finalAction, votes) {
  const opposing = (Array.isArray(votes) ? votes : []).filter((v) => {
    if (!v) return false;
    const sig = String(v.signal);
    return sig !== "HOLD" && sig !== String(finalAction) && (_num(v.score) ?? 0) > 0;
  });
  if (opposing.length === 0) return COUNTER_REASON_FALLBACK;
  return (
    "반대 근거: " +
    opposing
      .map((v) => {
        const r = String(v.reason || "").trim();
        return `${v.strategy}(${v.signal})` + (r ? `: ${r}` : "");
      })
      .join(" / ")
  );
}

function _exitPlanText(exitPlan, hasExitPlan) {
  if (!exitPlan || typeof exitPlan !== "object" || Object.keys(exitPlan).length === 0 || !hasExitPlan) {
    return EXIT_PLAN_FALLBACK;
  }
  const sl = exitPlan.stop_loss_pct ?? exitPlan.stop_loss;
  const tp = exitPlan.take_profit_pct ?? exitPlan.take_profit;
  const rr = exitPlan.risk_reward_ratio ?? exitPlan.risk_reward;
  const bits = [];
  if (sl != null) bits.push(`손절 ${sl}%`);
  if (tp != null) bits.push(`익절 ${tp}%`);
  if (rr != null) bits.push(`RR ${rr}`);
  if (exitPlan.trailing_stop) bits.push("트레일링 스탑");
  return bits.length ? `청산 계획: ${bits.join(", ")}` : EXIT_PLAN_FALLBACK;
}

function _riskFlagsText(riskFlags) {
  const flags = (Array.isArray(riskFlags) ? riskFlags : []).filter(Boolean).map(String);
  return flags.length ? `리스크 플래그: ${flags.join(", ")}` : RISK_FLAGS_FALLBACK;
}

function _riskVetoText(veto) {
  if (!veto || typeof veto !== "object" || !veto.veto_applied) return RISK_VETO_FALLBACK;
  const reason = String(veto.reason || veto.reason_code || "위험 플래그 초과");
  return `RiskOfficer veto: ${reason} → HOLD 강등`;
}

function _exitPlanValidationText(validation, finalAction, preAction) {
  if (!validation || typeof validation !== "object" || Object.keys(validation).length === 0) return "";
  if (validation.valid === false || (preAction === "BUY" && finalAction !== "BUY")) {
    const rc = validation.reason_code || "exit_plan invalid";
    return `BUY 차단: ${rc} (exit_plan 검증 실패)`;
  }
  return "";
}

function _sellReasonText(sellReason, finalAction) {
  if (finalAction !== "SELL") return "";
  if (!sellReason || typeof sellReason !== "object" || !sellReason.reason_code) return SELL_REASON_FALLBACK;
  const msg = String(sellReason.message || "").trim();
  return `SELL 사유: ${sellReason.reason_code}` + (msg ? ` — ${msg}` : "");
}

/** council dict → 설명 객체. council 없으면 안전 fallback. */
export function buildDecisionExplanation(council, { marketRegime = null, timePhase = null } = {}) {
  const c = council && typeof council === "object" ? council : {};
  const finalAction = String(c.final_action || "HOLD");
  const votes = Array.isArray(c.votes) ? c.votes : [];
  const reason = String(c.reason || "");
  const actKo = _ACTION_KO[finalAction] || finalAction;

  return {
    final_action: finalAction,
    final_action_ko: actKo,
    final_reason: reason || FINAL_REASON_FALLBACK,
    entry_reason: _entryReason(finalAction, votes, reason),
    counter_reason: _counterReason(finalAction, votes),
    exit_plan_text: _exitPlanText(c.exit_plan, Boolean(c.has_exit_plan)),
    risk_flags_text: _riskFlagsText(c.risk_flags),
    risk_veto_text: _riskVetoText(c.risk_veto_result),
    exit_plan_validation_text: _exitPlanValidationText(
      c.exit_plan_validation, finalAction, c.pre_exit_plan_action,
    ),
    sell_reason_text: _sellReasonText(c.sell_reason, finalAction),
    selected_strategies: Array.isArray(c.selected_strategies) ? c.selected_strategies.map(String) : [],
    quality_score: c.quality_score != null ? Number(c.quality_score) : null,
    confidence: _num(c.confidence),
    market_regime: String(marketRegime || c.market_regime || "UNKNOWN"),
    time_phase: String(timePhase || c.time_phase || "UNKNOWN"),
    explanation_summary: `최종 판단: ${actKo}(${finalAction}) — ${(reason || FINAL_REASON_FALLBACK).slice(0, 100)}`,
    is_order_signal: false,
    is_live_authorization: false,
    contains_secret: false,
  };
}

export default buildDecisionExplanation;
