"""P-32: 이벤트 로그 품질 점검 — decision episode 기록 정합성 진단 (read-only).

판단(episode) → 주문(broker_order_no/audit_id/decision_log_id) → 체결(order_quality)
→ 성과(outcome) → 복기(review) → 포트폴리오(portfolio_delta) 가 서로 누락 없이
연결돼 있는지 진단해 정합성 점수와 이슈 목록을 만든다. 기록이 불완전하면 성능
개선 데이터로 신뢰할 수 없으므로 이를 먼저 가시화한다.

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *진단 전용* — broker / OrderExecutor / route_order / 외부 HTTP /
  실 계좌 잔고 조회 import 0건. **DB write 0건 (read-only)**.
- 진단 결과만으로 자동 주문 중단/재개를 *수행하지 않는다* (별도 정책).
- secret / API key / 계좌번호 carry 0건 — episode 에 금지 키가 발견되면 *값을
  노출하지 않고* SECURITY 이슈로만 표기.
- `EventIntegrityReport.is_live_authorization=False` / `contains_secret=False`
  / `uses_account_balance=False` 영구. 결정적(deterministic).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 심각도.
SEV_CRITICAL = "CRITICAL"
SEV_HIGH     = "HIGH"
SEV_WARN     = "WARN"
SEV_INFO     = "INFO"

# 카테고리.
CAT_LINKAGE      = "LINKAGE"
CAT_ORDER        = "ORDER"
CAT_PORTFOLIO    = "PORTFOLIO"
CAT_DATA_QUALITY = "DATA_QUALITY"
CAT_SECURITY     = "SECURITY"

# 점수 가중 (penalty).
_PENALTY = {SEV_CRITICAL: 25.0, SEV_HIGH: 10.0, SEV_WARN: 3.0, SEV_INFO: 0.0}

# 4 전략 canonical.
_CANON_STRATEGIES = {"ORB", "MOMENTUM", "GAP", "VWAP"}

# 금지 키 (값 노출 없이 존재만 검출).
_FORBIDDEN_KEYS = frozenset({
    "kis_app_key", "kis_app_secret", "app_key", "app_secret", "api_key",
    "secret", "account_no", "access_token", "refresh_token", "password",
    "authorization", "bearer", "token", "private_key", "client_secret",
})


def _norm(k: Any) -> str:
    return str(k).strip().lower().replace("-", "_").replace(" ", "_")


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@dataclass(frozen=True)
class EventIntegrityIssue:
    """단일 정합성 이슈 — secret 값 carry 0건."""

    severity:        str
    category:        str
    code:            str
    message:         str
    episode_id:      str | None = None
    suggested_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity":         self.severity,
            "category":         self.category,
            "code":             self.code,
            "message":          self.message,
            "episode_id":       self.episode_id,
            "suggested_action": self.suggested_action,
        }


@dataclass(frozen=True)
class EventIntegrityReport:
    """이벤트 정합성 종합 리포트 — 진단 전용. 자동 주문 중단/주문 신호 아님."""

    lookback_days:     int
    total_episodes:    int
    checked_episodes:  int
    integrity_score:   float
    safe_for_analysis: bool
    safe_for_paper_gate: bool
    issue_counts:      dict[str, int]
    by_category:       dict[str, int]
    issues:            list[dict[str, Any]] = field(default_factory=list)
    top_issues:        list[dict[str, Any]] = field(default_factory=list)
    note:              str = ""

    contains_secret:       bool = False
    uses_account_balance:  bool = False
    is_order_signal:       bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        if self.contains_secret is not False:
            raise ValueError("EventIntegrityReport.contains_secret must be False")
        if self.uses_account_balance is not False:
            raise ValueError("EventIntegrityReport.uses_account_balance must be False")
        if self.is_order_signal is not False:
            raise ValueError("EventIntegrityReport.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("EventIntegrityReport.is_live_authorization must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "lookback_days":     self.lookback_days,
            "total_episodes":    self.total_episodes,
            "checked_episodes":  self.checked_episodes,
            "integrity_score":   self.integrity_score,
            "safe_for_analysis": self.safe_for_analysis,
            "safe_for_paper_gate": self.safe_for_paper_gate,
            "issue_counts":      dict(self.issue_counts),
            "by_category":       dict(self.by_category),
            "issues":            list(self.issues),
            "top_issues":        list(self.top_issues),
            "note":              self.note,
            "contains_secret":       False,
            "uses_account_balance":  False,
            "is_order_signal":       False,
            "is_live_authorization": False,
            "advisory_disclaimer": (
                "이 진단은 이벤트 기록 정합성 확인용이며 주문 신호가 아닙니다. "
                "자동 주문 중단은 별도 정책으로만 처리됩니다. 실제 계좌정보를 "
                "사용하지 않습니다."
            ),
        }


def _find_forbidden_keys(obj: Any) -> bool:
    """dict/list 재귀 — 금지 키 존재 여부만 반환 (값 노출 없음)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _norm(k) in _FORBIDDEN_KEYS:
                return True
            if _find_forbidden_keys(v):
                return True
    elif isinstance(obj, (list, tuple)):
        return any(_find_forbidden_keys(x) for x in obj)
    return False


