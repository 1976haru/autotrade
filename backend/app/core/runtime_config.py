"""런타임 설정 오버라이드 계층 — *장중* 변경 가능한 2개 값 전용.

배경 (2026-06-06, 하루 승인 완료):
  운영자가 홈에서 장중 어느 때나 **동시진입 종목 수(max_concurrent_positions)** 와
  **종목당 투자금(per_stock_budget)** 을 바꿀 수 있게 한다. 두 값만 — 손절/익절/
  일일한도/confidence 등은 *범위 밖*(여기서 절대 다루지 않는다).

설계 원칙:
  - 우선순위: **런타임 오버라이드 > env 기본값**(get_settings()).
  - 저장소: backend 데이터 디렉토리의 `runtime_overrides.json`. 이 파일의 *유일한*
    reader/writer 는 본 모듈이다(봇/리스크/주문 경로는 본 모듈의 effective getter 만
    호출 — 파일 직접 접근 금지).
  - 재시작 시 파일에서 복원. 파일 없거나 손상 시 env 기본값 폴백 + **로그 경고**
    (조용한 실패 금지).
  - 안전 플래그(ENABLE_LIVE_TRADING / KIS_IS_PAPER 등) 일절 미접촉. 주문 경로 코드
    미수정. is_live_authorization 개념 없음 — 본 모듈은 *Paper 운용 파라미터*만 조정.

적용 시점:
  봇(`kis_paper_realtime_scan_tick`)이 매 사이클 effective getter 를 호출하므로,
  저장 즉시 *다음 신규 매수 판단*부터 새 값이 적용된다. 기존 보유는 비접촉(강제
  청산 로직 없음 — 자연 청산까지 유지).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings

_log = logging.getLogger("autotrade.runtime_config")

# 이 기능으로 바꿀 수 있는 *유일한* 2개 키 — 화이트리스트(다른 키 저장 거부).
_INT_KEYS = ("max_concurrent_positions", "per_stock_budget")
_PROFILE_KEY = "active_profile"
# 화이트리스트(이 3개 외 키 저장 거부). 안전 플래그/손절/익절/일일한도/confidence 미포함.
_OVERRIDE_KEYS = (*_INT_KEYS, _PROFILE_KEY)

# 검증 범위 (서버 측 필수 — 프론트 검증만으로 불충분).
MAX_CONCURRENT_MIN, MAX_CONCURRENT_MAX = 1, 10
PER_STOCK_BUDGET_MIN, PER_STOCK_BUDGET_MAX = 100_000, 10_000_000  # 10만 ~ 1,000만 원

# S1: AI 운용 성향 — 런타임 전환 대상(보수/안정/공격).
VALID_PROFILES = ("conservative", "balanced", "aggressive")
DEFAULT_PROFILE = "balanced"

_OVERRIDES_FILENAME = "runtime_overrides.json"

_lock = threading.Lock()
# 인메모리 캐시 — None = 아직 로드 안 함. 로드 실패/손상 시 {} 로 세팅(폴백).
_cache: dict[str, Any] | None = None


class RuntimeConfigValidationError(ValueError):
    """PUT 검증 실패 — 일상 한국어 메시지 carry (API 가 400 으로 변환)."""


def _data_dir() -> Path:
    """sqlite database_url 의 디렉토리 = 데이터 디렉토리. 비-sqlite/파싱 실패 시 ./data."""
    url = str(get_settings().database_url or "")
    if url.startswith("sqlite:///"):
        raw = url[len("sqlite:///"):]
        # sqlite:////abs → '/abs', sqlite:///./data/x.db → './data/x.db'
        p = Path(raw)
        return (p.parent if p.suffix else p)
    return Path("./data")


def overrides_path() -> Path:
    return _data_dir() / _OVERRIDES_FILENAME


def _load() -> dict[str, Any]:
    """파일 → 캐시 1회 로드. 손상 시 빈 dict + 경고(조용한 실패 금지)."""
    global _cache
    if _cache is not None:
        return _cache
    path = overrides_path()
    if not path.exists():
        _cache = {}
        return _cache
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("runtime_overrides.json is not a JSON object")
        clean: dict[str, Any] = {}
        for k in _INT_KEYS:
            if k in raw and raw[k] is not None:
                clean[k] = int(raw[k])
        # active_profile 은 문자열 — 유효값만 수용(그 외 무시 → env 기본값 폴백).
        prof = raw.get(_PROFILE_KEY)
        if isinstance(prof, str) and prof.strip().lower() in VALID_PROFILES:
            clean[_PROFILE_KEY] = prof.strip().lower()
        if "updated_at" in raw and raw["updated_at"]:
            clean["updated_at"] = str(raw["updated_at"])
        _cache = clean
    except Exception as exc:  # noqa: BLE001 — 손상은 폴백 + 경고, 절대 raise 안 함.
        _log.warning(
            "[runtime_config] %s 손상/읽기 실패 — env 기본값으로 폴백합니다: %s",
            path, exc,
        )
        _cache = {}
    return _cache


def _persist(cache: dict[str, Any]) -> None:
    path = overrides_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _log.warning("[runtime_config] %s 저장 실패: %s", path, exc)
        raise


# ── effective getters (봇/리스크/UI 가 호출하는 단일 진실) ──────────────────────

def effective_max_concurrent_positions() -> int:
    ov = _load().get("max_concurrent_positions")
    if ov is not None:
        return int(ov)
    return int(getattr(get_settings(), "kis_paper_max_concurrent_positions", 5))


def effective_per_stock_budget() -> int:
    """종목당 투자금 = kis_paper_per_symbol_notional_krw 의 런타임 오버라이드."""
    ov = _load().get("per_stock_budget")
    if ov is not None:
        return int(ov)
    return int(getattr(get_settings(), "kis_paper_per_symbol_notional_krw", 1_000_000))


def effective_active_profile() -> str:
    """현재 활성 AI 운용 성향 — 런타임 오버라이드 > 기본(balanced). 봇이 매 사이클
    이 getter 로 읽어 다음 판단부터 반영(재시작 불요)."""
    ov = _load().get(_PROFILE_KEY)
    if isinstance(ov, str) and ov in VALID_PROFILES:
        return ov
    return DEFAULT_PROFILE


def _source(key: str) -> str:
    return "override" if _load().get(key) is not None else "env"


def get_runtime_config() -> dict[str, Any]:
    """GET /api/runtime-config 응답 — 실효값 + 출처 + 마지막 변경 시각(KST)."""
    updated_utc = _load().get("updated_at")
    updated_kst = None
    if updated_utc:
        try:
            dt = datetime.fromisoformat(str(updated_utc).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            updated_kst = dt.astimezone(timezone(timedelta(hours=9))).isoformat()
        except Exception:  # noqa: BLE001
            updated_kst = None
    return {
        "max_concurrent_positions": {
            "value":  effective_max_concurrent_positions(),
            "source": _source("max_concurrent_positions"),
            "min":    MAX_CONCURRENT_MIN, "max": MAX_CONCURRENT_MAX,
        },
        "per_stock_budget": {
            "value":  effective_per_stock_budget(),
            "source": _source("per_stock_budget"),
            "min":    PER_STOCK_BUDGET_MIN, "max": PER_STOCK_BUDGET_MAX,
        },
        # 충돌 경고용 — 일일 매수 한도(하드코딩 금지: config 에서 읽음).
        "daily_buy_limit_krw": int(getattr(get_settings(), "kis_paper_daily_buy_limit_krw", 3_000_000)),
        "active_profile": {
            "value":  effective_active_profile(),
            "source": _source(_PROFILE_KEY),
            "options": list(VALID_PROFILES),
        },
        "last_changed_at_kst": updated_kst,
        # 안전 invariant — 본 기능은 실거래 권한과 무관.
        "is_live_authorization": False,
    }


def _validate(max_concurrent_positions: int | None, per_stock_budget: int | None,
              active_profile: str | None = None) -> None:
    if max_concurrent_positions is not None:
        v = int(max_concurrent_positions)
        if not (MAX_CONCURRENT_MIN <= v <= MAX_CONCURRENT_MAX):
            raise RuntimeConfigValidationError(
                f"동시진입 종목 수는 {MAX_CONCURRENT_MIN}~{MAX_CONCURRENT_MAX}개 사이여야 해요."
            )
    if per_stock_budget is not None:
        v = int(per_stock_budget)
        if not (PER_STOCK_BUDGET_MIN <= v <= PER_STOCK_BUDGET_MAX):
            raise RuntimeConfigValidationError(
                "종목당 투자금은 10만 원 ~ 1,000만 원 사이여야 해요."
            )
    if active_profile is not None:
        if str(active_profile).strip().lower() not in VALID_PROFILES:
            raise RuntimeConfigValidationError(
                "운용 성향은 보수/안정/공격 중 하나여야 해요."
            )


def set_runtime_overrides(
    *,
    max_concurrent_positions: int | None = None,
    per_stock_budget: int | None = None,
    active_profile: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """검증 통과 시 저장(파일 + 캐시). *저장 후 다시 읽은 실효값*(get_runtime_config) 반환.

    변경 전 값도 함께 반환(`changes`) — 활동 피드 기록에 사용.
    """
    _validate(max_concurrent_positions, per_stock_budget, active_profile)
    now = now or datetime.now(timezone.utc)
    with _lock:
        before = {
            "max_concurrent_positions": effective_max_concurrent_positions(),
            "per_stock_budget": effective_per_stock_budget(),
            "active_profile": effective_active_profile(),
        }
        cur = dict(_load())
        if max_concurrent_positions is not None:
            cur["max_concurrent_positions"] = int(max_concurrent_positions)
        if per_stock_budget is not None:
            cur["per_stock_budget"] = int(per_stock_budget)
        if active_profile is not None:
            cur[_PROFILE_KEY] = str(active_profile).strip().lower()
        cur["updated_at"] = now.isoformat()
        _persist(cur)
        global _cache
        _cache = cur
    after = {
        "max_concurrent_positions": effective_max_concurrent_positions(),
        "per_stock_budget": effective_per_stock_budget(),
        "active_profile": effective_active_profile(),
    }
    changes: list[dict[str, Any]] = []
    for key in ("max_concurrent_positions", "per_stock_budget", "active_profile"):
        if before[key] != after[key]:
            changes.append({"key": key, "before": before[key], "after": after[key]})
    out = get_runtime_config()
    out["changes"] = changes
    return out


def reset_runtime_overrides_for_tests() -> None:
    """테스트 격리 — 캐시 + 파일 제거."""
    global _cache
    with _lock:
        _cache = None
        try:
            p = overrides_path()
            if p.exists():
                p.unlink()
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "RuntimeConfigValidationError",
    "MAX_CONCURRENT_MIN", "MAX_CONCURRENT_MAX",
    "PER_STOCK_BUDGET_MIN", "PER_STOCK_BUDGET_MAX",
    "overrides_path",
    "VALID_PROFILES", "DEFAULT_PROFILE",
    "effective_max_concurrent_positions",
    "effective_per_stock_budget",
    "effective_active_profile",
    "get_runtime_config",
    "set_runtime_overrides",
    "reset_runtime_overrides_for_tests",
]
