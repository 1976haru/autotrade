"""SignalReason 모델 + helper (#33).

`SignalReason`은 의사 결정 사슬의 한 단계가 만든 단일 근거(=reason)다.
여러 reason을 모은 `SignalExplanation`이 운영자/Agent/감사가 보는 패널의
원천 데이터.

본 모듈은 broker / RiskManager / PermissionGate / OrderExecutor 어떤 함수도
호출하지 않는다. 기존 audit/approval 테이블 스키마도 변경하지 않으며, 단지
`extract_reasons_from_audit_row`로 OrderAuditLog row에서 explanation을
*read-only* 합성한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable


# ---------- enums ----------


class ReasonCategory(StrEnum):
    """reason의 출처 단계 (의사 결정 사슬)."""
    STRATEGY        = "STRATEGY"
    SIGNAL_QUALITY  = "SIGNAL_QUALITY"
    MARKET_REGIME   = "MARKET_REGIME"
    RISK_MANAGER    = "RISK_MANAGER"
    PERMISSION_GATE = "PERMISSION_GATE"
    DATA_FRESHNESS  = "DATA_FRESHNESS"
    AGENT           = "AGENT"
    OPERATOR        = "OPERATOR"
    OTHER           = "OTHER"


class ReasonStatus(StrEnum):
    """단일 reason의 결과 — UI 배지 색과 매핑."""
    PASS    = "PASS"     # 조건 통과 (green)
    WARN    = "WARN"     # 통과는 했으나 운영자/Agent가 살펴야 함 (amber)
    FAIL    = "FAIL"     # 조건 미충족 (red)
    BLOCKED = "BLOCKED"  # 안전 가드 차단 (red, 강조)
    INFO    = "INFO"     # 단순 정보 (neutral)


class ReasonSeverity(StrEnum):
    """reason의 심각도 — 같은 FAIL이라도 priority 분기에 사용."""
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


class ExplainStatus(StrEnum):
    """explanation 전체의 최종 상태 — UI / Agent / Audit가 한눈에 본다."""
    APPROVED = "APPROVED"  # 모든 PASS, 주문 결정 통과
    PENDING  = "PENDING"   # PermissionGate가 승인 대기
    REJECTED = "REJECTED"  # 어떤 단계에서든 FAIL/BLOCKED
    WATCH    = "WATCH"     # 신호는 있으나 진입 보류
    UNKNOWN  = "UNKNOWN"   # explanation 미충분


# ---------- 데이터클래스 ----------


@dataclass(frozen=True)
class SignalReason:
    """의사 결정 한 단계가 만든 단일 근거.

    `category` = 어느 단계가 만들었는가. `status` = 그 단계의 결과.
    `severity` = 같은 FAIL이라도 우선순위 — UI/Agent가 정렬에 사용.
    `source` = 자유 문자열 — 모듈/함수 이름 등. `message` = 사람이 읽는
    요약. `code` = 운영자/Agent가 검색/필터에 쓰는 머신 가독 코드.
    `details` = 자유 dict — 지표 raw 값.
    """
    category: ReasonCategory
    status:   ReasonStatus
    message:  str
    severity: ReasonSeverity = ReasonSeverity.MEDIUM
    source:   str | None = None
    code:     str | None = None
    details:  dict | None = None

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "status":   self.status.value,
            "severity": self.severity.value,
            "source":   self.source,
            "code":     self.code,
            "message":  self.message,
            "details":  dict(self.details) if self.details else None,
        }


@dataclass(frozen=True)
class SignalExplanation:
    """여러 SignalReason을 모은 한 신호의 전체 설명.

    UI Explainability Panel + Agent 분석 + Audit 모두가 이 객체를 소비.
    `final_status`는 `classify_final_status`가 자동 산출하지만 호출자가
    override 가능 (운영자가 명시 결정한 경우).
    """
    reasons:        list[SignalReason]
    final_status:   ExplainStatus
    summary:        str
    audit_trace_id: int | None = None
    symbol:         str | None = None
    strategy:       str | None = None
    action:         str | None = None
    indicators:     dict | None = None
    risk_notes:     list[str] = field(default_factory=list)
    operator_note:  str | None = None

    def to_dict(self) -> dict:
        return {
            "reasons":        [r.to_dict() for r in self.reasons],
            "final_status":   self.final_status.value,
            "summary":        self.summary,
            "audit_trace_id": self.audit_trace_id,
            "symbol":         self.symbol,
            "strategy":       self.strategy,
            "action":         self.action,
            "indicators":     dict(self.indicators) if self.indicators else None,
            "risk_notes":     list(self.risk_notes),
            "operator_note":  self.operator_note,
        }

    def grouped_by_status(self) -> dict[str, list[SignalReason]]:
        """UI에서 PASS/WARN/FAIL/BLOCKED/INFO 별로 카드를 나누는 데 사용."""
        out: dict[str, list[SignalReason]] = {s.value: [] for s in ReasonStatus}
        for r in self.reasons:
            out[r.status.value].append(r)
        return out


class MissingExplanationError(ValueError):
    """`require_explanation_before_order`가 설명이 비어 있을 때 raise."""


# ---------- helpers ----------


_PASS_KEYWORDS_KO = (
    "통과", "정상", "충분", "ok", "ALLOW",
)
_FAIL_KEYWORDS_KO = (
    "차단", "거부", "REJECT", "BLOCK", "stale", "fail", "실패",
    "초과", "미달", "부족", "insufficient", "low",
)
_WARN_KEYWORDS_KO = (
    "추격", "주의", "warning", "주변", "근접", "축소", "REDUCE_SIZE",
)


def _classify_string_reason(text: str) -> ReasonStatus:
    """단순 문자열 reason의 status를 휴리스틱으로 추정 (legacy 호환)."""
    s = text.lower()
    for kw in _FAIL_KEYWORDS_KO:
        if kw.lower() in s:
            return ReasonStatus.FAIL
    for kw in _WARN_KEYWORDS_KO:
        if kw.lower() in s:
            return ReasonStatus.WARN
    for kw in _PASS_KEYWORDS_KO:
        if kw.lower() in s:
            return ReasonStatus.PASS
    return ReasonStatus.INFO


def _to_reason(
    obj:        Any,
    *,
    category:   ReasonCategory,
    source:     str | None = None,
    severity:   ReasonSeverity = ReasonSeverity.MEDIUM,
) -> SignalReason:
    """자유로운 입력(SignalReason / dict / str)을 SignalReason으로 정규화."""
    if isinstance(obj, SignalReason):
        return obj
    if isinstance(obj, dict):
        try:
            cat = ReasonCategory(obj.get("category", category.value))
        except ValueError:
            cat = category
        try:
            st = ReasonStatus(obj.get("status", "INFO"))
        except ValueError:
            st = _classify_string_reason(str(obj.get("message", "")))
        try:
            sev = ReasonSeverity(obj.get("severity", severity.value))
        except ValueError:
            sev = severity
        return SignalReason(
            category=cat,
            status=st,
            severity=sev,
            source=obj.get("source", source),
            code=obj.get("code"),
            message=str(obj.get("message", "")),
            details=obj.get("details") or None,
        )
    text = str(obj)
    return SignalReason(
        category=category,
        status=_classify_string_reason(text),
        severity=severity,
        source=source,
        message=text,
    )


def _extend_reasons(
    bucket:     list[SignalReason],
    raw_items:  Iterable[Any] | None,
    *,
    category:   ReasonCategory,
    source:     str | None,
    default_status: ReasonStatus | None = None,
    severity:   ReasonSeverity = ReasonSeverity.MEDIUM,
) -> None:
    if not raw_items:
        return
    for item in raw_items:
        r = _to_reason(item, category=category, source=source, severity=severity)
        if default_status is not None and isinstance(item, str):
            # 문자열 입력에 명시 default_status를 강제 (PASS/FAIL 컨텍스트에서).
            r = SignalReason(
                category=r.category, status=default_status, severity=r.severity,
                source=r.source, code=r.code, message=r.message, details=r.details,
            )
        bucket.append(r)


def compose_signal_explanation(
    *,
    signal:            Any | None = None,
    quality_result:    Any | None = None,
    regime_decision:   Any | None = None,
    risk_result:       Any | None = None,
    permission_result: Any | None = None,
    agent_decision:    Any | None = None,
    operator_note:     str | None = None,
    audit_trace_id:    int | None = None,
    symbol:            str | None = None,
    strategy:          str | None = None,
    action:            str | None = None,
    indicators:        dict | None = None,
) -> SignalExplanation:
    """여러 단계의 출력을 받아 하나의 `SignalExplanation`으로 합친다.

    각 입력은 *느슨하게* 받는다 — strategy SignalSignal/SignalExplanation,
    dict, list[str] 등 어떤 형태든 reasons/messages 키를 살펴서 normalize.
    호출자가 모든 단계를 가지지 않아도 부분 합성 가능 (None은 skip).
    """
    reasons: list[SignalReason] = []
    risk_notes: list[str] = []
    indicators_out: dict[str, Any] = dict(indicators or {})

    # 1. strategy signal
    if signal is not None:
        sig_reasons = _coerce_reasons(signal)
        sig_indicators = _coerce_indicators(signal)
        sig_notes = _coerce_risk_notes(signal)
        sig_action = action or _coerce_action(signal)
        action = sig_action
        indicators_out.update(sig_indicators)
        _extend_reasons(reasons, sig_reasons, category=ReasonCategory.STRATEGY,
                         source="strategy", default_status=None)
        risk_notes.extend(sig_notes)

    # 2. signal quality
    if quality_result is not None:
        q_reasons = _coerce_reasons(quality_result)
        q_indicators = _coerce_indicators(quality_result)
        indicators_out.update(q_indicators)
        _extend_reasons(reasons, q_reasons,
                         category=ReasonCategory.SIGNAL_QUALITY, source="quality")

    # 3. market regime
    if regime_decision is not None:
        rg_reasons = _coerce_reasons(regime_decision)
        rg_indicators = _coerce_indicators(regime_decision)
        rg_notes = _coerce_risk_notes(regime_decision)
        indicators_out.update(rg_indicators)
        # decision == ALLOW면 PASS, REDUCE_SIZE/WATCH_ONLY/BLOCK_NEW_BUY는 단계별
        decision_value = _attr(regime_decision, "decision", None)
        decision_str = (
            decision_value.value if hasattr(decision_value, "value")
            else str(decision_value) if decision_value is not None else None
        )
        if decision_str == "ALLOW":
            default_st = ReasonStatus.PASS
        elif decision_str == "REDUCE_SIZE":
            default_st = ReasonStatus.WARN
        elif decision_str == "WATCH_ONLY":
            default_st = ReasonStatus.WARN
        elif decision_str == "BLOCK_NEW_BUY":
            default_st = ReasonStatus.BLOCKED
        else:
            default_st = None
        _extend_reasons(reasons, rg_reasons,
                         category=ReasonCategory.MARKET_REGIME, source="market_regime",
                         default_status=default_st)
        risk_notes.extend(rg_notes)

    # 4. risk manager
    if risk_result is not None:
        rk_reasons = _coerce_reasons(risk_result)
        rk_decision = _attr(risk_result, "decision", None)
        if rk_decision and str(rk_decision).upper() in ("REJECT", "REJECTED"):
            default_st = ReasonStatus.BLOCKED
        elif rk_decision and str(rk_decision).upper() in ("APPROVE", "APPROVED"):
            default_st = ReasonStatus.PASS
        elif rk_decision and str(rk_decision).upper() in ("NEEDS_APPROVAL", "PENDING"):
            default_st = ReasonStatus.WARN
        else:
            default_st = None
        _extend_reasons(reasons, rk_reasons, category=ReasonCategory.RISK_MANAGER,
                         source="risk_manager", default_status=default_st)

    # 5. permission gate
    if permission_result is not None:
        pg_reasons = _coerce_reasons(permission_result)
        pg_status = _attr(permission_result, "status", None) or _attr(permission_result, "decision", None)
        ps = str(pg_status).upper() if pg_status else ""
        if ps in ("APPROVED", "APPROVE"):
            default_st = ReasonStatus.PASS
        elif ps in ("REJECTED", "REJECT", "BLOCKED"):
            default_st = ReasonStatus.BLOCKED
        elif ps in ("PENDING", "NEEDS_APPROVAL"):
            default_st = ReasonStatus.WARN
        else:
            default_st = None
        _extend_reasons(reasons, pg_reasons, category=ReasonCategory.PERMISSION_GATE,
                         source="permission_gate", default_status=default_st)

    # 6. agent decision
    if agent_decision is not None:
        ag_reasons = _coerce_reasons(agent_decision)
        ag_decision = _attr(agent_decision, "decision", None)
        ad = str(ag_decision).upper() if ag_decision else ""
        if ad in ("APPROVE", "BUY", "ALLOW"):
            default_st = ReasonStatus.PASS
        elif ad in ("REJECT", "BLOCK", "STOP"):
            default_st = ReasonStatus.BLOCKED
        elif ad in ("WARN", "HOLD"):
            default_st = ReasonStatus.WARN
        else:
            default_st = ReasonStatus.INFO
        _extend_reasons(reasons, ag_reasons, category=ReasonCategory.AGENT,
                         source="agent", default_status=default_st)

    # 7. operator note (reason 자체로도 추가)
    if operator_note:
        reasons.append(SignalReason(
            category=ReasonCategory.OPERATOR,
            status=ReasonStatus.INFO,
            severity=ReasonSeverity.LOW,
            source="operator",
            message=operator_note,
        ))

    final_status = classify_final_status(
        reasons, risk_result=risk_result, permission_result=permission_result,
    )
    summary = summarize_reasons(reasons)
    return SignalExplanation(
        reasons=reasons,
        final_status=final_status,
        summary=summary,
        audit_trace_id=audit_trace_id,
        symbol=symbol or _attr(signal, "symbol", None),
        strategy=strategy,
        action=action,
        indicators=indicators_out or None,
        risk_notes=risk_notes,
        operator_note=operator_note,
    )


def summarize_reasons(reasons: list[SignalReason], *, max_items: int = 3) -> str:
    """사람이 읽기 쉬운 2-3줄 요약. PASS는 길어도 1줄, FAIL/BLOCKED 우선.

    severity HIGH > MEDIUM > LOW + status BLOCKED > FAIL > WARN > PASS > INFO
    순으로 정렬해 상위 max_items만 join.
    """
    if not reasons:
        return "(설명 없음)"
    rank = {
        ReasonStatus.BLOCKED: 0,
        ReasonStatus.FAIL:    1,
        ReasonStatus.WARN:    2,
        ReasonStatus.PASS:    3,
        ReasonStatus.INFO:    4,
    }
    sev_rank = {ReasonSeverity.HIGH: 0, ReasonSeverity.MEDIUM: 1, ReasonSeverity.LOW: 2}
    sorted_reasons = sorted(
        reasons, key=lambda r: (rank.get(r.status, 99), sev_rank.get(r.severity, 99))
    )
    lines = [r.message for r in sorted_reasons[:max_items] if r.message]
    return " / ".join(lines) if lines else "(설명 없음)"


def classify_final_status(
    reasons:           list[SignalReason],
    *,
    risk_result:       Any | None = None,
    permission_result: Any | None = None,
) -> ExplainStatus:
    """최종 상태 판단.

    우선순위:
    1. permission_result.status가 APPROVED/REJECTED/PENDING이면 그대로 매핑.
    2. risk_result.decision이 REJECT면 REJECTED.
    3. reasons에 BLOCKED 또는 FAIL 있으면 REJECTED.
    4. reasons에 WARN만 있으면 WATCH.
    5. PASS만 있으면 APPROVED.
    6. 그 외 UNKNOWN.
    """
    pg_status = _attr(permission_result, "status", None) or _attr(permission_result, "decision", None)
    ps = str(pg_status).upper() if pg_status else ""
    if ps in ("APPROVED", "APPROVE"):
        return ExplainStatus.APPROVED
    if ps in ("REJECTED", "REJECT", "BLOCKED"):
        return ExplainStatus.REJECTED
    if ps in ("PENDING", "NEEDS_APPROVAL"):
        return ExplainStatus.PENDING

    rk_decision = _attr(risk_result, "decision", None)
    rd = str(rk_decision).upper() if rk_decision else ""
    if rd in ("REJECT", "REJECTED"):
        return ExplainStatus.REJECTED
    if rd in ("NEEDS_APPROVAL", "PENDING"):
        return ExplainStatus.PENDING
    if rd in ("APPROVE", "APPROVED"):
        return ExplainStatus.APPROVED

    if not reasons:
        return ExplainStatus.UNKNOWN

    statuses = {r.status for r in reasons}
    if ReasonStatus.BLOCKED in statuses or ReasonStatus.FAIL in statuses:
        return ExplainStatus.REJECTED
    if ReasonStatus.WARN in statuses and ReasonStatus.PASS not in statuses:
        return ExplainStatus.WATCH
    if ReasonStatus.PASS in statuses and ReasonStatus.WARN not in statuses:
        return ExplainStatus.APPROVED
    if ReasonStatus.PASS in statuses and ReasonStatus.WARN in statuses:
        # PASS와 WARN이 공존 — 신호는 살아있으나 운영자/Agent가 봐야 함.
        return ExplainStatus.WATCH
    return ExplainStatus.UNKNOWN


def require_explanation_before_order(
    explanation: SignalExplanation | None,
    *,
    raise_on_empty: bool = True,
) -> bool:
    """주문 또는 approval 등록 전 explanation이 충분한지 확인.

    충분 = `explanation`이 None이 아니고 `reasons`가 1건 이상.

    `raise_on_empty=True`(기본)이면 부족할 때 `MissingExplanationError`,
    아니면 bool 반환. 본 PR은 기존 주문 흐름에 자동 적용하지 않음 — helper
    + tests + docs로 정책을 명시. 향후 별도 옵트인 PR에서 route_order /
    permission_gate에 강제 적용.
    """
    ok = explanation is not None and bool(explanation.reasons)
    if not ok and raise_on_empty:
        raise MissingExplanationError(
            "explanation 또는 reasons가 비어 있어 주문 등록을 거부합니다 — "
            "체크리스트 #33 '설명 없는 주문 금지' 정책"
        )
    return ok


# ---------- audit row → explanation ----------


def extract_reasons_from_audit_row(row: Any) -> SignalExplanation:
    """OrderAuditLog row 또는 그 dict 표현으로부터 SignalExplanation 합성.

    row의 `reasons`(list[str]) / `decision` / `ai_decision_meta` 등을 살펴
    적절한 카테고리/상태로 분류. 본 함수는 read-only — DB 변경 없음.
    """
    decision    = _attr(row, "decision", None)
    reasons_raw = _attr(row, "reasons", None) or []
    audit_id    = _attr(row, "id", None)
    symbol      = _attr(row, "symbol", None)
    strategy    = _attr(row, "strategy", None)
    side        = _attr(row, "side", None)
    ai_meta     = _attr(row, "ai_decision_meta", None)
    message     = _attr(row, "message", None)

    bucket: list[SignalReason] = []
    decision_str = str(decision).upper() if decision is not None else ""
    if decision_str in ("APPROVED", "APPROVE"):
        ds = ReasonStatus.PASS
    elif decision_str in ("REJECTED", "REJECT", "BLOCKED"):
        ds = ReasonStatus.BLOCKED
    elif decision_str in ("PENDING", "NEEDS_APPROVAL"):
        ds = ReasonStatus.WARN
    else:
        ds = ReasonStatus.INFO

    # audit row의 reasons를 RISK_MANAGER 카테고리로 분류 (route_order가 만든 사유).
    for raw in reasons_raw:
        bucket.append(_to_reason(raw, category=ReasonCategory.RISK_MANAGER,
                                  source="audit:risk_manager"))

    # decision 자체를 별도 reason으로 추가 — 운영자가 한눈에 본다.
    bucket.append(SignalReason(
        category=ReasonCategory.RISK_MANAGER,
        status=ds,
        severity=ReasonSeverity.HIGH if ds == ReasonStatus.BLOCKED else ReasonSeverity.MEDIUM,
        source="audit:decision",
        code=f"DECISION_{decision_str or 'UNKNOWN'}",
        message=f"audit decision = {decision_str or 'UNKNOWN'}" + (f" — {message}" if message else ""),
    ))

    # AI 메타가 있으면 AGENT 카테고리로 carry.
    if isinstance(ai_meta, dict):
        ai_reasons = ai_meta.get("reasons") or []
        for r in ai_reasons:
            bucket.append(_to_reason(r, category=ReasonCategory.AGENT,
                                      source="audit:ai_decision_meta"))
        if "confidence" in ai_meta:
            bucket.append(SignalReason(
                category=ReasonCategory.AGENT, status=ReasonStatus.INFO,
                severity=ReasonSeverity.LOW, source="audit:ai",
                code="AI_CONFIDENCE",
                message=f"AI confidence = {ai_meta['confidence']}",
                details={"confidence": ai_meta["confidence"]},
            ))
        if ai_meta.get("rejected_by_guard"):
            bucket.append(SignalReason(
                category=ReasonCategory.AGENT, status=ReasonStatus.BLOCKED,
                severity=ReasonSeverity.HIGH, source="audit:ai",
                code="AI_REJECTED_BY_GUARD",
                message="AI guard에 의해 거부됨",
            ))

    final_status = classify_final_status(bucket)
    return SignalExplanation(
        reasons=bucket,
        final_status=final_status,
        summary=summarize_reasons(bucket),
        audit_trace_id=audit_id,
        symbol=symbol,
        strategy=strategy,
        action=side,
        indicators=None,
        risk_notes=[],
        operator_note=None,
    )


# ---------- private helpers — 자유로운 입력에서 안전 추출 ----------


def _attr(obj: Any, name: str, default: Any) -> Any:
    """obj가 dict이면 [name], 객체면 getattr — 둘 다 미존재 시 default."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _coerce_reasons(obj: Any) -> list[Any]:
    """객체/dict의 reasons / messages / risk_notes 추출 (느슨)."""
    if obj is None:
        return []
    # SignalExplanation-like
    if isinstance(obj, SignalExplanation):
        return list(obj.reasons)
    # explanation 속성을 가진 경우 (StrategySignal 등)
    expl = _attr(obj, "explanation", None)
    if expl is not None:
        return _coerce_reasons(expl)
    reasons = _attr(obj, "reasons", None)
    if reasons:
        return list(reasons)
    return []


def _coerce_indicators(obj: Any) -> dict:
    if obj is None:
        return {}
    expl = _attr(obj, "explanation", None)
    if expl is not None:
        ind = _attr(expl, "indicators", None) or {}
        return dict(ind)
    ind = _attr(obj, "indicators", None) or {}
    return dict(ind) if isinstance(ind, dict) else {}


def _coerce_risk_notes(obj: Any) -> list[str]:
    notes = _attr(obj, "risk_notes", None)
    if notes:
        return [str(n) for n in notes]
    expl = _attr(obj, "explanation", None)
    if expl is not None:
        ind = _attr(expl, "indicators", None) or {}
        if isinstance(ind, dict):
            n = ind.get("risk_notes") or []
            return [str(x) for x in n]
    return []


def _coerce_action(obj: Any) -> str | None:
    a = _attr(obj, "action", None)
    if a is None:
        return None
    return a.value if hasattr(a, "value") else str(a)
