"""Paper Auto Loop V2 운용 모드 분류 — 4 모드.

CONNECT-KIS-REALTIME-PRICE-TO-PAPER-AUTO-LOOP-V2 §1/§6:
운용 상태를 *명확히* 4가지로 분리해 UI/로그가 혼동 없이 표시하게 한다.

- VIRTUAL_ONLY          — mock/advisory. broker 호출 0, 주문 전송 없음.
- KIS_REALTIME_DRYRUN   — KIS 실시간 시세로 판단만. broker_order_sent=false.
- KIS_REALTIME_PAPER_AUTO — KIS 실시간 시세로 판단 + 조건 충족 시 KIS 모의주문
                            자동 전송 (여러 종목 가능, 리스크 한도 내).
- KIS_REALTIME_SMOKE_TEST — PAPER_AUTO 의 *하위 제한 모드* (단일 종목/1주/1건).

순수 함수 — settings(또는 dict-like)를 입력 DTO 로 받는다. broker / OrderExecutor
/ route_order / KisClient import 0건. `is_live_authorization=False` 영구.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PaperAutoMode(StrEnum):
    VIRTUAL_ONLY            = "VIRTUAL_ONLY"
    KIS_REALTIME_DRYRUN     = "KIS_REALTIME_DRYRUN"
    KIS_REALTIME_PAPER_AUTO = "KIS_REALTIME_PAPER_AUTO"
    KIS_REALTIME_SMOKE_TEST = "KIS_REALTIME_SMOKE_TEST"


_MODE_MESSAGE_KO: dict[str, str] = {
    PaperAutoMode.VIRTUAL_ONLY:
        "가상/진단 전용 — KIS 모의주문 전송 없음 (broker 호출 0).",
    PaperAutoMode.KIS_REALTIME_DRYRUN:
        "KIS 실시간 시세로 판단만 기록 — 실제 KIS 모의주문 전송 없음.",
    PaperAutoMode.KIS_REALTIME_PAPER_AUTO:
        "KIS 실시간 시세 기준 — 조건 충족 시 리스크 한도 내 KIS 모의주문 자동 전송.",
    PaperAutoMode.KIS_REALTIME_SMOKE_TEST:
        "최초 안전 확인 — 단일 종목 / 1주 / 1건 제한 (정상 운용 아님).",
}


@dataclass(frozen=True)
class PaperAutoModeResult:
    """모드 분류 결과 — *advisory*. is_live_authorization=False 영구."""

    mode:                  str
    message_ko:            str
    market_data_provider:  str
    price_source:          str          # "kis" / "mock" / "yfinance"
    kis_realtime:          bool         # 실시간 KIS 시세 기반 판단인가
    broker_order_enabled:  bool         # 조건 충족 시 실제 KIS 모의주문 전송 가능?
    dry_run:               bool
    smoke_mode:            bool
    # 절대 invariant.
    broker_order_type:     str  = "KIS_PAPER"
    is_live_authorization: bool = False
    is_order_signal:       bool = False

    def __post_init__(self) -> None:
        if self.broker_order_type != "KIS_PAPER":
            raise ValueError("PaperAutoModeResult.broker_order_type must be KIS_PAPER")
        if self.is_live_authorization is not False:
            raise ValueError("PaperAutoModeResult.is_live_authorization must be False")
        if self.is_order_signal is not False:
            raise ValueError("PaperAutoModeResult.is_order_signal must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode":                  self.mode,
            "message_ko":            self.message_ko,
            "market_data_provider":  self.market_data_provider,
            "price_source":          self.price_source,
            "kis_realtime":          bool(self.kis_realtime),
            "broker_order_enabled":  bool(self.broker_order_enabled),
            "dry_run":               bool(self.dry_run),
            "smoke_mode":            bool(self.smoke_mode),
            "broker_order_type":     self.broker_order_type,
            "is_live_authorization": self.is_live_authorization,
            "is_order_signal":       self.is_order_signal,
        }


def resolve_paper_auto_mode(settings: Any) -> PaperAutoModeResult:
    """settings → 현재 Paper Auto 운용 모드.

    분기:
      - enable_live_trading=true            → VIRTUAL_ONLY (driver 자체가 미실행)
      - enable_kis_paper_auto_trading=false → VIRTUAL_ONLY
      - market_data_provider != "kis"       → VIRTUAL_ONLY (mock/yfinance 로는
                                               KIS 모의주문 전송 금지)
      - provider="kis" + dry_run=true       → KIS_REALTIME_DRYRUN
      - provider="kis" + dry_run=false + smoke_mode → KIS_REALTIME_SMOKE_TEST
      - provider="kis" + dry_run=false      → KIS_REALTIME_PAPER_AUTO
    """
    def _g(key: str, default=None):
        if hasattr(settings, key):
            return getattr(settings, key)
        if isinstance(settings, dict):
            return settings.get(key, default)
        return default

    provider = str(_g("market_data_provider", "mock") or "mock").strip().lower()
    enable_kis_auto = bool(_g("enable_kis_paper_auto_trading", False))
    enable_live = bool(_g("enable_live_trading", False))
    dry_run = bool(_g("kis_paper_auto_order_dry_run", True))
    smoke = bool(_g("kis_paper_smoke_mode", False))

    kis_realtime = provider == "kis"
    price_source = provider

    if enable_live or not enable_kis_auto or provider != "kis":
        mode = PaperAutoMode.VIRTUAL_ONLY
        return PaperAutoModeResult(
            mode=mode.value, message_ko=_MODE_MESSAGE_KO[mode],
            market_data_provider=provider, price_source=price_source,
            kis_realtime=kis_realtime, broker_order_enabled=False,
            dry_run=dry_run, smoke_mode=smoke,
        )

    if dry_run:
        mode = PaperAutoMode.KIS_REALTIME_DRYRUN
        broker_order_enabled = False
    elif smoke:
        mode = PaperAutoMode.KIS_REALTIME_SMOKE_TEST
        broker_order_enabled = True
    else:
        mode = PaperAutoMode.KIS_REALTIME_PAPER_AUTO
        broker_order_enabled = True

    return PaperAutoModeResult(
        mode=mode.value, message_ko=_MODE_MESSAGE_KO[mode],
        market_data_provider=provider, price_source="kis",
        kis_realtime=True, broker_order_enabled=broker_order_enabled,
        dry_run=dry_run, smoke_mode=smoke,
    )


__all__ = ["PaperAutoMode", "PaperAutoModeResult", "resolve_paper_auto_mode"]
