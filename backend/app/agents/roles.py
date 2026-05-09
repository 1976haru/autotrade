"""#51: Six role-specific agent skeletons.

각 Agent는 deterministic mock output을 반환 — AI Provider key 없이도
작동한다. 실 LLM 통합은 *별도 옵트인 PR* 후속 과제. 모든 Agent는:

- `app.agents.base.AgentBase` 상속
- `AgentOutput`만 반환 (broker 객체 X)
- `is_order_intent = False` + `can_execute_order = False` 불변
- broker / OrderExecutor / route_order 호출 0건 (정적 grep 가드)
- API Key / Secret을 인자로 받지 않음

자세한 contract: [`docs/agent_architecture.md`](../../../docs/agent_architecture.md).
"""

from __future__ import annotations

from typing import Any

from app.agents.base import (
    AgentBase,
    AgentContext,
    AgentDecision,
    AgentMetadata,
    AgentOutput,
    AgentRole,
)


# ====================================================================
# 1. ObserverAgent — 시장/데이터/상태 관찰
# ====================================================================


class ObserverAgent(AgentBase):
    """시장 데이터 / 운영 상태를 *관찰*만 하고 요약 보고. 결정 X."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="observer",
            role=AgentRole.OBSERVER,
            description="시장 데이터 / 운영 상태 / 시세 / 변동성을 관찰만 한다.",
            inputs=["market_state", "watchlist", "recent_signals"],
            outputs=["AgentOutput(decision=OBSERVE)"],
            forbidden=[
                "주문 결정 / 추천 / 승인 후보 생성 금지 (분석은 Analyst 이후)",
                "broker / OrderExecutor 호출 금지 (CLAUDE.md 절대 원칙 1, 2)",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        watchlist = list(context.watchlist or [])
        signals   = list(context.recent_signals or [])
        # deterministic mock — context의 raw 데이터를 요약만.
        return AgentOutput(
            role=AgentRole.OBSERVER,
            decision=AgentDecision.OBSERVE,
            summary=(
                f"observed {len(watchlist)} watchlist symbols + "
                f"{len(signals)} recent signals"
            ),
            reasons=[
                f"watchlist size = {len(watchlist)}",
                f"recent signals = {len(signals)}",
            ],
            confidence=None,  # Observer는 confidence 부여 X
            metadata={
                "watchlist_size": len(watchlist),
                "signal_count":   len(signals),
                "regime":         (context.market_state or {}).get("regime"),
            },
        )


# ====================================================================
# 2. AnalystAgent — 후보 종목 / 상황 분석
# ====================================================================


class AnalystAgent(AgentBase):
    """Observer가 만든 raw 데이터에서 *분석 의견*을 도출. 결정 X (RECOMMEND는
    StrategyResearcher / ExecutionRecommender가 담당)."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="analyst",
            role=AgentRole.ANALYST,
            description="후보 종목 / 상황 / 시그널 quality 분석 의견 산출.",
            inputs=["market_state", "watchlist", "recent_signals"],
            outputs=["AgentOutput(decision=ANALYZE)"],
            forbidden=[
                "주문 추천 / 승인 후보 생성 금지 (그건 ExecutionRecommender)",
                "broker 호출 금지",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        signals = list(context.recent_signals or [])
        # deterministic mock — 신호 수 기반 대략적 confidence.
        confidence = min(100, max(0, 30 + len(signals) * 5))
        notable = [s for s in signals if isinstance(s, dict)
                                          and s.get("strength", 0) >= 70]
        return AgentOutput(
            role=AgentRole.ANALYST,
            decision=AgentDecision.ANALYZE,
            summary=(
                f"{len(signals)} signals analyzed, "
                f"{len(notable)} above strength threshold (>=70)"
            ),
            reasons=[
                f"total signals: {len(signals)}",
                f"high-strength signals: {len(notable)}",
            ],
            confidence=confidence,
            metadata={
                "notable_signal_count": len(notable),
            },
        )


# ====================================================================
# 3. RiskAuditorAgent — 일일 손실 / 중복 / stale data / risk events
# ====================================================================


class RiskAuditorAgent(AgentBase):
    """RiskManager 상태와 audit summary를 보고 WARN / REJECT 발행.

    실제 거부는 `RiskManager.evaluate_order`가 내리며, 본 Agent는 *advisory*만
    — 운영자가 risk events를 한눈에 보도록 요약."""

    # 임계 (조정 가능, 별도 옵트인 PR로 노출 검토).
    DEFAULT_REJECT_PCT_OF_DAILY = 80   # daily_realized_pnl_pct 절대값이 80% 이상이면 REJECT
    DEFAULT_WARN_PCT_OF_DAILY   = 50

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="risk_auditor",
            role=AgentRole.RISK_AUDITOR,
            description=(
                "일일 손실 / 중복 주문 / stale data / 자동 emergency_stop 같은 "
                "risk events를 점검해 WARN 또는 REJECT 발행."
            ),
            inputs=["risk_state", "audit_summary"],
            outputs=[
                "AgentOutput(decision=WARN)",
                "AgentOutput(decision=REJECT)",
            ],
            forbidden=[
                "broker 호출 금지 — RiskManager가 실제 거부 결정",
                "주문 생성 금지",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        risk_state    = dict(context.risk_state or {})
        audit_summary = dict(context.audit_summary or {})
        flags: list[str] = []
        reasons: list[str] = []

        # 1) emergency_stop ON
        if risk_state.get("emergency_stop"):
            flags.append("emergency_stop_active")
            reasons.append("emergency_stop ON")
        # 2) daily PnL 위반 percentage (advisory 산식)
        loss_pct = float(risk_state.get("daily_loss_pct", 0) or 0)
        if loss_pct >= self.DEFAULT_REJECT_PCT_OF_DAILY:
            flags.append("daily_loss_critical")
            reasons.append(
                f"daily loss at {loss_pct}% of limit (>= "
                f"{self.DEFAULT_REJECT_PCT_OF_DAILY}%)"
            )
        elif loss_pct >= self.DEFAULT_WARN_PCT_OF_DAILY:
            flags.append("daily_loss_elevated")
            reasons.append(
                f"daily loss at {loss_pct}% of limit (>= "
                f"{self.DEFAULT_WARN_PCT_OF_DAILY}%)"
            )
        # 3) recent stale price events
        stale = int(audit_summary.get("stale_price_rejections", 0) or 0)
        if stale > 0:
            flags.append("stale_data_recent")
            reasons.append(f"{stale} stale-price rejections in window")
        # 4) duplicate detector
        dup = int(audit_summary.get("duplicate_rejections", 0) or 0)
        if dup > 0:
            flags.append("duplicate_orders_recent")
            reasons.append(f"{dup} duplicate-order rejections in window")

        # 결정: critical flag 있으면 REJECT, warn-level은 WARN, 없으면 OBSERVE
        # (NO_OP 대신 OBSERVE — Risk Auditor도 무신호 상태를 표명).
        is_critical = (
            "emergency_stop_active" in flags
            or "daily_loss_critical" in flags
        )
        if is_critical:
            decision = AgentDecision.REJECT
            summary = "risk REJECT — critical conditions detected"
        elif flags:
            decision = AgentDecision.WARN
            summary = f"risk WARN — {len(flags)} elevated condition(s)"
        else:
            decision = AgentDecision.OBSERVE
            summary = "no risk events"
            reasons.append("no critical or elevated risk flags")

        return AgentOutput(
            role=AgentRole.RISK_AUDITOR,
            decision=decision,
            summary=summary,
            reasons=reasons,
            confidence=None,
            risk_flags=flags,
            metadata={
                "daily_loss_pct": loss_pct,
                "stale_price_rejections": stale,
                "duplicate_rejections": dup,
            },
        )


# ====================================================================
# 4. StrategyResearcherAgent — 전략 / 백테스트 개선안 제안
# ====================================================================


class StrategyResearcherAgent(AgentBase):
    """전략 / 백테스트 메타데이터를 보고 *개선 제안* 작성.

    구현은 deterministic stub — backtest summary가 약하면 RECOMMEND, 강하면
    REPORT. 본 PR 시점 실제 backtest 통합은 후속 과제."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="strategy_researcher",
            role=AgentRole.STRATEGY_RESEARCHER,
            description="전략 성과 분석 + 개선안 제안 (REPORT / RECOMMEND).",
            inputs=["audit_summary", "extra (backtest summary)"],
            outputs=[
                "AgentOutput(decision=REPORT)",
                "AgentOutput(decision=RECOMMEND)",
            ],
            forbidden=[
                "broker 호출 금지",
                "전략 자동 활성화 / 비활성화 금지 — 운영자 수동 결정",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        bt = (context.extra or {}).get("backtest_summary") or {}
        win_rate    = float(bt.get("win_rate", 0) or 0)
        profit_factor = float(bt.get("profit_factor", 0) or 0)
        # 임계 — RECOMMEND 분기.
        recommend = (win_rate < 0.45) or (profit_factor < 1.0)
        if recommend:
            decision = AgentDecision.RECOMMEND
            summary  = (
                "strategy underperforming — recommend review "
                f"(win_rate={win_rate}, pf={profit_factor})"
            )
            reasons = [
                f"win_rate {win_rate} below 0.45 threshold"
                if win_rate < 0.45 else "win_rate ok",
                f"profit_factor {profit_factor} below 1.0"
                if profit_factor < 1.0 else "profit_factor ok",
            ]
        else:
            decision = AgentDecision.REPORT
            summary  = (
                f"strategy healthy (win_rate={win_rate}, pf={profit_factor})"
            )
            reasons = ["thresholds met"]
        return AgentOutput(
            role=AgentRole.STRATEGY_RESEARCHER,
            decision=decision,
            summary=summary,
            reasons=reasons,
            confidence=None,
            metadata={"backtest_summary": dict(bt)},
        )


# ====================================================================
# 5. ReportWriterAgent — 일일 / 주간 리포트
# ====================================================================


class ReportWriterAgent(AgentBase):
    """audit summary + risk summary + agent outputs를 합쳐 리포트 작성."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="report_writer",
            role=AgentRole.REPORT_WRITER,
            description="일일 / 주간 운영 리포트 작성. read-only.",
            inputs=["audit_summary", "risk_state", "extra (other agents output)"],
            outputs=["AgentOutput(decision=REPORT)"],
            forbidden=[
                "broker 호출 금지",
                "주문 추천 금지",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        audit = dict(context.audit_summary or {})
        risk  = dict(context.risk_state or {})
        total_orders   = int(audit.get("total_orders", 0) or 0)
        approved       = int(audit.get("approved", 0) or 0)
        rejected       = int(audit.get("rejected", 0) or 0)
        emergency_on   = bool(risk.get("emergency_stop"))
        return AgentOutput(
            role=AgentRole.REPORT_WRITER,
            decision=AgentDecision.REPORT,
            summary=(
                f"daily report — {total_orders} orders ({approved} approved / "
                f"{rejected} rejected), emergency_stop="
                f"{'ON' if emergency_on else 'OFF'}"
            ),
            reasons=[
                f"total: {total_orders}",
                f"approved: {approved}",
                f"rejected: {rejected}",
                f"emergency_stop: {emergency_on}",
            ],
            confidence=None,
            metadata={
                "total_orders":   total_orders,
                "approved":       approved,
                "rejected":       rejected,
                "emergency_stop": emergency_on,
            },
        )


# ====================================================================
# 6. ExecutionRecommenderAgent — 매수/매도 후보 (approval queue 후보 payload만)
# ====================================================================


class ExecutionRecommenderAgent(AgentBase):
    """매수/매도 후보를 *제안*만 한다 — approval queue 후보 payload 생성까지.

    절대 invariant:
    - **broker.place_order 호출 0건** — 본 Agent는 broker를 import하지 않는다.
    - **route_order 호출 0건** — caller가 별도 흐름에서 결정.
    - **`can_execute_order = False`** 불변 — `AgentOutput.__post_init__` 가드.
    - 후보 payload는 `approval_candidate` 필드에만 carry — *큐 등록 자체는*
      caller가 별도 흐름(예: `app.ai.assist.submit_candidate` #44)에서 수행.
    """

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="execution_recommender",
            role=AgentRole.EXECUTION_RECOMMENDER,
            description=(
                "매수/매도 후보 제안. approval queue 후보 payload만 생성 — "
                "직접 주문 / broker 호출 0건. 운영자 수동 승인 + RiskManager + "
                "PermissionGate + OrderExecutor를 거쳐야만 실제 주문이 broker로 "
                "진행된다."
            ),
            inputs=["market_state", "recent_signals", "watchlist"],
            outputs=["AgentOutput(decision=APPROVAL_CANDIDATE)"],
            forbidden=[
                "broker / OrderExecutor / route_order 호출 금지",
                "approval queue *등록* 금지 (caller 책임)",
                "AI key / Secret 사용 금지 — deterministic mock만",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        signals = list(context.recent_signals or [])
        # deterministic mock — 가장 강한 signal을 후보로 추천. 실 LLM 통합은
        # 후속 PR.
        if not signals:
            return AgentOutput(
                role=AgentRole.EXECUTION_RECOMMENDER,
                decision=AgentDecision.NO_OP,
                summary="no signals — no candidate emitted",
                reasons=["recent_signals is empty"],
                confidence=None,
            )
        # 가장 강한 신호 (deterministic)
        best = max(
            (s for s in signals if isinstance(s, dict)),
            key=lambda s: int(s.get("strength", 0) or 0),
            default=None,
        )
        if best is None:
            return AgentOutput(
                role=AgentRole.EXECUTION_RECOMMENDER,
                decision=AgentDecision.NO_OP,
                summary="no usable signal",
                reasons=["recent_signals have no dict entries"],
                confidence=None,
            )
        symbol     = best.get("symbol")
        side       = best.get("side", "BUY")
        strength   = int(best.get("strength", 0) or 0)
        confidence = int(best.get("confidence", strength) or strength)

        # approval_candidate payload — caller가 #44 ai.assist.submit_candidate
        # 같은 별도 흐름에 전달. 본 dataclass는 *주문 객체가 아니다*.
        candidate: dict[str, Any] = {
            "source":       "AGENT_EXECUTION_RECOMMENDER",
            "symbol":       symbol,
            "side":         side,
            "quantity":     1,            # 보수적 기본
            "order_type":   "MARKET",
            "confidence":   confidence,
            "supporting_reasons": list(best.get("reasons") or []),
            "opposing_reasons":   [],
            "risk_note":    None,
            "is_order_intent": False,    # candidate payload임을 명시
        }
        return AgentOutput(
            role=AgentRole.EXECUTION_RECOMMENDER,
            decision=AgentDecision.APPROVAL_CANDIDATE,
            summary=(
                f"approval candidate — {side} {symbol} "
                f"(confidence {confidence})"
            ),
            reasons=[
                f"selected best-strength signal: strength={strength}",
                "candidate is advisory; caller routes through approval queue",
            ],
            confidence=confidence,
            approval_candidate=candidate,
            metadata={"selected_signal": dict(best)},
        )


# ====================================================================
# Registry — 6개 표준 역할의 단일 인스턴스 dict
# ====================================================================


def build_default_registry() -> dict[AgentRole, AgentBase]:
    """6개 표준 역할의 단일 인스턴스 dict.

    deterministic mock — 같은 context에 같은 결과. 운영자가 새 역할을
    추가하려면 별도 PR로 본 dict에 등록한다.
    """
    return {
        AgentRole.OBSERVER:              ObserverAgent(),
        AgentRole.ANALYST:               AnalystAgent(),
        AgentRole.RISK_AUDITOR:          RiskAuditorAgent(),
        AgentRole.STRATEGY_RESEARCHER:   StrategyResearcherAgent(),
        AgentRole.REPORT_WRITER:         ReportWriterAgent(),
        AgentRole.EXECUTION_RECOMMENDER: ExecutionRecommenderAgent(),
    }


# ====================================================================
# Module invariants
# ====================================================================
#
# - 본 모듈은 broker / OrderExecutor / route_order / KIS / mock_broker /
#   permission.gate 어떤 모듈도 import하지 않는다 (정적 grep 가드).
# - 모든 Agent의 `AgentOutput`은 is_order_intent=False + can_execute_order=False
#   (dataclass __post_init__ 가드).
# - ExecutionRecommender의 `approval_candidate.is_order_intent`도 False —
#   payload는 *주문 객체가 아니다*.
