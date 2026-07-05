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
    ("bio", "바이오·제약"),
    ("automobile", "자동차·부품"),
    ("finance", "금융"),
    ("secondary_battery", "2차전지"),
    ("internet", "인터넷·플랫폼"),
    ("defense", "방산·항공우주"),
    ("shipbuilding", "조선·기자재"),
    ("power_nuclear", "원자력·전력·전선"),
    ("entertainment_media", "엔터·미디어"),
    ("cosmetics", "화장품"),
    ("food_beverage", "음식료"),
    ("steel_materials", "철강·비철·소재"),
    ("construction_infra", "건설·인프라"),
    ("telecom_network", "통신·네트워크"),
    ("gaming", "게임"),
    ("robot_ai", "로봇·AI·자동화"),
    ("retail_consumer", "유통·소비"),
    ("energy_chemical", "에너지·화학"),
    ("transport_logistics", "운송·물류·여행"),
    ("other", "기타"),
)

# KRX 업종 snapshot을 운영자가 검토해 넓은 운영 테마로 정규화한 목록.
# 한 종목이 여러 set에 있으면 다중 테마가 된다.
_V1_THEME_CODES: dict[str, set[str]] = {
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

# taxonomy v2 수동 overlay. 기존 core 태그는 유지하고 generic industrial/consumer와
# 기타 종목을 20개 운영 대분류로 세분화한다.
def _codes(value: str) -> set[str]:
    return set(value.split())


THEME_CODES: dict[str, set[str]] = {
    "semiconductor": (
        _V1_THEME_CODES["semiconductor"]
        | _codes(
            "005935 034220 357780 007660 353200 440110 319660 222800 095610 "
            "131290 007810 031980 089970 183300 420770 281820 323280 031330 "
            "195870 204270 232140 036540 083450 074600 388210 252990 090460 "
            "213420 248070 356860 490470 122640 003160 033640 222080 102710 "
            "046890 094170 137310 043260 166090"
        )
    ),
    "bio": (
        (_V1_THEME_CODES["bio"] - {"166090", "082920"})
        | _codes(
            "950160 000250 298380 087010 310210 214450 226950 009420 237690 "
            "008930 140410 007390 039200 476830 458870 185750 491000 445680 "
            "397030 086450 048410 115180 005690 137310 358570"
        )
    ),
    "automobile": (
        _V1_THEME_CODES["automobile"]
        | _codes("005850 011210 073240 003570 437730 000240 089860 033240")
    ),
    "finance": (
        _V1_THEME_CODES["finance"]
        | _codes("085620 279570 003690 082640 100790 030610 003470 027360 000370 088980")
    ),
    "secondary_battery": (
        _V1_THEME_CODES["secondary_battery"]
        | _codes(
            "450080 078600 001820 361610 005070 417200 137400 161580 336370 "
            "004490 093370 457190 229640 082920"
        )
    ),
    "internet": (
        _V1_THEME_CODES["internet"]
        | _codes("018260 012510 023590 181710 032190 402340 022100 307950")
    ),
    "defense": (
        _V1_THEME_CODES["defense"]
        | _codes(
            "000880 099320 347700 077970 003570 036530 484870 295310 "
            "298040 052690 064350 079550 012450 047810"
        )
    ),
    "shipbuilding": (
        _V1_THEME_CODES["shipbuilding"]
        | _codes("329180 439260 097230 100090 075580 014620 023160 042660 009540 443060 071970")
    ),
    "power_nuclear": _codes(
        "015760 001440 062040 000500 336260 103590 032820 051600 083650 "
        "112610 229640 322000 033100 006340 071320 126340 034020 267260 "
        "010120 006260 267270 178320 004800"
    ),
    "entertainment_media": (
        _V1_THEME_CODES["entertainment"]
        | _codes("030000 079160 214320 041510 035900 352820 122870")
    ),
    "cosmetics": _codes("214450 483650 257720 161890 192820 002790 241710 051900 090430"),
    "food_beverage": _codes(
        "033780 097950 271560 003230 026960 004370 001800 006040 "
        "007310 003380 000080 005300 280360 001040"
    ),
    "steel_materials": _codes(
        "005490 010130 004020 450080 078600 017960 001430 005070 417200 "
        "295310 336370 003240 002380 038500 300720 127120 019210"
    ),
    "construction_infra": _codes(
        "028260 088980 002380 017800 006360 294870 415640 012630 038500 "
        "300720 009450 009240 010780 000720 241560 267270 047040 028050"
    ),
    "telecom_network": _codes("017670 030200 032640 218410 189300 032500 050890 060370 178320"),
    "gaming": _codes("259960 462870 192080 181710 251270 036570"),
    "robot_ai": _codes(
        "277810 454910 108490 022100 319400 098460 140860 090710 "
        "090360 328130 388720 466100 160190 307950 056190"
    ),
    "retail_consumer": _codes(
        "047050 111770 009970 057050 007070 001740 004170 008770 139480 "
        "023530 282330 069960 035250 034230 032350 114090 383220 005440 "
        "081660 021240 033790 001120 192400 012750 066570"
    ),
    "energy_chemical": (
        _V1_THEME_CODES["energy_chemical"]
        | _codes("018670 093370 457190 456040 499790 102710 003240 036460 078930 000210 126340 112610 322000")
    ),
    "transport_logistics": _codes(
        "011200 003490 180640 028670 000120 020560 060370 089860 "
        "032350 006040 439260"
    ),
}

PRIMARY_ORDER = (
    "semiconductor", "bio", "automobile", "finance", "secondary_battery",
    "internet", "defense", "shipbuilding", "power_nuclear",
    "entertainment_media", "cosmetics", "food_beverage", "steel_materials",
    "construction_infra", "telecom_network", "gaming", "robot_ai",
    "retail_consumer", "energy_chemical", "transport_logistics",
)


def _normalise_name(value: str) -> str:
    """이전 fallback dict의 CP949→latin1 mojibake를 정상 한글로 복원."""
    try:
        return value.encode("latin1").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


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
            "name": _normalise_name(FALLBACK_TOP402_NAMES.get(code, code)),
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
        "taxonomy_version": "kr-theme-v2",
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
