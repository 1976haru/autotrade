"""체크리스트 #64: 알림 메시지 템플릿 builders.

각 helper는 `NotificationEvent`만 반환 — 호출자가 `NotificationService.notify`
로 발송한다. 본 모듈은 broker / OrderExecutor / Secret 어떤 것도 import하지
않는다.

invariant:
- 모든 message는 *시스템 상태 알림* — "매수하세요" 같은 투자 조언 문구 금지.
- Secret(token / api key / 계좌번호)은 인자에 carry되지 않는다 (호출자 책임).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.notifications.types import (
    NotificationEvent,
    NotificationKind,
    NotificationSeverity,
)


def _now_kst_str() -> str:
    # KST iso(짧은 형식) — Telegram 표시용. 운영자가 한국 시간을 즉시 인지.
    from datetime import timedelta, timezone as _tz
    kst = _tz(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y-%m-%d %H:%M KST")


# ---------- 1. Emergency Stop ----------


def build_emergency_stop_event(
    *,
    enabled:     bool,
    level:       str | None = None,
    reason_code: str | None = None,
    decided_by:  str | None = None,
    note:        str | None = None,
) -> NotificationEvent:
    """긴급 정지 ON/OFF 토글 알림. ON=CRITICAL, OFF=INFO."""
    if enabled:
        title = "[긴급] Emergency Stop 활성화"
        severity = NotificationSeverity.CRITICAL
        body_lines = [
            f"단계: {level or 'LEVEL_1'}",
            f"사유: {reason_code or '(미지정)'}",
            f"운영자: {decided_by or '(미지정)'}",
        ]
    else:
        title = "[알림] Emergency Stop 해제"
        severity = NotificationSeverity.INFO
        body_lines = [
            f"운영자: {decided_by or '(미지정)'}",
        ]
    if note:
        body_lines.append(f"메모: {note}")
    body_lines.append(f"시간: {_now_kst_str()}")
    # dedupe: 같은 enabled/level/reason 조합이 짧은 시간에 다시 와도 한 번만.
    dedupe = f"emergency_stop:{int(enabled)}:{level or ''}:{reason_code or ''}"
    return NotificationEvent(
        kind=NotificationKind.EMERGENCY_STOP,
        severity=severity,
        title=title,
        message="\n".join(body_lines),
        dedupe_key=dedupe,
        extra={
            "enabled":     enabled,
            "level":       level,
            "reason_code": reason_code,
        },
    )


# ---------- 2. Data Stale ----------


def build_data_stale_event(
    *,
    symbol:        str,
    age_seconds:   int | float,
    threshold_seconds: int | float | None = None,
) -> NotificationEvent:
    """시세 stale 알림. 임계 초과 시 WARN — 운영자 확인 필요."""
    body_lines = [
        f"종목: {symbol}",
        f"마지막 수신: {int(age_seconds)}초 전",
    ]
    if threshold_seconds is not None and threshold_seconds > 0:
        body_lines.append(f"임계: {int(threshold_seconds)}초")
    body_lines.append("조치: 신규 매수가 자동 차단되었는지 확인하세요.")
    body_lines.append(f"시간: {_now_kst_str()}")
    return NotificationEvent(
        kind=NotificationKind.DATA_STALE,
        severity=NotificationSeverity.WARN,
        title="[주의] 데이터 지연 감지",
        message="\n".join(body_lines),
        # symbol + 분 단위 bucket으로 dedupe (같은 종목 stale이 매초 반복되는
        # 사고 방지).
        dedupe_key=f"data_stale:{symbol}:{int(int(age_seconds) // 60)}",
        extra={"symbol": symbol, "age_seconds": int(age_seconds)},
    )


# ---------- 3. Approval Pending ----------


def build_approval_pending_event(
    *,
    approval_id:     int,
    symbol:          str,
    side:            str,
    quantity:        int,
    strategy:        str | None = None,
    requested_by_ai: bool       = False,
    expires_at:      str | None = None,
) -> NotificationEvent:
    """결재 큐 신규 항목 알림. AI 제안은 별도 표시."""
    body_lines = [
        f"종목: {symbol} {side} {quantity}주",
        f"전략: {strategy or '(미명시)'}",
        f"AI 제안: {'예' if requested_by_ai else '아니오'}",
    ]
    if expires_at:
        body_lines.append(f"만료: {expires_at}")
    body_lines.append(f"시간: {_now_kst_str()}")
    return NotificationEvent(
        kind=NotificationKind.APPROVAL_PENDING,
        severity=NotificationSeverity.WARN,
        title="[확인 필요] 승인 대기 주문",
        message="\n".join(body_lines),
        dedupe_key=f"approval_pending:{approval_id}",
        extra={
            "approval_id":     approval_id,
            "symbol":          symbol,
            "side":            side,
            "quantity":        quantity,
            "requested_by_ai": requested_by_ai,
        },
    )


# ---------- 4. Daily Loss Warning ----------


def build_daily_loss_warning_event(
    *,
    current_loss: int,
    limit:        int,
    pct:          int | float | None = None,
) -> NotificationEvent:
    """일일 손실 한도 접근 알림. pct >= 70: WARN, pct >= 90: CRITICAL."""
    if pct is None:
        pct = int(round((abs(current_loss) / limit) * 100)) if limit > 0 else 0
    pct_int = int(round(float(pct)))
    if pct_int >= 90:
        severity = NotificationSeverity.CRITICAL
        title = "[위험] 일일 손실 한도 접근 (>= 90%)"
    elif pct_int >= 70:
        severity = NotificationSeverity.WARN
        title = "[주의] 일일 손실 한도 접근 (>= 70%)"
    else:
        severity = NotificationSeverity.INFO
        title = "[알림] 일일 손실 진행"
    body = "\n".join([
        f"현재 손실: {abs(int(current_loss)):,}원",
        f"한도: {int(limit):,}원",
        f"사용률: {pct_int}%",
        f"시간: {_now_kst_str()}",
    ])
    return NotificationEvent(
        kind=NotificationKind.DAILY_LOSS_WARNING,
        severity=severity,
        title=title,
        message=body,
        # 10%포인트 bucket으로 dedupe — 동일 구간을 반복 알림하지 않게.
        dedupe_key=f"daily_loss_warning:{pct_int // 10}",
        extra={"current_loss": int(current_loss), "limit": int(limit), "pct": pct_int},
    )


# ---------- 5. Broker Error ----------


def build_broker_error_event(
    *,
    broker:    str,
    operation: str,
    message:   str,
) -> NotificationEvent:
    body = "\n".join([
        f"Broker: {broker}",
        f"호출: {operation}",
        f"오류: {message[:200]}",
        f"시간: {_now_kst_str()}",
    ])
    return NotificationEvent(
        kind=NotificationKind.BROKER_ERROR,
        severity=NotificationSeverity.CRITICAL,
        title="[위험] Broker API 장애",
        message=body,
        dedupe_key=f"broker_error:{broker}:{operation}",
        extra={"broker": broker, "operation": operation},
    )


# ---------- 6. Repeated Rejection ----------


def build_repeated_rejection_event(
    *,
    count:    int,
    window_seconds: int,
    threshold: int,
) -> NotificationEvent:
    body = "\n".join([
        f"연속 REJECTED: {count}건",
        f"윈도우: {window_seconds}초",
        f"임계: {threshold}건",
        "조치: emergency_stop 자동 트리거 여부 확인하세요.",
        f"시간: {_now_kst_str()}",
    ])
    return NotificationEvent(
        kind=NotificationKind.REPEATED_REJECTION,
        severity=NotificationSeverity.WARN,
        title="[주의] 주문 연속 거부",
        message=body,
        dedupe_key=f"repeated_rejection:{count // threshold}",
        extra={"count": count, "threshold": threshold},
    )


# ---------- 7. Margin Risk (Futures) ----------


def build_margin_risk_event(
    *,
    used_pct: float,
    liquidation_distance_pct: float | None = None,
) -> NotificationEvent:
    if used_pct >= 90 or (liquidation_distance_pct is not None
                          and liquidation_distance_pct <= 3):
        severity = NotificationSeverity.CRITICAL
        title = "[위험] 선물 강제청산 임박"
    else:
        severity = NotificationSeverity.WARN
        title = "[주의] 선물 증거금 위험"
    body_lines = [f"증거금 사용률: {used_pct:.1f}%"]
    if liquidation_distance_pct is not None:
        body_lines.append(f"청산가 거리: {liquidation_distance_pct:.2f}%")
    body_lines.append(f"시간: {_now_kst_str()}")
    return NotificationEvent(
        kind=NotificationKind.MARGIN_RISK,
        severity=severity,
        title=title,
        message="\n".join(body_lines),
        dedupe_key=f"margin_risk:{int(used_pct) // 5}",
        extra={
            "used_pct": float(used_pct),
            "liquidation_distance_pct": (
                None if liquidation_distance_pct is None
                else float(liquidation_distance_pct)
            ),
        },
    )


# ---------- 8. Risk Auditor Warn ----------


def build_risk_auditor_event(
    *,
    audit_level: str,
    risk_score:  int,
    summary:     str,
    pause_recommended: bool = False,
    emergency_recommended: bool = False,
) -> NotificationEvent:
    level = (audit_level or "GREEN").upper()
    severity = (NotificationSeverity.CRITICAL
                if level in ("RED", "ORANGE") or emergency_recommended
                else NotificationSeverity.WARN
                if level == "YELLOW" or pause_recommended
                else NotificationSeverity.INFO)
    title = f"[Risk Auditor] {level} (score {risk_score})"
    body_lines = [summary[:200]]
    if pause_recommended:
        body_lines.append("권고: 거래 일시 중지")
    if emergency_recommended:
        body_lines.append("권고: emergency_stop 활성화 검토")
    body_lines.append(f"시간: {_now_kst_str()}")
    return NotificationEvent(
        kind=NotificationKind.RISK_AUDITOR_WARN,
        severity=severity,
        title=title,
        message="\n".join(body_lines),
        dedupe_key=f"risk_auditor:{level}:{risk_score // 10}",
        extra={
            "audit_level": level,
            "risk_score":  int(risk_score),
            "pause":       pause_recommended,
            "emergency":   emergency_recommended,
        },
    )
