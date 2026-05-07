"""Enhanced Agent Council members (224, MUST).

기존 10개 멤버(council.py) 위에 5개 추가:
  11. AgentCritic            — 다른 agent 판단의 약점 / 과신 / 모순 지적.
  12. ScenarioStressAgent    — 급락장·거래량 급감·슬리피지·뉴스반전 시나리오 stress.
  13. OperatorBriefingAgent  — 스마트폰 3줄 요약 생성.
  14. ReadinessAgent         — 오늘 자동운용 READY/CAUTION/BLOCKED 판단.
  15. StopLossGuardianAgent  — 보유 포지션 stop/trailing/time 청산 독립 감시.

모든 Agent는 deterministic — AI Key 없어도 mock output. 결과는 structured
JSON-friendly dict (Agent 내부적으로 AgentDecision로 wrap). chain_id를 받아
같은 의사결정 사슬에 묶을 수 있도록 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ai.agents.base import Agent, AgentDecision


# ---------- 11. AgentCritic ----------

class AgentCritic(Agent):
    """다른 Agent들의 출력을 비판한다.

    검출 패턴:
      - 과신:  confidence >= 90 AND meta가 비어 있음 → "evidence-light overconfidence"
      - 모순:  같은 chain 안에서 BUY와 SELL이 동시에 등장
      - 약점:  reasons가 1개 이하 (이유 부족)
    """
    name = "AgentCritic"

    def decide(self, *, member_decisions: list[AgentDecision] | None = None,
               **kwargs: Any) -> AgentDecision:
        members = list(member_decisions or [])
        weaknesses: list[str] = []

        decisions_by_kind: dict[str, list[str]] = {}
        for m in members:
            decisions_by_kind.setdefault(m.decision, []).append(m.agent_name)
            if (m.confidence or 0) >= 90 and not m.meta:
                weaknesses.append(f"overconfident:{m.agent_name}({m.confidence})")
            if len(m.reasons) <= 1 and (m.confidence or 0) >= 70:
                weaknesses.append(f"thin_reasoning:{m.agent_name}")

        # 같은 chain 안에서 BUY와 SELL이 동시에 = 모순
        if "BUY" in decisions_by_kind and "SELL" in decisions_by_kind:
            buy = ",".join(decisions_by_kind["BUY"])
            sell = ",".join(decisions_by_kind["SELL"])
            weaknesses.append(f"contradiction:BUY({buy})_vs_SELL({sell})")

        if not members:
            return AgentDecision(
                agent_name=self.name, decision="INFO",
                confidence=0,
                reasons=["no_members_to_critique"],
            )

        if weaknesses:
            return AgentDecision(
                agent_name=self.name, decision="WARN",
                confidence=70,
                reasons=weaknesses[:5],
                meta={"flagged_count": len(weaknesses)},
            )
        return AgentDecision(
            agent_name=self.name, decision="INFO",
            confidence=60,
            reasons=["no_weaknesses_detected"],
            meta={"flagged_count": 0},
        )


# ---------- 12. ScenarioStressAgent ----------

@dataclass
class ScenarioVerdict:
    """시나리오 stress 결과 — JSON-friendly."""
    overall_verdict:   str          # SURVIVES / FRAGILE / FAILS
    overall_score:     int          # 0-100
    scenarios:         dict[str, str] = field(default_factory=dict)
    reasons:           list[str]      = field(default_factory=list)


class ScenarioStressAgent(Agent):
    """이 판단이 4개 시나리오에서 살아남는지 평가.

    시나리오:
      - crash:      급락장 -5%
      - volume_dry: 거래량 급감
      - slippage:   슬리피지 확대 +1%
      - news_flip:  뉴스 반전 (sentiment 90 → 30)

    각 시나리오 verdict: PASS / WARN / FAIL.
    overall_verdict:
      - 모든 PASS → SURVIVES
      - WARN/FAIL 1개 이하 → FRAGILE
      - 그 외 → FAILS
    """
    name = "ScenarioStressAgent"

    def decide(
        self, *,
        notional: int,
        unrealized_pct: float = 0.0,
        sentiment: int = 50,
        regime: str = "any",
        **kwargs: Any,
    ) -> AgentDecision:
        scenarios: dict[str, str] = {}
        reasons: list[str] = []

        # crash: 큰 notional + 손실 진행 → FAIL
        if notional > 500_000 and unrealized_pct < 0:
            scenarios["crash"] = "FAIL"
            reasons.append("crash:large_position_already_losing")
        elif notional > 1_000_000:
            scenarios["crash"] = "WARN"
            reasons.append("crash:large_position_size")
        else:
            scenarios["crash"] = "PASS"

        # volume_dry: ranging regime + 큰 사이즈 = 빠져나오기 힘듦
        if "ranging" in (regime or "") and notional > 300_000:
            scenarios["volume_dry"] = "WARN"
            reasons.append("volume_dry:ranging_with_size")
        else:
            scenarios["volume_dry"] = "PASS"

        # slippage: notional 큰 비례 → 영향 큼
        if notional > 2_000_000:
            scenarios["slippage"] = "FAIL"
            reasons.append("slippage:notional_too_big")
        elif notional > 500_000:
            scenarios["slippage"] = "WARN"
            reasons.append("slippage:moderate_impact")
        else:
            scenarios["slippage"] = "PASS"

        # news_flip: 현재 high sentiment에 의존하는지
        if sentiment >= 70:
            scenarios["news_flip"] = "WARN"
            reasons.append("news_flip:depends_on_positive_sentiment")
        else:
            scenarios["news_flip"] = "PASS"

        verdict_counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
        for v in scenarios.values():
            verdict_counts[v] += 1

        score = 100 - 30 * verdict_counts["FAIL"] - 10 * verdict_counts["WARN"]
        score = max(0, min(100, score))

        if verdict_counts["FAIL"] >= 1:
            overall = "FAILS"
            decision = "WARN"
            confidence = 80
        elif verdict_counts["WARN"] <= 1:
            overall = "SURVIVES"
            decision = "INFO"
            confidence = 70
        else:
            overall = "FRAGILE"
            decision = "WARN"
            confidence = 65

        return AgentDecision(
            agent_name=self.name, decision=decision,
            confidence=confidence,
            reasons=reasons or [f"verdict:{overall}"],
            meta={
                "overall_verdict": overall,
                "overall_score": score,
                "scenarios": scenarios,
            },
        )


# ---------- 13. OperatorBriefingAgent ----------

class OperatorBriefingAgent(Agent):
    """스마트폰용 3줄 요약. 다른 agent들의 reasons / decision을 사람말로 압축."""
    name = "OperatorBriefingAgent"

    def decide(self, *, chief_decision: AgentDecision | None = None,
               readiness_label: str = "READY",
               regime: str | None = None,
               **kwargs: Any) -> AgentDecision:
        lines: list[str] = []
        if chief_decision is None:
            lines = ["Agent 결정 없음", "—", "—"]
        else:
            lines.append(f"Chief: {chief_decision.decision}")
            if regime:
                lines.append(f"장세: {regime}")
            elif chief_decision.reasons:
                lines.append(chief_decision.reasons[0][:30])
            else:
                lines.append("—")
            lines.append(f"준비도: {readiness_label}")

        return AgentDecision(
            agent_name=self.name, decision="INFO",
            confidence=50,
            reasons=lines,
            meta={"summary_lines": lines, "readiness_label": readiness_label},
        )


# ---------- 14. ReadinessAgent ----------

class ReadinessAgent(Agent):
    """오늘 자동운용 가능/주의/금지를 판정.

    READY: 모든 신호 정상.
    CAUTION: high volatility, agent disagreement, low confidence 중 하나.
    BLOCKED: emergency_stop, RiskOfficer REJECT, ScenarioStress FAILS 중 하나.
    """
    name = "ReadinessAgent"

    def decide(
        self, *,
        emergency_stop: bool = False,
        risk_officer_decision: str | None = None,
        scenario_verdict: str | None = None,
        market_volatility_pct: float = 0.0,
        agent_agreement_score: int = 100,    # 0-100
        chief_confidence: int = 70,
        **kwargs: Any,
    ) -> AgentDecision:
        reasons: list[str] = []

        # BLOCKED 우선 평가
        if emergency_stop:
            reasons.append("emergency_stop_on")
            return self._verdict("BLOCKED", reasons)
        if risk_officer_decision == "REJECT":
            reasons.append("risk_officer_rejected")
            return self._verdict("BLOCKED", reasons)
        if scenario_verdict == "FAILS":
            reasons.append("scenario_stress_fails")
            return self._verdict("BLOCKED", reasons)

        # CAUTION
        if market_volatility_pct >= 5.0:
            reasons.append(f"high_volatility:{market_volatility_pct:.1f}%")
        if agent_agreement_score < 60:
            reasons.append(f"low_agent_agreement:{agent_agreement_score}")
        if chief_confidence < 50:
            reasons.append(f"low_chief_confidence:{chief_confidence}")
        if reasons:
            return self._verdict("CAUTION", reasons)

        return self._verdict("READY", ["all_checks_passed"])

    @staticmethod
    def _verdict(label: str, reasons: list[str]) -> AgentDecision:
        decision_map = {"READY": "INFO", "CAUTION": "WARN", "BLOCKED": "REJECT"}
        confidence_map = {"READY": 75, "CAUTION": 70, "BLOCKED": 95}
        return AgentDecision(
            agent_name="ReadinessAgent",
            decision=decision_map[label],
            confidence=confidence_map[label],
            reasons=reasons,
            meta={"readiness_label": label},
        )


# ---------- 15. StopLossGuardianAgent ----------

class StopLossGuardianAgent(Agent):
    """포지션 보유 중 stop / trailing / time / risk 청산을 독립 감시.

    ExitTimingAgent와는 독립적 — 둘이 같은 권고를 내면 신뢰도가 올라간다.
    Guardian은 더 보수적: stop_loss는 -1.5%부터 ALERT, trailing은 peak에서
    -1% 후퇴 시 발동, time은 6시간 보유 후.
    """
    name = "StopLossGuardianAgent"

    def decide(
        self, *,
        unrealized_pct: float = 0.0,
        peak_pct: float = 0.0,            # 보유 중 최고 수익률
        holding_minutes: int = 0,
        risk_spike: bool = False,
        **kwargs: Any,
    ) -> AgentDecision:
        # risk_spike: 급격한 리스크 증가 (변동성 급등 등)가 있으면 즉시 LIQUIDATE
        if risk_spike:
            return AgentDecision(
                agent_name=self.name, decision="SELL",
                confidence=95,
                reasons=["risk_spike:liquidate_immediately"],
                meta={"action": "LIQUIDATE", "reason_code": "risk_spike"},
            )

        if unrealized_pct <= -1.5:
            return AgentDecision(
                agent_name=self.name, decision="SELL",
                confidence=90,
                reasons=[f"stop_loss:{unrealized_pct:.2f}%"],
                meta={"action": "STOP_LOSS", "reason_code": "stop_loss"},
            )

        # trailing: peak에서 1% 이상 후퇴
        retracement = peak_pct - unrealized_pct
        if peak_pct > 1.0 and retracement >= 1.0:
            return AgentDecision(
                agent_name=self.name, decision="SELL",
                confidence=85,
                reasons=[f"trailing:peak={peak_pct:.2f}%_now={unrealized_pct:.2f}%"],
                meta={"action": "TRAILING", "reason_code": "trailing"},
            )

        if holding_minutes >= 360:
            return AgentDecision(
                agent_name=self.name, decision="SELL",
                confidence=80,
                reasons=[f"time_exit:holding={holding_minutes}min"],
                meta={"action": "TIME_EXIT", "reason_code": "time_exit"},
            )

        return AgentDecision(
            agent_name=self.name, decision="HOLD",
            confidence=60,
            reasons=[f"guardian_ok:unrealized={unrealized_pct:.2f}%"],
            meta={"action": "HOLD"},
        )