def _submitted(ep: dict[str, Any]) -> bool:
    kor = ep.get("kis_order_result")
    return bool(isinstance(kor, dict) and kor.get("submitted")) or bool(ep.get("broker_order_no"))


def _dry_run(ep: dict[str, Any]) -> bool:
    kor = ep.get("kis_order_result")
    rc = str(ep.get("reason_code", "") or "").upper()
    return bool(isinstance(kor, dict) and kor.get("dry_run")) or "DRY_RUN" in rc


def _order_quality(ep: dict[str, Any]) -> dict[str, Any] | None:
    kor = ep.get("kis_order_result")
    if not isinstance(kor, dict):
        return None
    q = kor.get("order_quality")
    return q if isinstance(q, dict) else None


def _diagnose_episode(ep: dict[str, Any]) -> list[EventIntegrityIssue]:
    out: list[EventIntegrityIssue] = []
    eid = ep.get("episode_id")

    def _issue(sev, cat, code, msg, action=""):
        out.append(EventIntegrityIssue(severity=sev, category=cat, code=code,
                                       message=msg, episode_id=eid,
                                       suggested_action=action))

    # ── 보안 invariant (먼저) ──
    if _find_forbidden_keys(ep):
        _issue(SEV_CRITICAL, CAT_SECURITY, "secret_key_present",
            "episode 기록에 금지 키(secret/account/api_key 류)가 포함됨",
            "저장 단계 sanitize 누락 점검 — 즉시 차단 필요")
    if ep.get("is_live_authorization") is True:
        _issue(SEV_CRITICAL, CAT_SECURITY, "live_authorization_true",
            "episode.is_live_authorization 가 True (불변 위반)",
            "기록 경로 점검 — 항상 False 여야 함")

    # ── 데이터 품질 ──
    if not eid:
        _issue(SEV_CRITICAL, CAT_LINKAGE, "episode_id_missing",
            "episode_id 누락 — 판단 사슬 추적 불가", "episode_id 발급 경로 점검")
    action = str(ep.get("final_action", "") or "").upper()
    if action not in ("BUY", "SELL", "HOLD"):
        _issue(SEV_HIGH, CAT_DATA_QUALITY, "final_action_missing",
            f"final_action 누락/비정상: {action or '(없음)'}", "council 결정 기록 점검")

    ms = ep.get("market_snapshot")
    msum = ep.get("market_summary") or {}
    no_market = (not isinstance(ms, dict) or not ms) and \
        str(msum.get("data_status", "")).upper() in ("", "NO_MARKET_DATA")
    if no_market:
        _issue(SEV_WARN, CAT_DATA_QUALITY, "market_snapshot_missing",
            "market_snapshot 누락 — 시장 맥락 분석 불가", "시세 스냅샷 기록 점검")

    votes = ep.get("votes")
    vote_strats = ({str(v.get("strategy", "")).upper()
                    for v in votes if isinstance(v, dict)} if isinstance(votes, list) else set())
    if not _CANON_STRATEGIES.issubset(vote_strats):
        missing = ", ".join(sorted(_CANON_STRATEGIES - vote_strats)) or "(전부)"
        _issue(SEV_WARN, CAT_DATA_QUALITY, "strategy_votes_incomplete",
            f"4전략 vote 누락: {missing}", "vote 기록(ORB/MOMENTUM/GAP/VWAP) 점검")

    # ── 주문 연결성 ──
    submitted = _submitted(ep)
    dry = _dry_run(ep)
    q = _order_quality(ep)
    if submitted and action in ("BUY", "SELL"):
        if not ep.get("broker_order_no") and not dry:
            _issue(SEV_HIGH, CAT_ORDER, "broker_order_no_missing",
                "주문 제출됐는데 broker_order_no 누락", "체결 응답 매핑 점검")
        if ep.get("decision_log_id") is None:
            _issue(SEV_HIGH, CAT_LINKAGE, "decision_log_unlinked",
                "주문 episode 인데 AgentDecisionLog(decision_log_id) 미연결",
                "AgentDecisionLog chain_id 연결 점검")
        if ep.get("audit_id") is None and not dry:
            _issue(SEV_WARN, CAT_LINKAGE, "audit_unlinked",
                "주문 episode 인데 audit_id(OrderAuditLog) 미연결",
                "OrderAuditLog 연결 점검")
        if q is None:
            _issue(SEV_HIGH, CAT_ORDER, "order_quality_missing",
                "주문 제출됐는데 order_quality 누락", "주문품질 로그 기록 점검")

    if q is not None:
        st = str(q.get("order_status", "") or "").upper()
        if st in ("FILLED", "PARTIALLY_FILLED") and q.get("avg_fill_price") in (None, 0):
            _issue(SEV_WARN, CAT_ORDER, "fill_price_missing",
                f"{st} 인데 avg_fill_price 누락", "체결가 기록 점검")
        if st == "REJECTED" and not (q.get("rejection_reason_code")
                                     or q.get("rejection_reason_message")):
            _issue(SEV_WARN, CAT_ORDER, "rejection_reason_missing",
                "REJECTED 인데 거절 사유 누락", "거절 사유 기록 점검")
        for fld in ("latency_ms", "slippage_bps"):
            v = q.get(fld)
            if v is not None and not _is_num(v):
                _issue(SEV_WARN, CAT_DATA_QUALITY, f"{fld}_type_error",
                    f"order_quality.{fld} 타입 오류: {type(v).__name__}",
                    "수치 필드 직렬화 점검")

    # ── 성과 / 복기 / 매도사유 ──
    outcome = ep.get("outcome") if isinstance(ep.get("outcome"), dict) else {}
    o_status = str(outcome.get("status", "") or "").upper()
    if action in ("BUY", "SELL"):
        if not outcome:
            _issue(SEV_INFO, CAT_DATA_QUALITY, "outcome_pending",
                "BUY/SELL 인데 outcome 미기록 (성과 라벨링 대기)",
                "사후 성과 라벨링(P-25) 누적 대기")
        elif o_status == "COMPLETE":
            rv = ep.get("review")
            if not (isinstance(rv, dict) and rv.get("review_status")):
                _issue(SEV_WARN, CAT_DATA_QUALITY, "review_missing",
                    "outcome COMPLETE 인데 복기(review) 누락",
                    "PostTradeReview(P-27) 실행 점검")
    if action == "SELL":
        sr = (ep.get("council") or {}).get("sell_reason")
        if not (isinstance(sr, dict) and sr.get("reason_code")):
            _issue(SEV_WARN, CAT_DATA_QUALITY, "sell_reason_missing",
                "SELL 인데 sell_reason 누락", "매도 사유 기록(P-26) 점검")

    # ── 포트폴리오 정합성 ──
    pd = ep.get("portfolio_delta") if isinstance(ep.get("portfolio_delta"), dict) else None
    st = str((q or {}).get("order_status", "") or "").upper()
    filled = st in ("FILLED", "PARTIALLY_FILLED")
    rejected = st in ("REJECTED", "CANCELLED") or str(ep.get("reason_code", "")).upper() in (
        "BLOCKED_BY_RISK_MANAGER", "BLOCKED_BY_PERMISSION_GATE")
    if filled and action in ("BUY", "SELL") and not pd:
        _issue(SEV_WARN, CAT_PORTFOLIO, "filled_not_in_portfolio",
            f"FILLED {action} 인데 portfolio_delta 미반영", "포트폴리오 반영 경로 점검")
    if rejected and pd and _delta_nonzero(pd):
        _issue(SEV_HIGH, CAT_PORTFOLIO, "rejected_in_portfolio",
            "거절/차단 주문이 portfolio_delta 에 반영됨(오류)", "반영 조건 점검")
    if pd:
        cash = _first_num(pd, ("cash_after", "cash", "current_cash"))
        if cash is not None and cash < 0:
            _issue(SEV_CRITICAL, CAT_PORTFOLIO, "negative_cash",
                "current cash 가 음수 — 자금 정합성 위반", "체결/차감 로직 점검")
        qty = _first_num(pd, ("position_qty", "quantity", "position_quantity"))
        if qty is not None and qty < 0:
            _issue(SEV_CRITICAL, CAT_PORTFOLIO, "negative_position",
                "position 수량이 음수 — 포지션 정합성 위반", "포지션 갱신 로직 점검")
    return out


