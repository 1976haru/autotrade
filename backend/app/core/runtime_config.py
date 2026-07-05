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
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings

_log = logging.getLogger("autotrade.runtime_config")

# 런타임 오버라이드 정수 키. T2(2026-06-12): daily_buy_limit_krw 추가 — 100종목
#   확장 대응(종목수·투자금과 동일 메커니즘, 화이트리스트 확장). 안전 플래그 미포함.
_INT_KEYS = ("max_concurrent_positions", "per_stock_budget", "daily_buy_limit_krw")
# C1(2026-06-10): 손절/익절 % 도 런타임 오버라이드 대상(양수 magnitude — 손절 2.0=−2%).
_FLOAT_KEYS = ("stop_loss_pct", "take_profit_pct")
_PROFILE_KEY = "active_profile"
# 종목 풀 확장(100→400) 런타임 전환 — universe_size(단계) / universe_mode(auto/watchlist).
#   ★cap(scan_max_symbols)·안전 플래그는 *여기서 다루지 않는다*(cap 고정).
_UNIVERSE_SIZE_KEY = "universe_size"
_UNIVERSE_MODE_KEY = "universe_mode"
_THEME_EXCLUSIONS_KEY = "theme_exclusions"
# 화이트리스트(이 키들 외 저장 거부). 안전 플래그/confidence/cap 미포함.
_OVERRIDE_KEYS = (*_INT_KEYS, *_FLOAT_KEYS, _PROFILE_KEY,
                  _UNIVERSE_SIZE_KEY, _UNIVERSE_MODE_KEY)

# 종목 풀 단계(롤아웃) + 소스. cap 은 미포함(고정).
VALID_UNIVERSE_SIZES = (100, 200, 300, 400)
VALID_UNIVERSE_MODES = ("auto", "watchlist")
DEFAULT_UNIVERSE_MODE = "auto"

# 검증 범위 (서버 측 필수 — 프론트 검증만으로 불충분).
# 2026-06-15: 동시진입 상한 10→15 (분산 확대 — 종목당 200만×15=3,000만, 일일한도 내).
MAX_CONCURRENT_MIN, MAX_CONCURRENT_MAX = 1, 15
PER_STOCK_BUDGET_MIN, PER_STOCK_BUDGET_MAX = 100_000, 10_000_000  # 10만 ~ 1,000만 원
STOP_LOSS_PCT_MIN, STOP_LOSS_PCT_MAX = 0.5, 10.0     # 손절 -0.5% ~ -10% (magnitude)
TAKE_PROFIT_PCT_MIN, TAKE_PROFIT_PCT_MAX = 0.5, 20.0  # 익절 +0.5% ~ +20%
# T2: 일일 매수금액 한도 300만 ~ 3억(100종목×300만 수용), 스테퍼 100만 단위.
DAILY_BUY_LIMIT_MIN, DAILY_BUY_LIMIT_MAX = 3_000_000, 300_000_000

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
    """sqlite database_url 의 디렉토리 = 데이터 디렉토리. 비-sqlite/파싱 실패 시 ./data.

    ★절대경로로 고정(.resolve()) — database_url 이 CWD-상대(`./data/...`)라 다른 작업
    디렉토리에서 호출하면 *다른* runtime_overrides.json 을 읽어 effective 가 조용히
    env 로 원복(피드 기록 없는 상태 변경)되던 구멍을 막는다. DB 파일과 같은 디렉토리."""
    url = str(get_settings().database_url or "")
    if url.startswith("sqlite:///"):
        raw = url[len("sqlite:///"):]
        # sqlite:////abs → '/abs', sqlite:///./data/x.db → './data/x.db'
        p = Path(raw)
        d = (p.parent if p.suffix else p)
        return d.resolve()
    return Path("./data").resolve()


