"""10-Agent Council implementations (185, MUST).

각 Agent는 deterministic stub — 실 LLM 호출 없이도 동작. AI Key 없어도
운영 가능. 외부 LLM 통합은 별도 옵트인 PR (LIVE 영역).

ChiefTradingAgent.coordinate()가 다른 9 agent를 호출 + 결과를 종합한다.
"""

from dataclasses import dataclass
from typing import Any

from app.ai.agents.base import Agent, AgentDecision, new_chain_id


# ---------- 1. MarketRegimeAgent ----------

class MarketRegimeAgent(Agent):
    """현재 시장 체제 분류. classify_regime(135) 결과를 카운슬에서 사용 가능한
    AgentDecision으로 wrap."""
    name = "MarketRegimeAgent"

    def decide(self, *, regime: str, sample_size: int, **kwargs: Any) -> AgentDecision:
        # confidence: 표본이 많을수록 높음. 60봉 이상은 max.
        conf = min(100, int((sample_size / 60.0) * 80) + 20)
        return AgentDecision(
            agent_name=self.name,
            decision="INFO",
            confidence=conf,
            reasons=[f"regime={regime}", f"sample_size={sample_size}"],
            meta={"regime": regime, "sample_size": sample_size},
        )


# ---------- 2. StrategySelectionAgent ----------

class StrategySelectionAgent(Agent):
    """현재 regime에 매칭되는 strategy 선택."""
    name = "StrategySelectionAgent"

    _PREFERRED: dict[str, str] = {
        "trending":      "sma_crossover",
        "trending_up":   "orb_vwap",
        "trending_down": "orb_vwap",
        "ranging":       "rsi_reversion",
        "high_vol":      "sma_crossover",
        "any":           "sma_crossover",
    }

    def decide(self, *, regime: str, **kwargs: Any) -> AgentDecision:
        chosen = self._PREFERRED.get(regime, "sma_crossover")
        return AgentDecision(
            agent_name=self.name,
            decision="INFO",
            confidence=70,
            reasons=[f"regime={regime}", f"chosen={chosen}"],
            meta={"strategy": chosen, "regime": regime},
        )


# ---------- 3. StockSelectionAgent ----------

class StockSelectionAgent(Agent):
    """주어진 후보 중 거래 대상 symbol 선택. 단순화: 첫 후보를 그대로 선택."""
    name = "StockSelectionAgent"

    def decide(self, *, candidates: list[str], **kwargs: Any) -> AgentDecision:
        if not candidates:
            return AgentDecision(
                agent_name=self.name, decision="HOLD", confidence=0,
                reasons=["no_candidates"],
            )
        chosen = candidates[0]
        return AgentDecision(
            agent_name=self.name,
            decision="INFO",
            confidence=60,
            reasons=[f"chosen={chosen}", f"n_candidates={len(candidates)}"],
            symbol=chosen,
            meta={"candidates": list(candidates)},
        )


# ---------- 4. PositionSizingAgent ----------

class PositionSizingAgent(Agent):
    """자본 + 권장 % → 수량. equity, price, risk_pct를 받아 quantity 추천."""
    name = "PositionSizingAgent"

    def decide(self, *, equity: int, price: int, risk_pct: float = 5.0,
                **kwargs: Any) -> AgentDecision:
        if price <= 0 or equity <= 0:
            return AgentDecision(
                agent_name=self.name, decision="HOLD", confidence=0,
                reasons=["invalid_inputs"],
            )
        target_notional = equity * risk_pct / 100.0
        qty = max(1, int(target_notional / price))
        return AgentDecision(
            agent_name=self.name,
            decision="INFO",
            confidence=65,
            reasons=[f"equity={equity}", f"risk_pct={risk_pct}", f"qty={qty}"],
            meta={"quantity": qty, "target_notional": int(target_notional)},
        )


# ---------- 5. RiskOfficerAgent ----------

class RiskOfficerAgent(Agent):
    """RiskPolicy 사전 검토 — RiskManager.evaluate_order 호출 전 advisory.
    실제 가드는 RiskManager가 수행 — 본 agent는 운영자 visibility용."""
    name = "RiskOfficerAgent"

    def decide(self, *, notional: int, max_order_notional: int,
                emergency_stop: bool, **kwargs: Any) -> AgentDecision:
        warnings: list[str] = []
        if emergency_stop:
            warnings.append("emergency_stop is ON")
        if notional > max_order_notional:
            warnings.append(f"notional {notional} > max_order_notional {max_order_notional}")
        if warnings:
            return AgentDecision(
                agent_name=self.name, decision="REJECT",
                confidence=95, reasons=warnings,
                meta={"warnings": warnings},
            )
        return AgentDecision(
            agent_name=self.name, decision="APPROVE",
            confidence=80, reasons=["preliminary_ok"],
        )


