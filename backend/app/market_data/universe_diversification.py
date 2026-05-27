"""종목군 다변화 universe manifest (CHECKLIST-05, research-only).

기존 백테스트가 대형주 10종목 중심이라 한국시장 전체로 일반화할 수 없다는 한계를 풀기
위해, universe 를 5개 그룹(LARGE_CAP_CORE / MID_CAP_LIQUID / KOSDAQ_LIQUID /
HIGH_VOL_THEME / ETF_PROXY)으로 나눈 *순수 메타데이터* 모듈.

read-only / research-only:
- 본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API 를 import 하지 않는다.
- 종목 코드 → 그룹 매핑은 순수 데이터. 데이터 *가용성* 은 CSV 존재로 판정(라이브 KIS
  호출 0건 — 수집은 별도 read-only 시세 collector 가 담당).
- 어떤 것도 런타임 전략으로 등록/적용되지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# 그룹별 종목 (코드, 한글명). 일부 종목은 복수 그룹에 속할 수 있어 membership 으로 표현.
UNIVERSE_GROUPS: dict[str, list[tuple[str, str]]] = {
    "LARGE_CAP_CORE": [
        ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("005380", "현대차"),
        ("000270", "기아"), ("012330", "현대모비스"), ("066570", "LG전자"),
        ("006400", "삼성SDI"), ("035420", "NAVER"), ("051910", "LG화학"),
        ("105560", "KB금융"),
    ],
    "MID_CAP_LIQUID": [
        ("011200", "HMM"), ("015760", "한국전력"), ("034020", "두산에너빌리티"),
        ("042660", "한화오션"), ("047050", "포스코인터내셔널"), ("010120", "LS ELECTRIC"),
        ("267260", "HD현대일렉트릭"), ("042700", "한미반도체"), ("079550", "LIG넥스원"),
        ("064350", "현대로템"),
    ],
    "KOSDAQ_LIQUID": [
        ("247540", "에코프로비엠"), ("196170", "알테오젠"), ("028300", "HLB"),
        ("141080", "리가켐바이오"), ("068760", "셀트리온제약"), ("087010", "펩트론"),
        ("277810", "레인보우로보틱스"), ("035900", "JYP Ent."), ("041510", "에스엠"),
        ("095340", "ISC"),
    ],
    "HIGH_VOL_THEME": [
        ("042700", "한미반도체"), ("454910", "두산로보틱스"), ("108490", "로보티즈"),
        ("058610", "에스피지"), ("277810", "레인보우로보틱스"), ("007660", "이수페타시스"),
        ("080220", "제주반도체"), ("399720", "가온칩스"), ("403870", "HPSP"),
        ("067310", "하나마이크론"),
    ],
    "ETF_PROXY": [
        ("069500", "KODEX 200"), ("229200", "KODEX 코스닥150"), ("122630", "KODEX 레버리지"),
        ("114800", "KODEX 인버스"), ("102110", "TIGER 200"),
    ],
}

# 시장 proxy (regime 산출용): KODEX 200 우선.
MARKET_PROXY_SYMBOL = "069500"
_SYM_RE = re.compile(r"^(\d{6})")


def all_symbols() -> list[str]:
    """전 그룹 종목 코드 union (순서 보존 dedup)."""
    seen: dict[str, None] = {}
    for members in UNIVERSE_GROUPS.values():
        for code, _name in members:
            seen.setdefault(code, None)
    return list(seen)


def symbol_groups() -> dict[str, list[str]]:
    """종목 코드 → 속한 그룹 리스트 (복수 가능)."""
    out: dict[str, list[str]] = {}
    for g, members in UNIVERSE_GROUPS.items():
        for code, _name in members:
            out.setdefault(code, []).append(g)
    return out


def _present_symbols(data_dir: Path) -> set[str]:
    out: set[str] = set()
    if data_dir.exists():
        for p in data_dir.glob("*.csv"):
            m = _SYM_RE.match(p.stem)
            if m:
                out.add(m.group(1))
    return out


def build_universe_manifest(data_dirs: list[Path]) -> dict[str, Any]:
    """그룹별 종목 + 데이터 가용성(여러 dir 의 CSV 존재) manifest.

    KIS 라이브 호출 0건 — 가용성은 *수집된 CSV 존재* 로만 판정.
    """
    present: set[str] = set()
    for d in data_dirs:
        present |= _present_symbols(Path(d))

    groups_out: dict[str, Any] = {}
    failed: list[str] = []
    for g, members in UNIVERSE_GROUPS.items():
        avail = [c for c, _n in members if c in present]
        miss = [c for c, _n in members if c not in present]
        failed += [f"{g}:{c}" for c in miss]
        groups_out[g] = {
            "symbols": [{"code": c, "name": n, "present": c in present} for c, n in members],
            "available_count": len(avail), "missing_count": len(miss),
            "available_symbols": avail, "missing_symbols": miss,
            "data_sufficient": len(avail) >= 5,   # 그룹 최소 5종목
        }
    return {
        "is_research_only": True,
        "groups": groups_out,
        "market_proxy_symbol": MARKET_PROXY_SYMBOL,
        "total_symbols": len(all_symbols()),
        "total_present": len([c for c in all_symbols() if c in present]),
        "failed_symbols": failed,
        "data_dirs": [str(d) for d in data_dirs],
        "note": "데이터 가용성은 수집된 CSV 존재로 판정 — KIS 라이브 호출 0건. "
                "어떤 그룹/종목도 런타임 전략으로 등록/적용되지 않음(research_only).",
        "auto_apply_allowed": False, "applied_to_runtime": False,
        "is_live_authorization": False, "contains_secret": False,
    }