def _overrides_base_dir() -> Path:
    """override 파일 디렉토리. ★%APPDATA%\\Autotrade (.env·토큰 캐시와 동거 — CWD
    완전 독립). APPDATA 없으면(Linux/CI) DB 데이터 디렉토리(절대)로 폴백."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Autotrade"
    return _data_dir()


def _legacy_overrides_path() -> Path:
    """이전 위치(DB 데이터 디렉토리). 마이그레이션 원본."""
    return _data_dir() / _OVERRIDES_FILENAME


def overrides_path() -> Path:
    return _overrides_base_dir() / _OVERRIDES_FILENAME


def _migrate_legacy_override_if_needed() -> None:
    """신 위치(%APPDATA%)에 없고 구 위치(./data)에 있으면 1회 이전 — 기존 설정 보존."""
    try:
        new = overrides_path()
        if new.exists():
            return
        old = _legacy_overrides_path()
        if old.resolve() == new.resolve() or not old.exists():
            return
        new.parent.mkdir(parents=True, exist_ok=True)
        new.write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
        _log.info("[runtime_config] override 마이그레이션: %s → %s", old, new)
    except Exception as exc:  # noqa: BLE001 — 마이그레이션 실패는 정상 폴백(env), raise 금지.
        _log.warning("[runtime_config] override 마이그레이션 실패(무시): %s", exc)


def _load() -> dict[str, Any]:
    """파일 → 캐시 1회 로드. 손상 시 빈 dict + 경고(조용한 실패 금지)."""
    global _cache
    if _cache is not None:
        return _cache
    _migrate_legacy_override_if_needed()   # 구 ./data 위치 → %APPDATA% 1회 이전
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
        for k in _FLOAT_KEYS:
            if k in raw and raw[k] is not None:
                clean[k] = float(raw[k])
        # active_profile 은 문자열 — 유효값만 수용(그 외 무시 → env 기본값 폴백).
        prof = raw.get(_PROFILE_KEY)
        if isinstance(prof, str) and prof.strip().lower() in VALID_PROFILES:
            clean[_PROFILE_KEY] = prof.strip().lower()
        # universe_size — 100/200/400 만 수용(그 외 무시 → env 기본 100 폴백).
        usz = raw.get(_UNIVERSE_SIZE_KEY)
        if usz is not None:
            try:
                if int(usz) in VALID_UNIVERSE_SIZES:
                    clean[_UNIVERSE_SIZE_KEY] = int(usz)
            except (TypeError, ValueError):
                pass
        # universe_mode — auto/watchlist 만 수용.
        umode = raw.get(_UNIVERSE_MODE_KEY)
        if isinstance(umode, str) and umode.strip().lower() in VALID_UNIVERSE_MODES:
            clean[_UNIVERSE_MODE_KEY] = umode.strip().lower()
        exclusions = raw.get(_THEME_EXCLUSIONS_KEY)
        if isinstance(exclusions, dict):
            clean_exclusions: dict[str, dict[str, Any]] = {}
            for theme_id, state in exclusions.items():
                if not isinstance(theme_id, str) or not isinstance(state, dict):
                    continue
                duration = str(state.get("duration") or "")
                if duration not in ("today", "until_enabled"):
                    continue
                clean_exclusions[theme_id] = {
                    "duration": duration,
                    "disabled_at": str(state.get("disabled_at") or ""),
                    "expires_at": (
                        str(state["expires_at"]) if state.get("expires_at") else None
                    ),
                }
            clean[_THEME_EXCLUSIONS_KEY] = clean_exclusions
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
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
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


def effective_daily_buy_limit() -> int:
    """일일 매수금액 한도 = kis_paper_daily_buy_limit_krw 의 런타임 오버라이드.
    ★봇이 매 사이클 이 getter 로 읽어 BUY 사전 가드(driver_bridge)에 반영(R1 패턴,
    재시작 불요). 주문 경로 route_order/RiskManager/Gate/Executor 는 미접촉."""
    ov = _load().get("daily_buy_limit_krw")
    if ov is not None:
        return int(ov)
    return int(getattr(get_settings(), "kis_paper_daily_buy_limit_krw", 3_000_000))


def effective_active_profile() -> str:
    """현재 활성 AI 운용 성향 — 런타임 오버라이드 > 기본(balanced). 봇이 매 사이클
    이 getter 로 읽어 다음 판단부터 반영(재시작 불요)."""
    ov = _load().get(_PROFILE_KEY)
    if isinstance(ov, str) and ov in VALID_PROFILES:
        return ov
    return DEFAULT_PROFILE


def effective_stop_loss_pct() -> float:
    """손절 % (양수 magnitude, 2.0=−2%) — 런타임 오버라이드 > config 기본.
    ★봇 council 이 *매 사이클* 이 getter 로 읽어 신규 진입 + 보유 포지션 청산
    판단에 반영(profile 무관 단일 진실, 재시작 불요)."""
    ov = _load().get("stop_loss_pct")
    if ov is not None:
        return float(ov)
    return float(getattr(get_settings(), "kis_paper_default_stop_loss_pct", 2.0))


def effective_take_profit_pct() -> float:
    """익절 % (양수 magnitude, 3.5=+3.5%) — 런타임 오버라이드 > config 기본."""
    ov = _load().get("take_profit_pct")
    if ov is not None:
        return float(ov)
    return float(getattr(get_settings(), "kis_paper_default_take_profit_pct", 3.5))


def effective_universe_size() -> int:
    """종목 풀 크기(100/200/400) — 런타임 오버라이드 > config 기본(100).
    봇(_scan_universe_symbols)이 매 사이클 읽어 TOP402[:size] 슬라이스. cap 무관."""
    ov = _load().get(_UNIVERSE_SIZE_KEY)
    if ov is not None and int(ov) in VALID_UNIVERSE_SIZES:
        return int(ov)
    sz = int(getattr(get_settings(), "kis_paper_universe_size", 100) or 100)
    return sz if sz in VALID_UNIVERSE_SIZES else 100


def effective_universe_mode() -> str:
    """유니버스 소스(auto/watchlist) — 런타임 오버라이드 > config 기본(auto)."""
    ov = _load().get(_UNIVERSE_MODE_KEY)
    if isinstance(ov, str) and ov in VALID_UNIVERSE_MODES:
        return ov
    m = str(getattr(get_settings(), "kis_paper_universe_mode", "auto") or "auto").lower()
    return m if m in VALID_UNIVERSE_MODES else DEFAULT_UNIVERSE_MODE


def _as_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def effective_theme_exclusions(now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """현재 유효한 OFF 테마. 만료 판단은 서버 시각으로 하며 저장 파일은 건드리지 않는다."""
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    raw = _load().get(_THEME_EXCLUSIONS_KEY)
    if not isinstance(raw, dict):
        return {}
    active: dict[str, dict[str, Any]] = {}
    for theme_id, state in raw.items():
        if not isinstance(state, dict):
            continue
        duration = str(state.get("duration") or "")
        expires = _as_utc(state.get("expires_at"))
        if duration == "today" and (expires is None or expires <= now_utc):
            continue
        if duration not in ("today", "until_enabled"):
            continue
        active[str(theme_id)] = dict(state)
    return active


def effective_disabled_theme_ids(now: datetime | None = None) -> frozenset[str]:
    return frozenset(effective_theme_exclusions(now))


def set_theme_enabled(
    theme_id: str,
    *,
    enabled: bool,
    duration: str | None = None,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """테마 ON/OFF를 기존 runtime override 파일에 부분 갱신한다."""
    from app.theme_filter.catalog import get_theme_catalog

    theme_id = str(theme_id or "").strip()
    if theme_id not in get_theme_catalog().theme_ids:
        raise KeyError(theme_id)
    if not enabled and duration not in ("today", "until_enabled"):
        raise RuntimeConfigValidationError("OFF 기간은 오늘만(today) 또는 해제까지(until_enabled)여야 해요.")

    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires_at: str | None = None
    if not enabled and duration == "today":
        kst = timezone(timedelta(hours=9))
        local = now_utc.astimezone(kst)
        next_midnight = (local + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        expires_at = next_midnight.astimezone(timezone.utc).isoformat()

    global _cache
    with _lock:
        cur = dict(_load())
        exclusions = dict(cur.get(_THEME_EXCLUSIONS_KEY) or {})
        if enabled:
            exclusions.pop(theme_id, None)
        else:
            exclusions[theme_id] = {
                "duration": duration,
                "disabled_at": now_utc.isoformat(),
                "expires_at": expires_at,
            }
        cur[_THEME_EXCLUSIONS_KEY] = exclusions
        cur["updated_at"] = now_utc.isoformat()
        _persist(cur)
        _cache = cur
    return effective_theme_exclusions(now_utc)


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
        "stop_loss_pct": {
            "value":  effective_stop_loss_pct(),
            "source": _source("stop_loss_pct"),
            "min":    STOP_LOSS_PCT_MIN, "max": STOP_LOSS_PCT_MAX,
        },
        "take_profit_pct": {
            "value":  effective_take_profit_pct(),
            "source": _source("take_profit_pct"),
            "min":    TAKE_PROFIT_PCT_MIN, "max": TAKE_PROFIT_PCT_MAX,
        },
        # T2: 일일 매수금액 한도 — 이제 런타임 오버라이드 대상(종목수·투자금과 동일).
        #   meta(min/max/source) 추가 — UI 스테퍼용. 충돌 경고 계산도 이 value 사용.
        "daily_buy_limit_krw": {
            "value":  effective_daily_buy_limit(),
            "source": _source("daily_buy_limit_krw"),
            "min":    DAILY_BUY_LIMIT_MIN, "max": DAILY_BUY_LIMIT_MAX,
        },
        "active_profile": {
            "value":  effective_active_profile(),
            "source": _source(_PROFILE_KEY),
            "options": list(VALID_PROFILES),
        },
        # 종목 풀 확장 — 단계(size) + 소스(mode). cap 은 미노출(고정).
        "universe_size": {
            "value":  effective_universe_size(),
            "source": _source(_UNIVERSE_SIZE_KEY),
            "options": list(VALID_UNIVERSE_SIZES),
        },
        "universe_mode": {
            "value":  effective_universe_mode(),
            "source": _source(_UNIVERSE_MODE_KEY),
            "options": list(VALID_UNIVERSE_MODES),
        },
        "last_changed_at_kst": updated_kst,
        # 안전 invariant — 본 기능은 실거래 권한과 무관.
        "is_live_authorization": False,
    }


def _validate(max_concurrent_positions: int | None, per_stock_budget: int | None,
              active_profile: str | None = None,
              stop_loss_pct: float | None = None,
              take_profit_pct: float | None = None,
              daily_buy_limit_krw: int | None = None,
              universe_size: int | None = None,
              universe_mode: str | None = None) -> None:
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
    if daily_buy_limit_krw is not None:
        v = int(daily_buy_limit_krw)
        if not (DAILY_BUY_LIMIT_MIN <= v <= DAILY_BUY_LIMIT_MAX):
            raise RuntimeConfigValidationError(
                "일일 매수 한도는 300만 원 ~ 3억 원 사이여야 해요."
            )
    if stop_loss_pct is not None:
        try:
            v = float(stop_loss_pct)
        except (TypeError, ValueError):
            raise RuntimeConfigValidationError("손절 %는 숫자여야 해요.")
        if not (STOP_LOSS_PCT_MIN <= v <= STOP_LOSS_PCT_MAX):
            raise RuntimeConfigValidationError(
                "손절은 -0.5% ~ -10% 사이여야 해요."
            )
    if take_profit_pct is not None:
        try:
            v = float(take_profit_pct)
        except (TypeError, ValueError):
            raise RuntimeConfigValidationError("익절 %는 숫자여야 해요.")
        if not (TAKE_PROFIT_PCT_MIN <= v <= TAKE_PROFIT_PCT_MAX):
            raise RuntimeConfigValidationError(
                "익절은 +0.5% ~ +20% 사이여야 해요."
            )
    if active_profile is not None:
        if str(active_profile).strip().lower() not in VALID_PROFILES:
            raise RuntimeConfigValidationError(
                "운용 성향은 보수/안정/공격 중 하나여야 해요."
            )
    if universe_size is not None:
        try:
            v = int(universe_size)
        except (TypeError, ValueError):
            raise RuntimeConfigValidationError("종목 풀 크기는 100/200/400 중 하나여야 해요.")
        if v not in VALID_UNIVERSE_SIZES:
            raise RuntimeConfigValidationError("종목 풀 크기는 100/200/400 중 하나여야 해요.")
    if universe_mode is not None:
        if str(universe_mode).strip().lower() not in VALID_UNIVERSE_MODES:
            raise RuntimeConfigValidationError(
                "유니버스 소스는 자동(auto)/관심종목(watchlist) 중 하나여야 해요."
            )


def set_runtime_overrides(
    *,
    max_concurrent_positions: int | None = None,
    per_stock_budget: int | None = None,
    active_profile: str | None = None,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    daily_buy_limit_krw: int | None = None,
    universe_size: int | None = None,
    universe_mode: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """검증 통과 시 저장(파일 + 캐시). *저장 후 다시 읽은 실효값*(get_runtime_config) 반환.

    변경 전 값도 함께 반환(`changes`) — 활동 피드 기록에 사용.
    """
    _validate(max_concurrent_positions, per_stock_budget, active_profile,
              stop_loss_pct, take_profit_pct, daily_buy_limit_krw,
              universe_size, universe_mode)
    now = now or datetime.now(timezone.utc)
    with _lock:
        before = {
            "max_concurrent_positions": effective_max_concurrent_positions(),
            "per_stock_budget": effective_per_stock_budget(),
            "active_profile": effective_active_profile(),
            "stop_loss_pct": effective_stop_loss_pct(),
            "take_profit_pct": effective_take_profit_pct(),
            "daily_buy_limit_krw": effective_daily_buy_limit(),
            "universe_size": effective_universe_size(),
            "universe_mode": effective_universe_mode(),
        }
        cur = dict(_load())
        if max_concurrent_positions is not None:
            cur["max_concurrent_positions"] = int(max_concurrent_positions)
        if per_stock_budget is not None:
            cur["per_stock_budget"] = int(per_stock_budget)
        if stop_loss_pct is not None:
            cur["stop_loss_pct"] = float(stop_loss_pct)
        if take_profit_pct is not None:
            cur["take_profit_pct"] = float(take_profit_pct)
        if daily_buy_limit_krw is not None:
            cur["daily_buy_limit_krw"] = int(daily_buy_limit_krw)
        if active_profile is not None:
            cur[_PROFILE_KEY] = str(active_profile).strip().lower()
        if universe_size is not None:
            cur[_UNIVERSE_SIZE_KEY] = int(universe_size)
        if universe_mode is not None:
            cur[_UNIVERSE_MODE_KEY] = str(universe_mode).strip().lower()
        cur["updated_at"] = now.isoformat()
        _persist(cur)
        # 가시성: override 가 저장된 *절대 경로* 를 로그로 — 다음 기동이 다른 경로를
        #   읽어 조용히 env 로 원복되면 로그 대조로 즉시 진단 가능.
        _log.info("[runtime_config] override persisted to %s (mc=%s bud=%s daily=%s sl=%s tp=%s profile=%s)",
                  overrides_path(), cur.get("max_concurrent_positions"),
                  cur.get("per_stock_budget"), cur.get("daily_buy_limit_krw"),
                  cur.get("stop_loss_pct"),
                  cur.get("take_profit_pct"), cur.get(_PROFILE_KEY))
        global _cache
        _cache = cur
    after = {
        "max_concurrent_positions": effective_max_concurrent_positions(),
        "per_stock_budget": effective_per_stock_budget(),
        "active_profile": effective_active_profile(),
        "stop_loss_pct": effective_stop_loss_pct(),
        "take_profit_pct": effective_take_profit_pct(),
        "daily_buy_limit_krw": effective_daily_buy_limit(),
        "universe_size": effective_universe_size(),
        "universe_mode": effective_universe_mode(),
    }
    changes: list[dict[str, Any]] = []
    for key in ("max_concurrent_positions", "per_stock_budget", "active_profile",
                "stop_loss_pct", "take_profit_pct", "daily_buy_limit_krw",
                "universe_size", "universe_mode"):
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
    "DAILY_BUY_LIMIT_MIN", "DAILY_BUY_LIMIT_MAX",
    "overrides_path",
    "VALID_PROFILES", "DEFAULT_PROFILE",
    "VALID_UNIVERSE_SIZES", "VALID_UNIVERSE_MODES", "DEFAULT_UNIVERSE_MODE",
    "effective_max_concurrent_positions",
    "effective_per_stock_budget",
    "effective_daily_buy_limit",
    "effective_active_profile",
    "effective_universe_size",
    "effective_universe_mode",
    "effective_theme_exclusions",
    "effective_disabled_theme_ids",
    "set_theme_enabled",
    "get_runtime_config",
    "set_runtime_overrides",
    "reset_runtime_overrides_for_tests",
]