# ---------- 6. EntryTimingAgent ----------

class EntryTimingAgent(Agent):
    """진입 타이밍. last_close가 prev_close보다 위면 BUY, 아래면 HOLD."""
    name = "EntryTimingAgent"

    def decide(self, *, last_close: int, prev_close: int,
                **kwargs: Any) -> AgentDecision:
        if last_close > prev_close:
            return AgentDecision(
                agent_name=self.name, decision="BUY", confidence=70,
                reasons=[f"close_up:{last_close}_vs_{prev_close}"],
            )
        return AgentDecision(
            agent_name=self.name, decision="HOLD", confidence=60,
            reasons=[f"close_not_up:{last_close}_vs_{prev_close}"],
        )


# ---------- 7. ExitTimingAgent ----------

class ExitTimingAgent(Agent):
    """청산 타이밍. unrealized_pct로 stop_loss / take_profit / hold 권장."""
    name = "ExitTimingAgent"

    def decide(self, *, unrealized_pct: float,
                stop_loss_pct: float = 2.0,
                take_profit_pct: float = 5.0,
                **kwargs: Any) -> AgentDecision:
        if unrealized_pct <= -(stop_loss_pct / 100):
            return AgentDecision(
                agent_name=self.name, decision="SELL",
                confidence=90,
                reasons=[f"stop_loss:{unrealized_pct:.4f}"],
                meta={"reason_code": "stop_loss"},
            )
        if unrealized_pct >= (take_profit_pct / 100):
            return AgentDecision(
                agent_name=self.name, decision="SELL",
                confidence=85,
                reasons=[f"take_profit:{unrealized_pct:.4f}"],
                meta={"reason_code": "take_profit"},
            )
        return AgentDecision(
            agent_name=self.name, decision="HOLD",
            confidence=70,
            reasons=[f"within_band:{unrealized_pct:.4f}"],
        )


# ---------- 8. NewsTrendAgent ----------

class NewsTrendAgent(Agent):
    """뉴스/추세. 실 LLM 없이는 deterministic placeholder — 운영자가 명시 입력
    한 sentiment 점수를 그대로 surface."""
    name = "NewsTrendAgent"

    def decide(self, *, sentiment: int = 50, **kwargs: Any) -> AgentDecision:
        # 50 중립, < 30 부정, > 70 긍정.
        if sentiment >= 70:
            decision = "INFO"
            reasons = [f"positive_sentiment:{sentiment}"]
        elif sentiment <= 30:
            decision = "WARN"
            reasons = [f"negative_sentiment:{sentiment}"]
        else:
            decision = "INFO"
            reasons = [f"neutral_sentiment:{sentiment}"]
        return AgentDecision(
            agent_name=self.name, decision=decision,
            confidence=40,  # 실 LLM 없이는 confidence 낮음
            reasons=reasons,
            meta={"sentiment": sentiment},
        )


# ---------- 9. PostTradeReviewAgent ----------

class PostTradeReviewAgent(Agent):
    """사후 거래 분석. realized_pnl + win_rate를 받아 평가."""
    name = "PostTradeReviewAgent"

    def decide(self, *, realized_pnl: int, win_rate: float,
                trades: int, **kwargs: Any) -> AgentDecision:
        if trades < 5:
            return AgentDecision(
                agent_name=self.name, decision="INFO",
                confidence=30,
                reasons=[f"insufficient_sample:{trades}"],
                meta={"trades": trades},
            )
        if realized_pnl > 0 and win_rate >= 0.5:
            return AgentDecision(
                agent_name=self.name, decision="INFO",
                confidence=80,
                reasons=[f"positive_review:pnl={realized_pnl}", f"win_rate={win_rate:.2f}"],
                meta={"verdict": "good"},
            )
        return AgentDecision(
            agent_name=self.name, decision="WARN",
            confidence=70,
            reasons=[f"underperformance:pnl={realized_pnl}", f"win_rate={win_rate:.2f}"],
            meta={"verdict": "underperform"},
        )


# ---------- 10. ChiefTradingAgent (orchestrator) ----------

@dataclass
class CouncilContext:
    """Chief가 다른 agent에 전달할 시나리오 입력."""
    symbol:           str
    last_close:       int
    prev_close:       int
    equity:           int
    notional:         int
    regime:           str
    sample_size:      int = 60
    candidates:       list[str] | None = None
    risk_pct:         float = 5.0
    emergency_stop:   bool  = False
    max_order_notional: int = 1_000_000
    sentiment:        int   = 50
    unrealized_pct:   float = 0.0


