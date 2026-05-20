"""Market state read-only endpoint.

fix/market-closed-state-distinction:
사용자가 장 종료 후 desktop EXE 를 실행했을 때 UI 가 "Agent 판단 조회 실패"
처럼 *오류*로 보이게 되는 문제를 해결하기 위한 *advisory* 엔드포인트.

frontend 는 본 엔드포인트를 호출해 현재 한국 시장 phase 를 확인하고,
다른 endpoint 에서 빈 데이터 / 오류가 와도 "장 종료 / 휴장으로 신규 판단
없음" 으로 친절히 안내할 수 있다.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order import 0건
- DB / 외부 HTTP 호출 0건 (순수 시계산)
- Secret / API Key / 계좌번호 포함 0건
- 안전 flag mutate 0건

응답 shape:
    {
      "phase":            "OPEN" | "PRE_OPEN" | "CLOSED" | "WEEKEND",
      "is_open":          bool,
      "is_closed":        bool,        # phase != OPEN
      "label":            str,         # 운영자가 읽을 수 있는 한국어
      "reason":           str,         # 한 줄 설명 (UI banner 노출용)
      "kst_now":          str,         # KST 기준 ISO 8601 ('+09:00')
      "kst_weekday":      int,         # 0=월 ~ 6=일
      "market_open_kst":  "09:00",
      "market_close_kst": "15:30",
    }

본 endpoint 는 무인증 / 빠른 응답 — desktop launcher 의 health probe 와 동일
한 수준으로 가볍게 호출 가능. polling 주기는 frontend 가 1분 단위로 결정.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.scheduler.market_clock import (
    KOREAN_MARKET_CLOSE,
    KOREAN_MARKET_OPEN,
    MarketPhase,
    current_market_phase,
    to_kst,
)

router = APIRouter(prefix="/market", tags=["market"])


_PHASE_LABEL_KR: dict[MarketPhase, str] = {
    MarketPhase.PRE_OPEN: "장 시작 전 대기",
    MarketPhase.OPEN:     "정규장 열림",
    MarketPhase.CLOSED:   "장 종료",
    MarketPhase.WEEKEND:  "휴장 (주말)",
}

_PHASE_REASON_KR: dict[MarketPhase, str] = {
    MarketPhase.PRE_OPEN: "장 시작 전 — 09:00 KST 이전이라 신규 판단 없음",
    MarketPhase.OPEN:     "정규장 열림 — Agent / 전략 / 모니터링 데이터 활성",
    MarketPhase.CLOSED:   "장 종료 — 15:30 KST 이후라 신규 판단 없음",
    MarketPhase.WEEKEND:  "주말 휴장 — 신규 판단 없음",
}


@router.get("/state")
def get_market_state() -> dict:
    """현재 한국 시장 phase 를 read-only 로 반환.

    `current_market_phase` 가 순수 함수이므로 본 endpoint 는 DB / 외부 호출
    0건이며 항상 200 을 반환한다. 공휴일 처리는 후속 작업 — 본 PR 시점에는
    평일/주말 만 구분 (market_clock 정책 일치).
    """
    now = datetime.now(timezone.utc)
    phase = current_market_phase(now)
    kst = to_kst(now)
    return {
        "phase":            phase.value,
        "is_open":          phase == MarketPhase.OPEN,
        "is_closed":        phase != MarketPhase.OPEN,
        "label":            _PHASE_LABEL_KR[phase],
        "reason":           _PHASE_REASON_KR[phase],
        "kst_now":          kst.isoformat(),
        "kst_weekday":      kst.weekday(),
        "market_open_kst":  KOREAN_MARKET_OPEN.strftime("%H:%M"),
        "market_close_kst": KOREAN_MARKET_CLOSE.strftime("%H:%M"),
    }
