"""테마 토글 API용 read model."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.runtime_config import effective_theme_exclusions
from app.theme_filter.catalog import get_theme_catalog
from app.universe.default_universe import FALLBACK_MARKET_CAP_TOP402


KST = timezone(timedelta(hours=9))


def _to_kst(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST).isoformat()
    except (TypeError, ValueError):
        return None


def theme_filter_status(now: datetime | None = None) -> dict[str, Any]:
    catalog = get_theme_catalog()
    active = effective_theme_exclusions(now)
    disabled = frozenset(active)
    blocked = {
        symbol
        for symbol, item in catalog.symbols.items()
        if set(item.themes) & disabled
    }
    rows = []
    for definition in catalog.themes:
        # `other`는 분류 coverage용 잔여 bucket이며 운영 토글 대상은 아니다.
        if definition.id == "other":
            continue
        state = active.get(definition.id)
        rows.append({
            "id": definition.id,
            "label": definition.label,
            "enabled": state is None,
            "duration": state.get("duration") if state else None,
            "disabled_at_kst": _to_kst(state.get("disabled_at")) if state else None,
            "expires_at_kst": _to_kst(state.get("expires_at")) if state else None,
            "mapped_symbol_count": catalog.mapped_count(definition.id),
        })
    unmapped = [s for s in FALLBACK_MARKET_CAP_TOP402 if s not in catalog.symbols]
    return {
        "catalog_version": catalog.taxonomy_version,
        "timezone": "Asia/Seoul",
        "themes": rows,
        "effective_disabled_theme_ids": sorted(disabled),
        "effective_blocked_symbol_count": len(blocked),
        "unmapped_symbol_count": len(unmapped),
        "unmapped_symbols": unmapped,
        "applies_to": "NEW_ENTRY_ONLY",
        "restart_required": False,
        "is_live_authorization": False,
    }
