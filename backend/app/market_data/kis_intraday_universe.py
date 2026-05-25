"""KIS-INTRADAY-100-VALIDATION-01 — 검증용 종목 universe 구성 (순수 데이터, read-only).

KIS read-only 분봉 수집 + 전략검증에 사용할 **100종목 내외** 후보군을 구성한다.
기존 `default_universe.FALLBACK_MARKET_CAP_TOP50`(50종목)을 포함하고, 시가총액 /
거래대금 상위 *예시* 후보로 보강한다.

설계 원칙 (CLAUDE.md 절대 원칙 상속):
- 본 모듈은 *순수 데이터 + 결정론적 함수* — broker / OrderExecutor / route_order /
  KIS / Anthropic / OpenAI / httpx / requests import 0건. DB / 외부 HTTP 호출 0건.
- 안전 flag mutate 0건. secret / 계좌 carry 0건.
- **본 universe 는 투자 추천이 아니라 검증 후보군이다** — 매수 신호 / 주문 신호가 아님.

종목 코드는 KOSPI/KOSDAQ 대형·중형 *예시* 후보군이며 실시간 시가총액 순위와 정확히
일치하지 않을 수 있다(정적 목록). 우선주 / 스팩 / 관리종목 / 거래정지 종목은 제외 후보로
분류하지만, 정적 목록 한계상 실제 거래 가능 여부는 *수집 단계에서* 응답으로 확인한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.universe.default_universe import (
    FALLBACK_MARKET_CAP_TOP50,
    FALLBACK_MARKET_CAP_TOP50_NAMES,
)

# 6자리 국내 종목코드.
_SYMBOL_RE = re.compile(r"^\d{6}$")

# 명시적 우선주 제외 set (정적 목록 — 보강 후보에 우선주 미포함이 원칙).
_KNOWN_PREFERRED: frozenset[str] = frozenset({
    "005935",  # 삼성전자우 (top50 에 포함되어 있어 명시 제외)
    "005385",  # 현대차우
    "005387",  # 현대차2우B
    "051915",  # LG화학우
    "000815",  # 삼성화재우
    "003555",  # LG우
})

# 보강 후보 (시총/거래대금 상위 예시 — 보통주만, 우선주/스팩/ETF 제외).
# (code, 표시명) — 표시명은 UI carry 용. code 가 단일 진실.
_EXTRA_CANDIDATES: tuple[tuple[str, str], ...] = (
    # KOSPI 대형 — 반도체/2차전지/조선/방산
    ("207940", "삼성바이오로직스"),
    ("373220", "LG에너지솔루션"),
    ("042700", "한미반도체"),
    ("012450", "한화에어로스페이스"),
    ("047810", "한국항공우주"),
    ("064350", "현대로템"),
    ("009540", "HD한국조선해양"),
    ("042660", "한화오션"),
    ("010620", "현대미포조선"),
    ("267260", "HD현대일렉트릭"),
    ("011070", "LG이노텍"),
    ("034220", "LG디스플레이"),
    ("000990", "DB하이텍"),
    ("011790", "SKC"),
    ("009830", "한화솔루션"),
    ("011780", "금호석유"),
    ("285130", "SK케미칼"),
    # KOSPI — 금융/증권
    ("105560", "KB금융"),
    ("316140", "우리금융지주"),
    ("138040", "메리츠금융지주"),
    ("071050", "한국금융지주"),
    ("006800", "미래에셋증권"),
    ("016360", "삼성증권"),
    ("005940", "NH투자증권"),
    # KOSPI — 게임/엔터/플랫폼
    ("259960", "크래프톤"),
    ("352820", "하이브"),
    ("036570", "엔씨소프트"),
    ("251270", "넷마블"),
    # KOSPI — 소비재/유통/식음료
    ("008770", "호텔신라"),
    ("004370", "농심"),
    ("005300", "롯데칠성"),
    ("280360", "롯데웰푸드"),
    ("139480", "이마트"),
    ("023530", "롯데쇼핑"),
    ("069960", "현대백화점"),
    ("282330", "BGF리테일"),
    ("007070", "GS리테일"),
    ("057050", "현대홈쇼핑"),
    ("018880", "한온시스템"),
    ("002790", "아모레G"),
    ("004990", "롯데지주"),
    # KOSPI — 운송/제약/바이오
    ("003490", "대한항공"),
    ("161890", "한국콜마"),
    ("069620", "대웅제약"),
    ("008930", "한미사이언스"),
    # KOSDAQ — 2차전지/반도체 소부장
    ("247540", "에코프로비엠"),
    ("086520", "에코프로"),
    ("240810", "원익IPS"),
    ("357780", "솔브레인"),
    ("058470", "리노공업"),
    ("348370", "엔켐"),
    ("067310", "하나마이크론"),
    ("095340", "ISC"),
    # KOSDAQ — 바이오
    ("196170", "알테오젠"),
    ("028300", "HLB"),
    ("145020", "휴젤"),
    ("214150", "클래시스"),
    ("068760", "셀트리온제약"),
    # KOSDAQ — 엔터/게임/보안
    ("053800", "안랩"),
    ("035900", "JYP Ent."),
    ("041510", "에스엠"),
    ("122870", "와이지엔터테인먼트"),
    ("263750", "펄어비스"),
    ("293490", "카카오게임즈"),
    ("112040", "위메이드"),
)

_EXTRA_NAMES: dict[str, str] = {c: n for c, n in _EXTRA_CANDIDATES}

EXCLUDE_INVALID_FORMAT = "INVALID_FORMAT"
EXCLUDE_PREFERRED = "PREFERRED_STOCK"
EXCLUDE_DUPLICATE = "DUPLICATE"


@dataclass(frozen=True)
class UniverseBuildResult:
    target: int
    symbols: tuple[str, ...]
    names: dict[str, str]
    excluded: tuple[dict[str, str], ...]
    source_counts: dict[str, int]
    is_order_signal: bool = False
    is_investment_advice: bool = False
    contains_secret: bool = False
    reasons: tuple[str, ...] = ()
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.is_order_signal or self.is_investment_advice or self.contains_secret:
            raise ValueError("unsafe invariant True on UniverseBuildResult")


def validate_symbol(code: Any) -> bool:
    """6자리 국내 종목코드 유효성."""
    return bool(code) and bool(_SYMBOL_RE.match(str(code).strip()))


def classify_excluded_symbol_reason(code: Any) -> str | None:
    """제외 사유 분류 — 유효하면 None.

    우선주 / 잘못된 포맷만 정적으로 판정한다. 스팩 / 관리종목 / 거래정지는 정적
    목록으로 확정할 수 없어 *수집 단계의 빈 응답*으로 자연 배제한다.
    """
    s = str(code).strip()
    if not validate_symbol(s):
        return EXCLUDE_INVALID_FORMAT
    if s in _KNOWN_PREFERRED:
        return EXCLUDE_PREFERRED
    return None


def dedupe_symbols(symbols: list[str]) -> tuple[list[str], list[str]]:
    """순서 보존 dedupe → (unique, duplicates)."""
    seen: set[str] = set()
    unique: list[str] = []
    dups: list[str] = []
    for s in symbols:
        key = str(s).strip()
        if key in seen:
            dups.append(key)
        else:
            seen.add(key)
            unique.append(key)
    return unique, dups


def build_kis_intraday_validation_universe(target: int = 100) -> UniverseBuildResult:
    """검증용 universe 구성 — top50 + 보강 후보, 우선주/중복/invalid 제외.

    반환 종목 수는 최대 `target`. 부족분은 가능한 만큼만(과장 금지).
    """
    excluded: list[dict[str, str]] = []
    names: dict[str, str] = {}
    source_counts = {"fallback_top50": 0, "extra_candidates": 0}

    # 1) 출처별 원시 후보 수집 (top50 우선, 그 다음 보강).
    raw: list[tuple[str, str]] = []
    for c in FALLBACK_MARKET_CAP_TOP50:
        raw.append((c, "fallback_top50"))
    for c, _ in _EXTRA_CANDIDATES:
        raw.append((c, "extra_candidates"))

    # 2) dedupe (순서 보존, 첫 출처 유지).
    codes = [c for c, _ in raw]
    unique, dups = dedupe_symbols(codes)
    dup_set = set(dups)
    for d in dups:
        excluded.append({"symbol": d, "reason": EXCLUDE_DUPLICATE})

    # 3) validate + 우선주 제외.
    accepted: list[str] = []
    src_for: dict[str, str] = {}
    for c, src in raw:
        if c in src_for:
            continue
        src_for[c] = src
    for c in unique:
        reason = classify_excluded_symbol_reason(c)
        if reason is not None:
            excluded.append({"symbol": c, "reason": reason})
            continue
        accepted.append(c)
        nm = FALLBACK_MARKET_CAP_TOP50_NAMES.get(c) or _EXTRA_NAMES.get(c) or ""
        if nm:
            names[c] = nm
        source_counts[src_for.get(c, "extra_candidates")] += 1
        if len(accepted) >= target:
            break

    reasons: list[str] = []
    if len(accepted) < target:
        reasons.append(
            f"목표 {target}종목 중 {len(accepted)}종목만 확보 (정적 후보 한계). "
            "수집 단계에서 실제 거래 가능 종목만 PASS 처리됨.")
    if dup_set:
        reasons.append(f"중복 제거 {len(dup_set)}건")

    return UniverseBuildResult(
        target=target,
        symbols=tuple(accepted),
        names=names,
        excluded=tuple(excluded),
        source_counts=source_counts,
        reasons=tuple(reasons),
    )


def to_dict(r: UniverseBuildResult) -> dict[str, Any]:
    return {
        "target": r.target,
        "symbol_count": len(r.symbols),
        "symbols": list(r.symbols),
        "names": r.names,
        "excluded": list(r.excluded),
        "source_counts": r.source_counts,
        "reasons": list(r.reasons),
        "is_order_signal": r.is_order_signal,
        "is_investment_advice": r.is_investment_advice,
        "contains_secret": r.contains_secret,
    }