def _delta_nonzero(pd: dict[str, Any]) -> bool:
    for k in ("quantity", "position_qty", "cash_delta", "filled_quantity"):
        v = pd.get(k)
        if _is_num(v) and v != 0:
            return True
    return False


def _first_num(pd: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        v = pd.get(k)
        if _is_num(v):
            return float(v)
    return None


def diagnose_episodes(
    episodes: list[dict[str, Any]], *, lookback_days: int = 7,
) -> EventIntegrityReport:
    """episode dict 목록 → 정합성 리포트 (deterministic, 예외 0, read-only).

    중복 episode_id 도 검출. integrity_score = 100 - 가중 penalty (clamp 0~100).
    """
    episodes = [e for e in (episodes or []) if isinstance(e, dict)]
    all_issues: list[EventIntegrityIssue] = []

    # 중복 episode_id 검출.
    seen: dict[str, int] = {}
    for e in episodes:
        eid = e.get("episode_id")
        if eid:
            seen[eid] = seen.get(eid, 0) + 1
    for eid, c in seen.items():
        if c > 1:
            all_issues.append(EventIntegrityIssue(
                severity=SEV_HIGH, category=CAT_LINKAGE, code="duplicate_episode_id",
                message=f"중복 episode_id: {eid} ({c}건)", episode_id=eid,
                suggested_action="episode_id 유일성 점검"))

    for e in episodes:
        all_issues.extend(_diagnose_episode(e))

    counts = {SEV_CRITICAL: 0, SEV_HIGH: 0, SEV_WARN: 0, SEV_INFO: 0}
    by_cat: dict[str, int] = {}
    penalty = 0.0
    for iss in all_issues:
        counts[iss.severity] = counts.get(iss.severity, 0) + 1
        by_cat[iss.category] = by_cat.get(iss.category, 0) + 1
        penalty += _PENALTY.get(iss.severity, 0.0)

    score = round(max(0.0, min(100.0, 100.0 - penalty)), 2)
    safe_analysis = counts[SEV_CRITICAL] == 0 and score >= 70.0
    safe_paper_gate = counts[SEV_CRITICAL] == 0 and counts[SEV_HIGH] == 0 and score >= 85.0

    # top issues — severity 우선순위 정렬.
    order = {SEV_CRITICAL: 0, SEV_HIGH: 1, SEV_WARN: 2, SEV_INFO: 3}
    sorted_issues = sorted(all_issues, key=lambda i: (order.get(i.severity, 9), i.code))
    issues_d = [i.to_dict() for i in sorted_issues]

    return EventIntegrityReport(
        lookback_days=int(lookback_days),
        total_episodes=len(episodes),
        checked_episodes=len(episodes),
        integrity_score=score,
        safe_for_analysis=safe_analysis,
        safe_for_paper_gate=safe_paper_gate,
        issue_counts=counts,
        by_category=by_cat,
        issues=issues_d,
        top_issues=issues_d[:5],
        note=("정합성 양호 — 분석 데이터로 사용 가능." if safe_analysis
              else "정합성 이슈 발견 — 성능 분석 신뢰도 저하 가능. 자동 주문 중단은 별도 정책."),
    )


def run_event_integrity_diagnostics(
    db: Any, *, lookback_days: int = 7, limit: int = 2000,
) -> EventIntegrityReport:
    """DB 의 최근 episode 를 read-only 로 로드해 정합성 진단.

    DB write 0건 — list_episodes(SELECT) 만 사용. broker / 실 계좌 조회 0건.
    """
    from app.agents.decision_episode import list_episodes
    episodes = list_episodes(db, limit=limit)
    return diagnose_episodes(episodes, lookback_days=lookback_days)


__all__ = [
    "EventIntegrityIssue", "EventIntegrityReport",
    "diagnose_episodes", "run_event_integrity_diagnostics",
    "SEV_CRITICAL", "SEV_HIGH", "SEV_WARN", "SEV_INFO",
    "CAT_LINKAGE", "CAT_ORDER", "CAT_PORTFOLIO", "CAT_DATA_QUALITY", "CAT_SECURITY",
]
