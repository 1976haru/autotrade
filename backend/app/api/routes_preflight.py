"""V8: 출발 전 점검(preflight) — 월요일 아침 수동 체크리스트를 화면 1번으로.

GET /api/preflight → 6항목 OK/FAIL/WARN:
  ① .env 출처 + 키지문   ② 안전 플래그 4종   ③ KIS 연결(잔고 1회)
  ④ 토큰 캐시 상태       ⑤ 보유 정합(KIS N)  ⑥ 긴급정지 OFF

전부 기존 정보 재사용 — 신규 KIS 호출은 ③뿐(⑤는 같은 잔고 캐시에 편승).
주문 경로/안전 플래그 미변경, read-only.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

_KST = timezone(timedelta(hours=9))

from fastapi import APIRouter, Depends

from app.api.deps import get_broker, get_risk_manager
from app.core.config import get_settings

router = APIRouter(prefix="/preflight", tags=["preflight"])


def _item(key, label, status, detail):
    return {"key": key, "label": label, "status": status, "detail": detail}


@router.get("")
async def preflight(broker=Depends(get_broker), risk=Depends(get_risk_manager)) -> dict:
    s = get_settings()
    items = []

    # ① .env 출처 + 키지문(앱키 sha256 앞 16 — 평문 노출 0).
    app_key = str(getattr(s, "kis_app_key", "") or "")
    fp = hashlib.sha256(app_key.encode("utf-8")).hexdigest()[:16] if app_key else None
    items.append(_item(
        "env", "키/환경(.env)",
        "ok" if app_key else "fail",
        f"키지문 {fp}" if fp else "KIS 키가 비어 있어요 — .env 확인 필요"))

    # ② 안전 플래그 4종.
    mode = str(getattr(s, "default_mode", "")).upper()
    live = bool(getattr(s, "enable_live_trading", False))
    ai_exec = bool(getattr(s, "enable_ai_execution", False))
    paper = bool(getattr(s, "kis_is_paper", True))
    flags_ok = (not live) and (not ai_exec) and paper and ("LIVE" not in mode or "SHADOW" in mode)
    items.append(_item(
        "safety_flags", "안전 플래그",
        "ok" if flags_ok else "warn",
        f"모드 {mode} · 실거래 {'ON' if live else 'OFF'} · AI실행 {'ON' if ai_exec else 'OFF'} · 모의 {'예' if paper else '아니오'}"))

    # ③ KIS 연결 — 잔고 1회(유일한 신규 호출). ④/⑤ 가 이 호출 캐시에 편승.
    kis_ok = False
    holdings_n = None
    try:
        bal = await broker.get_balance()
        cash = getattr(bal, "cash", None)
        kis_ok = True
        items.append(_item("kis", "증권사(KIS) 연결", "ok",
                           f"예수금 {int(cash):,}원" if isinstance(cash, (int, float)) else "잔고 응답 정상"))
    except Exception as exc:  # noqa: BLE001
        items.append(_item("kis", "증권사(KIS) 연결", "fail",
                           f"잔고 조회 실패 — 잠시 후 다시 시도돼요 ({type(exc).__name__})"))

    # ④ 토큰 캐시 상태(디스크).
    try:
        from app.brokers.kis_client import _load_cached_token
        cached = _load_cached_token(app_key, paper)
        if cached and datetime.now(timezone.utc) < cached[1]:
            items.append(_item("token_cache", "토큰 캐시", "ok",
                               f"유효(만료 {cached[1].astimezone(_KST).strftime('%m-%d %H:%M')})"))
        else:
            items.append(_item("token_cache", "토큰 캐시", "warn", "캐시 없음/만료 — 첫 호출 시 재발급"))
    except Exception:  # noqa: BLE001
        items.append(_item("token_cache", "토큰 캐시", "warn", "확인 불가"))

    # ⑤ 보유 정합 — 같은 잔고 캐시(get_positions)에 편승(신규 KIS 호출 0).
    if kis_ok:
        try:
            positions = await broker.get_positions()
            holdings_n = len([p for p in positions if getattr(p, "quantity", 0) > 0])
            items.append(_item("holdings", "보유 종목", "ok", f"KIS {holdings_n}종목 — 화면과 동일 소스"))
        except Exception:  # noqa: BLE001
            items.append(_item("holdings", "보유 종목", "warn", "보유 조회 실패"))
    else:
        items.append(_item("holdings", "보유 종목", "warn", "KIS 연결 후 확인돼요"))

    # ⑥ 긴급정지 OFF.
    estop = bool(getattr(risk, "emergency_stop", False))
    items.append(_item("emergency_stop", "긴급정지",
                       "ok" if not estop else "warn",
                       "꺼짐(정상)" if not estop else "켜짐 — 주문이 차단돼 있어요"))

    all_ok = all(i["status"] == "ok" for i in items)
    return {
        "items": items,
        "all_ok": all_ok,
        "summary": "출발 준비 완료" if all_ok else "확인이 필요한 항목이 있어요",
        "is_live_authorization": False,
    }
