"""TOP402 테마 catalog 생성기.

운영 중 외부 호출은 하지 않는다. KRX 업종을 운영 테마로 정규화한 수동 snapshot과
다중 테마 overlay를 결합해 data/market/theme_catalog.json을 만든다.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.universe.default_universe import (  # noqa: E402
    FALLBACK_MARKET_CAP_TOP402,
    FALLBACK_TOP402_NAMES,
)


THEMES = (
    ("semiconductor", "반도체"),
    ("bio", "바이오"),
    ("automobile", "자동차"),
    ("finance", "금융"),
    ("secondary_battery", "2차전지"),
    ("internet", "인터넷"),
    ("defense", "방산"),
    ("shipbuilding", "조선"),
    ("energy_chemical", "에너지·화학"),
    ("industrial", "산업재"),
    ("consumer", "소비재"),
    ("entertainment", "엔터테인먼트"),
    ("other", "기타"),
)

# KRX 업종 snapshot을 운영자가 검토해 넓은 운영 테마로 정규화한 목록.
# 한 종목이 여러 set에 있으면 다중 테마가 된다.
THEME_CODES: dict[str, set[str]] = {
    "semiconductor": {
        "005930", "000660", "009150", "042700", "058470", "240810", "000990",
        "036930", "039030", "403870", "080220", "095340", "067310", "005290",
        "108320", "036830", "058610", "084370", "064760", "089030", "101490",
        "033240", "059090", "000670", "006120", "053610",
    },
    "bio": {
        "207940", "068270", "326030", "028300", "196170", "068760", "145020",
        "214150", "000100", "128940", "141080", "347850", "006280", "096530",
        "166090", "069620", "195940", "302440", "290650", "214370", "281740",
        "065350", "085660", "475830", "082920", "003850", "285130",
    },
    "automobile": {
        "005380", "000270", "012330", "086280", "018880", "204320", "007340",
        "161390", "025540", "064960", "033240", "298050", "122870",
    },
    "finance": {
        "105560", "055550", "086790", "032830", "000810", "316140", "024110",
        "029780", "138040", "071050", "016360", "006800", "039490", "005940",
        "005830", "138930", "175330", "139130", "031210", "001450", "001720",
        "003540", "003530", "323410", "377300",
    },
    "secondary_battery": {
        "373220", "051910", "006400", "003670", "247540", "086520", "066970",
        "011790", "009830", "278470", "020150", "006110", "003850", "285130",
        "011070", "064400", "131970", "475150",
    },
    "internet": {
        "035420", "035720", "323410", "377300", "263750", "293490", "112040",
        "251270", "036570", "035760", "067310", "052020", "030190",
    },
    "defense": {
        "012450", "064350", "047810", "079550", "272210", "047040", "103140",
        "052690", "006260", "298040", "489790",
    },
    "shipbuilding": {
        "042660", "009540", "267250", "010140", "443060", "071970", "082740",
        "267270", "010120",
    },
    "energy_chemical": {
        "051910", "096770", "010950", "011170", "011780", "009830", "010060",
        "088350", "014680", "375500", "004000", "006650", "120110", "285130",
        "003090", "298020", "020150", "010170",
    },
    "industrial": {
        "005490", "034020", "267260", "004020", "010120", "006260", "000720",
        "000150", "241560", "267270", "036460", "006360", "000240", "042660",
        "009540", "443060", "071970", "047040", "028050", "298040", "064350",
        "052690", "028670", "100790", "030610", "000120", "001120", "004490",
        "006340",
    },
    "consumer": {
        "051900", "090430", "097950", "271560", "008770", "139480", "023530",
        "282330", "069960", "035250", "003230", "004170", "021240", "383220",
        "007310", "004370", "005300", "002790", "192820", "241710", "003240",
        "000080", "089860", "033790",
    },
    "entertainment": {
        "041510", "035900", "352820", "122870", "035760", "263750", "293490",
        "112040", "251270",
    },
}

PRIMARY_ORDER = (
    "semiconductor", "bio", "automobile", "finance", "secondary_battery",
    "internet", "defense", "shipbuilding", "energy_chemical", "industrial",
    "consumer", "entertainment",
)


def build_catalog() -> dict:
    universe = set(FALLBACK_MARKET_CAP_TOP402)
    unknown = sorted(set().union(*THEME_CODES.values()) - universe)
    if unknown:
        raise ValueError(f"manual theme codes outside TOP402: {unknown}")

    labels = dict(THEMES)
    symbols: dict[str, dict] = {}
    for code in FALLBACK_MARKET_CAP_TOP402:
        tags = [theme for theme, codes in THEME_CODES.items() if code in codes]
        primary = next((theme for theme in PRIMARY_ORDER if theme in tags), "other")
        if not tags:
            tags = ["other"]
        symbols[code] = {
            "name": FALLBACK_TOP402_NAMES.get(code, code),
            "primary_theme": primary,
            "themes": tags,
            "krx_industry": labels[primary],
            "source": "krx_snapshot+manual_overlay" if len(tags) > 1 else "krx_snapshot",
            "reviewed_at": date.today().isoformat(),
        }

    return {
        "schema_version": 1,
        "universe": "TOP402",
        "universe_as_of": "2026-07-03",
        "taxonomy_version": "kr-theme-v1",
        "themes": [
            {"id": theme_id, "label": label, "order": (idx + 1) * 10}
            for idx, (theme_id, label) in enumerate(THEMES)
        ],
        "symbols": symbols,
    }


def main() -> None:
    out = ROOT / "data" / "market" / "theme_catalog.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(build_catalog(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out} ({len(FALLBACK_MARKET_CAP_TOP402)} symbols)")


if __name__ == "__main__":
    main()