class ChiefTradingAgent(Agent):
    """Council 종합 결정자. 9 agent의 출력을 받아 최종 BUY/SELL/HOLD."""
    name = "ChiefTradingAgent"

    def __init__(self) -> None:
        self.regime_agent   = MarketRegimeAgent()
        self.strategy_agent = StrategySelectionAgent()
        self.stock_agent    = StockSelectionAgent()
        self.size_agent     = PositionSizingAgent()
        self.risk_agent     = RiskOfficerAgent()
        self.entry_agent    = EntryTimingAgent()
        self.exit_agent     = ExitTimingAgent()
        self.news_agent     = NewsTrendAgent()
        self.post_agent     = PostTradeReviewAgent()

    def coordinate(
        self,
        ctx: CouncilContext,
        *,
        chain_id: str | None = None,
    ) -> tuple[AgentDecision, list[AgentDecision]]:
        """Council 호출 + 종합 결정 산출. (chief_decision, [member_decisions]) 반환.
        호출자가 모든 decision을 persist_decision으로 영구화 결정."""
        if chain_id is None:
            chain_id = new_chain_id()

        members: list[AgentDecision] = []

        regime  = self.regime_agent.decide(regime=ctx.regime, sample_size=ctx.sample_size)
        regime.chain_id = chain_id
        regime.symbol = ctx.symbol
        members.append(regime)

        strat = self.strategy_agent.decide(regime=ctx.regime)
        strat.chain_id = chain_id
        strat.symbol = ctx.symbol
        members.append(strat)

        stock = self.stock_agent.decide(candidates=ctx.candidates or [ctx.symbol])
        stock.chain_id = chain_id
        members.append(stock)

        size = self.size_agent.decide(
            equity=ctx.equity, price=ctx.last_close, risk_pct=ctx.risk_pct,
        )
        size.chain_id = chain_id
        size.symbol = ctx.symbol
        members.append(size)

        risk = self.risk_agent.decide(
            notional=ctx.notional, max_order_notional=ctx.max_order_notional,
            emergency_stop=ctx.emergency_stop,
        )
        risk.chain_id = chain_id
        risk.symbol = ctx.symbol
        members.append(risk)

        entry = self.entry_agent.decide(
            last_close=ctx.last_close, prev_close=ctx.prev_close,
        )
        entry.chain_id = chain_id
        entry.symbol = ctx.symbol
        members.append(entry)

        exit_ = self.exit_agent.decide(unrealized_pct=ctx.unrealized_pct)
        exit_.chain_id = chain_id
        exit_.symbol = ctx.symbol
        members.append(exit_)

        news = self.news_agent.decide(sentiment=ctx.sentiment)
        news.chain_id = chain_id
        news.symbol = ctx.symbol
        members.append(news)

        # PostTradeReview는 historical metric 받을 때만 의미 — 본 coordinate는
        # forward-looking이라 placeholder.
        post = self.post_agent.decide(realized_pnl=0, win_rate=0.0, trades=0)
        post.chain_id = chain_id
        post.symbol = ctx.symbol
        members.append(post)

        # 종합 규칙:
        # - risk_agent.decision == REJECT → 즉시 REJECT
        # - entry.decision == BUY + risk APPROVE + news != WARN → BUY
        # - exit.decision == SELL → SELL (청산 우선)
        # - 그 외 HOLD
        if risk.decision == "REJECT":
            chief_decision = "REJECT"
            chief_conf = max(risk.confidence or 0, 90)
            chief_reasons = ["chief:risk_rejected"] + risk.reasons
        elif exit_.decision == "SELL":
            chief_decision = "SELL"
            chief_conf = exit_.confidence or 70
            chief_reasons = ["chief:exit_sell"] + exit_.reasons
        elif entry.decision == "BUY" and news.decision != "WARN":
            chief_decision = "BUY"
            chief_conf = min(entry.confidence or 70, news.confidence or 70)
            chief_reasons = ["chief:entry_buy"] + entry.reasons
        else:
            chief_decision = "HOLD"
            chief_conf = 50
            chief_reasons = ["chief:no_action"]

        chief = AgentDecision(
            agent_name=self.name,
            decision=chief_decision,
            confidence=chief_conf,
            reasons=chief_reasons,
            symbol=ctx.symbol,
            chain_id=chain_id,
            meta={
                "regime": regime.meta.get("regime"),
                "strategy": strat.meta.get("strategy"),
                "qty": size.meta.get("quantity"),
                "exit_reason_code": exit_.meta.get("reason_code"),
            },
        )
        return chief, members

    def decide(self, **kwargs: Any) -> AgentDecision:
        """ABC 만족용. coordinate가 실제 진입점."""
        ctx = kwargs.get("ctx")
        if not isinstance(ctx, CouncilContext):
            raise TypeError("ChiefTradingAgent.decide requires ctx=CouncilContext")
        chief, _ = self.coordinate(ctx, chain_id=kwargs.get("chain_id"))
        return chief
