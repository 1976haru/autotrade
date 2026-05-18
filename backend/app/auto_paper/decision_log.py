"""#4-10: Paper AI 판단 영구 로그 — `agent_decision_log` 테이블 재사용.

AI Paper 자동매매 흐름의 *모든* 판단을 `AgentDecisionLog` 한 줄로 영구화한다.
사후 분석 시 "왜 그런 판단을 했는가" 를 시계열로 추적할 수 있다.

## 기록 대상

- 4-07 PaperDecisionBridge 의 *각* PaperDecision (BUY / SELL / HOLD / EXIT / NO_OP)
- 4-09 Risk veto 결과 (각 decision metadata 에 carry)
- 4-08 Position sizing 결과 (각 decision metadata 에 carry)
- 4-05 PaperStartExplanation 의 verdict / market_regime / overfit_flag
- 4-03 OverfitWarning / 4-04 MarketRegime / 4-02 StrategyCombination 등 상위 단계

본 모듈은 *기록 전용* — 새 의사 결정을 만들지 않는다. AI 의 종료 직전 결정
state 를 그대로 영구화하는 *write-only* 어댑터.

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. **본 모듈은 *기록 전용*** — broker / OrderExecutor / route_order 호출 0건.
2. `mode="PAPER"` 고정 — 실거래 로그와 절대 혼동되지 않음 (정적 검사).
3. AgentDecisionLog row 의 `meta` JSON 에 *어떤 secret 도 carry 하지 않음* —
   API key / 계좌번호 / Anthropic Key / OpenAI Key 등은 sanitizer 로 제거.
4. INSERT only — DELETE / UPDATE 0건 (정적 grep 가드 — 4-10 PR 내).
5. 외부 HTTP / AI SDK / LLM import 0건.

## append-only 보장

- `record_bridge_report()` 는 매 호출마다 `BridgeReport.decisions` 의 각 row 를
  새 `AgentDecisionLog` row 로 INSERT — *수정 / 삭제 0건*.
- `query_paper_decision_log()` 는 read-only SELECT — `created_at` 역순 + limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.models import AgentDecisionLog


DECISION_LOG_SCHEMA_VERSION = "1.0"

# 본 모듈을 통해 작성된 모든 row 는 `mode="PAPER"`. 영구 invariant.
PAPER_DECISION_LOG_MODE = "PAPER"

# 본 모듈을 통해 작성된 row 의 `meta` 에 들어가는 *sentinel* — UI / 분석 시
# 이 row 가 4-10 paper bridge 흐름 출처임을 식별.
PAPER_DECISION_LOG_SOURCE = "paper_decision_bridge"


# ─────────────────────────────────────────────────────────────────────────────
# Secret sanitizer — meta JSON 에 우발적으로 secret 이 섞이지 않도록 fail-closed.
# ─────────────────────────────────────────────────────────────────────────────


_FORBIDDEN_META_KEYS = frozenset({
    "api_key", "app_key", "app_secret", "secret", "access_token",
    "kis_app_key", "kis_app_secret", "anthropic_api_key", "openai_api_key",
    "account_no", "account_number", "kis_account_no", "bearer", "token",
    "password", "passwd",
})

# 명백한 secret pattern — fail-closed.
_SECRET_VALUE_PATTERNS: list[re.Pattern] = [
    re.compile(r"^sk-[A-Za-z0-9_\-]{20,}$"),                  # OpenAI / Anthropic
    re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,}$"),
    re.compile(r"^ghp_[A-Za-z0-9_\-]{20,}$"),                 # GitHub PAT
    re.compile(r"^xox[abps]-[A-Za-z0-9_\-]{20,}$"),           # Slack
    re.compile(r"^eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}$"),  # JWT
]


class SecretInDecisionLogError(ValueError):
    """meta JSON 에 secret 의심값이 발견되면 raise — fail-closed."""


def _check_no_secret_keys(meta: dict[str, Any]) -> None:
    """forbidden key 가 있으면 raise — fail-closed."""
    for k in meta.keys():
        if not isinstance(k, str):
            continue
        if k.lower() in _FORBIDDEN_META_KEYS:
            raise SecretInDecisionLogError(
                f"decision_log meta key '{k}' is a secret field name"
            )


def _check_no_secret_values(meta: dict[str, Any]) -> None:
    """value 가 명백한 secret pattern 이면 raise."""
    for k, v in meta.items():
        if not isinstance(v, str):
            continue
        for pat in _SECRET_VALUE_PATTERNS:
            if pat.match(v):
                raise SecretInDecisionLogError(
                    f"decision_log meta['{k}'] matches secret pattern"
                )


def _sanitize_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """fail-closed 검사 후 *원본 그대로* 반환.

    redaction 이 아니라 *예외* — 호출자가 secret carry 시 즉시 차단.
    """
    if not isinstance(meta, dict):
        return {}
    _check_no_secret_keys(meta)
    _check_no_secret_values(meta)
    return dict(meta)


# ─────────────────────────────────────────────────────────────────────────────
# Agent name 결정 — source_module 에 따라 일관된 라벨.
# ─────────────────────────────────────────────────────────────────────────────


_SOURCE_AGENT_NAME = {
    "paper_decision_bridge": "PaperDecisionBridge",
    "paper_start_explanation": "PaperStartExplanation",
    "strategy_combination_recommender": "StrategyCombinationRecommender",
    "market_regime_agent": "MarketRegimeAgent",
    "overfit_warning_agent": "OverfitWarningAgent",
    "risk_veto": "RiskVeto",
    "position_sizer": "PositionSizer",
}


def _agent_name_for(source_module: str) -> str:
    return _SOURCE_AGENT_NAME.get(source_module, "PaperPipeline")


# ─────────────────────────────────────────────────────────────────────────────
# DTO — bridge → log row 변환의 *중간* 단계 (테스트 / API output 에 그대로 carry).
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PaperDecisionLogEntry:
    """단일 PaperDecision 의 로그 view — *read-only*, advisory."""

    decision_id:        str
    timestamp:          str       # ISO 8601 UTC
    agent_name:         str
    strategy:           str
    symbol:             str
    mode:               str
    decision_action:    str
    confidence:         int | None
    reason:             str
    reasons:            list[str]            = field(default_factory=list)
    risk_flags:         list[str]            = field(default_factory=list)
    market_regime:      str | None           = None
    overfit_flag:       bool                 = False
    risk_veto:          bool                 = False
    risk_veto_reasons:  list[str]            = field(default_factory=list)
    risk_veto_severity: str | None           = None
    position_size:      int                  = 0
    sizing_verdict:     str | None           = None
    paper_order_id:     str | None           = None
    paper_fill_status:  str | None           = None
    chain_id:           str | None           = None
    source_module:      str                  = PAPER_DECISION_LOG_SOURCE

    # 절대 invariant.
    is_order_signal:       bool = False
    auto_apply_allowed:    bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"PaperDecisionLogEntry.{name} must be False.")
        if self.mode != PAPER_DECISION_LOG_MODE:
            raise ValueError(
                f"PaperDecisionLogEntry.mode must be {PAPER_DECISION_LOG_MODE!r}, "
                f"got {self.mode!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id":        self.decision_id,
            "timestamp":          self.timestamp,
            "agent_name":         self.agent_name,
            "strategy":           self.strategy,
            "symbol":             self.symbol,
            "mode":               self.mode,
            "decision_action":    self.decision_action,
            "confidence":         self.confidence,
            "reason":             self.reason,
            "reasons":            list(self.reasons),
            "risk_flags":         list(self.risk_flags),
            "market_regime":      self.market_regime,
            "overfit_flag":       bool(self.overfit_flag),
            "risk_veto":          bool(self.risk_veto),
            "risk_veto_reasons":  list(self.risk_veto_reasons),
            "risk_veto_severity": self.risk_veto_severity,
            "position_size":      int(self.position_size),
            "sizing_verdict":     self.sizing_verdict,
            "paper_order_id":     self.paper_order_id,
            "paper_fill_status":  self.paper_fill_status,
            "chain_id":           self.chain_id,
            "source_module":      self.source_module,
            "is_order_signal":       False,
            "auto_apply_allowed":    False,
            "is_live_authorization": False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Row 빌더 — PaperDecision + 컨텍스트 → AgentDecisionLog row.
# ─────────────────────────────────────────────────────────────────────────────


def _decision_to_log_meta(
    *,
    decision_id:        str,
    paper_decision:     Any,    # PaperDecision (dataclass) — duck-typed to avoid circular import
    market_regime:      str | None,
    explanation_verdict: str | None,
    source_module:      str,
) -> dict[str, Any]:
    """PaperDecision + 컨텍스트 → AgentDecisionLog.meta JSON.

    risk_veto / sizing_verdict / sizing_quantity / overfit_verdict 정보는
    PaperDecision.metadata 가 carry. 본 함수는 그것을 *정규화* 만 한다.
    """
    src_meta = dict(paper_decision.metadata or {})

    risk_veto_raw = src_meta.get("risk_veto", False)
    risk_veto_bool = bool(risk_veto_raw) if isinstance(risk_veto_raw, bool) else False
    veto_reasons = list(src_meta.get("risk_veto_reasons") or [])
    veto_severity = src_meta.get("risk_veto_severity")

    sizing_verdict = src_meta.get("sizing_verdict")
    sizing_quantity_raw = src_meta.get("sizing_quantity")
    sizing_quantity = (
        int(sizing_quantity_raw)
        if isinstance(sizing_quantity_raw, (int, float)) else None
    )

    overfit_verdict = src_meta.get("overfit_verdict") or ""
    overfit_flag = bool(overfit_verdict and overfit_verdict.upper() == "OVERFIT_RISK")

    meta = {
        "decision_id":          decision_id,
        "schema_version":       DECISION_LOG_SCHEMA_VERSION,
        "source_module":        source_module,
        "paper_decision_action": str(paper_decision.action.value
                                     if hasattr(paper_decision.action, "value")
                                     else paper_decision.action),
        "paper_order_id":       paper_decision.paper_order_id,
        "paper_fill_status":    (paper_decision.paper_fill_status.value
                                 if hasattr(paper_decision.paper_fill_status, "value")
                                 else paper_decision.paper_fill_status),
        "virtual_position_delta": int(paper_decision.virtual_position_delta or 0),
        "pnl_estimate":         (
            float(paper_decision.pnl_estimate)
            if paper_decision.pnl_estimate is not None else None
        ),
        "source_direction":     paper_decision.source_direction,
        "risk_flags":           list(paper_decision.risk_flags or []),
        "market_regime":        market_regime,
        "explanation_verdict":  explanation_verdict,
        "overfit_verdict":      overfit_verdict,
        "overfit_flag":         overfit_flag,
        "risk_veto":            risk_veto_bool,
        "risk_veto_reasons":    veto_reasons,
        "risk_veto_severity":   veto_severity,
        "sizing_verdict":       sizing_verdict,
        "sizing_quantity":      sizing_quantity,
        "bridge_bucket":        src_meta.get("bridge_bucket"),
        "paper_candidate_status": src_meta.get("paper_candidate_status"),
        # 절대 invariant carry — JSON consumer 안전.
        "is_order_signal":       False,
        "auto_apply_allowed":    False,
        "is_live_authorization": False,
    }
    return _sanitize_meta(meta)


# ─────────────────────────────────────────────────────────────────────────────
# Main entry — record_bridge_report
# ─────────────────────────────────────────────────────────────────────────────


def record_bridge_report(
    db:             Session,
    *,
    bridge_report:  Any,    # BridgeReport — duck-typed
    explanation:    Any = None,   # PaperStartExplanation — for market_regime carry
    source_module:  str = PAPER_DECISION_LOG_SOURCE,
    chain_id:       str | None = None,
) -> list[AgentDecisionLog]:
    """`BridgeReport.decisions` 각 row 를 `AgentDecisionLog` 로 INSERT.

    *broker 호출 0건* — append-only DB write.

    Returns: INSERT 된 row 리스트 (테스트 / API 응답용).
    """
    if chain_id is None:
        chain_id = str(uuid4())

    agent_name = _agent_name_for(source_module)
    market_regime = (
        getattr(explanation, "market_regime", None) if explanation is not None
        else None
    )
    explanation_verdict = bridge_report.explanation_verdict

    rows: list[AgentDecisionLog] = []
    for d in bridge_report.decisions:
        decision_id = str(uuid4())
        meta = _decision_to_log_meta(
            decision_id=decision_id,
            paper_decision=d,
            market_regime=market_regime,
            explanation_verdict=explanation_verdict,
            source_module=source_module,
        )
        decision_action = str(
            d.action.value if hasattr(d.action, "value") else d.action
        )
        confidence_pct: int | None = None
        if d.confidence is not None:
            try:
                confidence_pct = int(round(float(d.confidence) * 100))
                # clamp to 0..100.
                confidence_pct = max(0, min(100, confidence_pct))
            except (TypeError, ValueError):
                confidence_pct = None
        reasons_list = [d.reason] if d.reason else []
        row = AgentDecisionLog(
            agent_name=agent_name,
            symbol=d.symbol,
            mode=PAPER_DECISION_LOG_MODE,
            decision=decision_action,
            confidence=confidence_pct,
            reasons=reasons_list,
            meta=meta,
            chain_id=chain_id,
        )
        db.add(row)
        rows.append(row)

    db.flush()
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Query — read-only.
# ─────────────────────────────────────────────────────────────────────────────


def _row_to_entry(row: AgentDecisionLog) -> PaperDecisionLogEntry:
    meta = dict(row.meta or {})
    return PaperDecisionLogEntry(
        decision_id=str(meta.get("decision_id") or row.id),
        timestamp=row.created_at.isoformat() if row.created_at else "",
        agent_name=row.agent_name,
        strategy=str(meta.get("strategy") or _strategy_from_chain(row, meta)),
        symbol=row.symbol or "",
        mode=row.mode,
        decision_action=row.decision,
        confidence=row.confidence,
        reason=(list(row.reasons or [None])[0] or "") if row.reasons else "",
        reasons=list(row.reasons or []),
        risk_flags=list(meta.get("risk_flags") or []),
        market_regime=meta.get("market_regime"),
        overfit_flag=bool(meta.get("overfit_flag", False)),
        risk_veto=bool(meta.get("risk_veto", False)),
        risk_veto_reasons=list(meta.get("risk_veto_reasons") or []),
        risk_veto_severity=meta.get("risk_veto_severity"),
        position_size=int(meta.get("sizing_quantity") or
                          meta.get("virtual_position_delta") or 0),
        sizing_verdict=meta.get("sizing_verdict"),
        paper_order_id=meta.get("paper_order_id"),
        paper_fill_status=meta.get("paper_fill_status"),
        chain_id=row.chain_id,
        source_module=str(meta.get("source_module") or PAPER_DECISION_LOG_SOURCE),
    )


def _strategy_from_chain(row: AgentDecisionLog, meta: dict[str, Any]) -> str:
    """strategy 는 본 모듈에서 row.symbol 외에 별도 컬럼이 없으므로 meta 우선,
    없으면 paper_decision_action prefix 로 fallback."""
    # bridge_bucket 보다는 explicit strategy 키가 있으면 사용.
    if isinstance(meta.get("strategy"), str):
        return meta["strategy"]
    return ""


def query_paper_decision_log(
    db:       Session,
    *,
    limit:    int = 50,
    strategy: str | None = None,
    symbol:   str | None = None,
    action:   str | None = None,
) -> list[PaperDecisionLogEntry]:
    """`AgentDecisionLog` 중 *Paper* mode + 본 모듈 source row 만 read-only.

    INSERT / UPDATE / DELETE 0건 — 정적 grep 가드.
    """
    q = (
        db.query(AgentDecisionLog)
        .filter(AgentDecisionLog.mode == PAPER_DECISION_LOG_MODE)
        .order_by(desc(AgentDecisionLog.created_at))
    )
    if symbol:
        q = q.filter(AgentDecisionLog.symbol == symbol)
    if action:
        q = q.filter(AgentDecisionLog.decision == action)
    rows = q.limit(max(1, min(int(limit), 1000))).all()
    entries: list[PaperDecisionLogEntry] = []
    for r in rows:
        try:
            entry = _row_to_entry(r)
        except ValueError:
            # 잘못된 mode / invariant 위반 row 는 skip — 본 모듈은 read-only.
            continue
        if strategy and entry.strategy and entry.strategy != strategy:
            continue
        entries.append(entry)
    return entries


def summarize_paper_decisions(
    entries: Iterable[PaperDecisionLogEntry],
) -> dict[str, Any]:
    """카드 / API 용 카운트 요약 — read-only."""
    by_action: dict[str, int] = {}
    veto_count = 0
    sizing_reduced = 0
    for e in entries:
        by_action[e.decision_action] = by_action.get(e.decision_action, 0) + 1
        if e.risk_veto:
            veto_count += 1
        if e.sizing_verdict and e.sizing_verdict == "REDUCED":
            sizing_reduced += 1
    return {
        "by_action":       by_action,
        "veto_count":      veto_count,
        "sizing_reduced":  sizing_reduced,
    }


__all__ = [
    "DECISION_LOG_SCHEMA_VERSION",
    "PAPER_DECISION_LOG_MODE",
    "PAPER_DECISION_LOG_SOURCE",
    "PaperDecisionLogEntry",
    "SecretInDecisionLogError",
    "record_bridge_report",
    "query_paper_decision_log",
    "summarize_paper_decisions",
]
