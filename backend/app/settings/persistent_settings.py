"""P-16: Paper 자금 설정 영구 저장 — EXE 재실행 후에도 유지.

사용자가 EXE 에서 설정한 *Paper 자금 운용 기준* (시드머니 / 종목당 투자금 /
최대 보유 / 일일 매수 한도 / 종목 비중 / 추가매수 / 운용 성향) 을 OS 사용자
설정 폴더의 JSON 파일에 저장한다. 프로그램을 껐다 켜도 유지된다.

**저장하지 않는 것 (절대 금지):** API key / App Secret / 계좌번호 / 비밀번호 /
access token / refresh token / 실거래 허가. 이들은 `.env` 만의 책임이며 본
모듈과 *분리* 된다. 저장 전 secret scan 으로 secret-like key/value 발견 시
`SecretInSettingsError` 로 *저장 거부* (fail-closed).

저장 위치 우선순위:
  1. `AGENT_TRADER_CONFIG_DIR` 환경변수 (테스트 / 운영자 override)
  2. OS 사용자 설정 폴더:
     - Windows: `%APPDATA%/Autotrade/config/`
     - macOS:   `~/Library/Application Support/Autotrade/config/`
     - Linux:   `$XDG_CONFIG_HOME/Autotrade/config/` 또는 `~/.config/Autotrade/config/`
  3. fallback: `backend/.runtime/config/`

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건.
- 안전 flag (`ENABLE_LIVE_TRADING` 등) 변경 0건 — 본 모듈은 자금 *기준* 만 저장.
- `is_live_authorization=False` / `is_order_signal=False` / `contains_secret=False`
  영구.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_log = logging.getLogger("autotrade.settings.persistent")

SCHEMA_VERSION = 1
_VENDOR_DIR = "Autotrade"          # 기존 EXE 의 %APPDATA%\Autotrade\ 와 일관.
_CONFIG_SUBDIR = "config"
SETTINGS_FILENAME = "paper_capital_settings.json"

VALID_RISK_PROFILES = ("CONSERVATIVE", "BALANCED", "AGGRESSIVE")


# ─────────────────────────────────────────────────────────────────────────────
# 기본값 + 한도 (frontend usePaperCapitalSettings 와 정합)
# ─────────────────────────────────────────────────────────────────────────────


DEFAULT_SETTINGS: dict[str, Any] = {
    "total_paper_capital":  10_000_000,
    "per_symbol_allocation": 1_000_000,
    "max_positions":        5,
    "max_daily_buy_amount": 3_000_000,
    "max_symbol_weight_pct": 0.2,
    "allow_additional_buy": False,
    "risk_profile":         "BALANCED",
}

_LIMITS = {
    "total_paper_capital":  (100_000, 10_000_000_000),
    "per_symbol_allocation": (10_000, 10_000_000_000),
    "max_positions":        (1, 100),
    "max_daily_buy_amount": (10_000, 10_000_000_000),
}


# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────


class SecretInSettingsError(ValueError):
    """설정 입력에 secret 추정 패턴 발견 — 저장 거부 (fail-closed)."""


class InvalidPaperCapitalSettingsError(ValueError):
    """검증 실패 — 잘못된 자금 설정 값."""


# ─────────────────────────────────────────────────────────────────────────────
# Secret scan (저장 전 fail-closed)
# ─────────────────────────────────────────────────────────────────────────────


_SECRET_KEY_PATTERNS = (
    "api_key", "apikey", "secret", "app_secret", "appsecret", "access_token",
    "accesstoken", "refresh_token", "refreshtoken", "bearer", "password",
    "passwd", "private_key", "privatekey", "anthropic", "openai", "kis_app_key",
    "kis_app_secret", "account_no", "account_number", "kis_account", "token",
)

_SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"PST[A-Za-z0-9]{30,}"),
    re.compile(r"\b\d{6,}-\d{2,}\b"),     # 한국 계좌번호 형태
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\."),   # JWT
)

# 자금 설정의 *허용된 키* — 이 외의 키는 secret/오용 의심으로 저장 거부.
_ALLOWED_KEYS = frozenset(DEFAULT_SETTINGS.keys())


def scan_for_secrets(data: dict[str, Any]) -> None:
    """secret-like key/value 발견 시 `SecretInSettingsError`. 통과 시 None."""
    if not isinstance(data, dict):
        return
    for k, v in data.items():
        kl = str(k).lower()
        for pat in _SECRET_KEY_PATTERNS:
            if pat in kl:
                raise SecretInSettingsError(
                    f"forbidden settings key (secret-like): {k!r}"
                )
        if isinstance(v, str):
            for rx in _SECRET_VALUE_PATTERNS:
                if rx.search(v):
                    raise SecretInSettingsError(
                        f"forbidden settings value (secret pattern) for key {k!r}"
                    )


# ─────────────────────────────────────────────────────────────────────────────
# Config dir / path resolver
# ─────────────────────────────────────────────────────────────────────────────


def get_config_dir() -> Path:
    """설정 디렉토리 경로 — 없으면 생성. 우선순위: env override → OS → fallback."""
    override = os.environ.get("AGENT_TRADER_CONFIG_DIR")
    if override and override.strip():
        d = Path(override.strip())
    else:
        d = _os_config_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 — 권한/경로 문제 시 fallback.
        d = _fallback_config_dir()
        d.mkdir(parents=True, exist_ok=True)
    return d


def _os_config_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / _VENDOR_DIR / _CONFIG_SUBDIR
        return _fallback_config_dir()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / _VENDOR_DIR / _CONFIG_SUBDIR
    # Linux / 기타.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg and xdg.strip() else (Path.home() / ".config")
    return base / _VENDOR_DIR / _CONFIG_SUBDIR


def _fallback_config_dir() -> Path:
    return Path(__file__).resolve().parents[2] / ".runtime" / "config"


def get_paper_capital_settings_path() -> Path:
    return get_config_dir() / SETTINGS_FILENAME


def config_dir_label() -> str:
    """diagnostics 표시용 *짧은* 라벨 — 전체 경로 노출 최소화."""
    override = os.environ.get("AGENT_TRADER_CONFIG_DIR")
    if override and override.strip():
        return "AGENT_TRADER_CONFIG_DIR"
    system = platform.system()
    return {"Windows": "%APPDATA%/Autotrade/config",
            "Darwin": "~/Library/Application Support/Autotrade/config"}.get(
        system, "~/.config/Autotrade/config")


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PaperCapitalSettingsResult:
    """load/save/reset 결과 — UI 안전 payload. secret 0건."""

    settings:       dict[str, Any]
    source:         str               # PERSISTED / DEFAULT / DEFAULT_CORRUPTED
    reason_message: str
    errors:         list[str] = field(default_factory=list)
    config_label:   str = ""

    is_live_authorization: bool = False
    is_order_signal:       bool = False
    contains_secret:       bool = False
    safe_for_ui:           bool = True

    def __post_init__(self) -> None:
        if self.is_live_authorization is not False:
            raise ValueError("is_live_authorization must be False")
        if self.is_order_signal is not False:
            raise ValueError("is_order_signal must be False")
        if self.contains_secret is not False:
            raise ValueError("contains_secret must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "settings":       dict(self.settings),
            "source":         self.source,
            "reason_message": self.reason_message,
            "errors":         list(self.errors),
            "config_label":   self.config_label,
            "is_live_authorization": self.is_live_authorization,
            "is_order_signal":       self.is_order_signal,
            "contains_secret":       self.contains_secret,
            "safe_for_ui":           self.safe_for_ui,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_paper_capital_settings(
    data: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """입력 → 검증된 설정 dict + 오류 라벨. 잘못된 필드는 *default fallback*.

    잘못된 값은 적용하지 않고 default 로 둔다(errors 에 사유 carry). secret scan
    은 caller(save)가 별도 수행 — 본 함수는 형식/범위 검증만.
    """
    out = dict(DEFAULT_SETTINGS)
    errors: list[str] = []
    if not isinstance(data, dict):
        return out, errors

    total = data.get("total_paper_capital")
    if total is not None:
        lo, hi = _LIMITS["total_paper_capital"]
        if _is_number(total) and lo <= total <= hi:
            out["total_paper_capital"] = int(total)
        else:
            errors.append(f"total_paper_capital must be in [{lo}, {hi}]")

    per = data.get("per_symbol_allocation")
    if per is not None:
        lo, hi = _LIMITS["per_symbol_allocation"]
        if _is_number(per) and lo <= per <= hi:
            if per > out["total_paper_capital"]:
                errors.append("per_symbol_allocation must be <= total_paper_capital")
            else:
                out["per_symbol_allocation"] = int(per)
        else:
            errors.append(f"per_symbol_allocation must be in [{lo}, {hi}]")

    mp = data.get("max_positions")
    if mp is not None:
        lo, hi = _LIMITS["max_positions"]
        if isinstance(mp, int) and not isinstance(mp, bool) and lo <= mp <= hi:
            out["max_positions"] = int(mp)
        else:
            errors.append(f"max_positions must be int in [{lo}, {hi}]")

    md = data.get("max_daily_buy_amount")
    if md is not None:
        lo, hi = _LIMITS["max_daily_buy_amount"]
        if _is_number(md) and lo <= md <= hi:
            if md > out["total_paper_capital"]:
                errors.append("max_daily_buy_amount must be <= total_paper_capital")
            else:
                out["max_daily_buy_amount"] = int(md)
        else:
            errors.append(f"max_daily_buy_amount must be in [{lo}, {hi}]")

    wp = data.get("max_symbol_weight_pct")
    if wp is not None:
        if _is_number(wp) and 0 < wp <= 1:
            out["max_symbol_weight_pct"] = float(wp)
        else:
            errors.append("max_symbol_weight_pct must be in (0, 1]")

    aab = data.get("allow_additional_buy")
    if aab is not None:
        if isinstance(aab, bool):
            out["allow_additional_buy"] = aab
        else:
            errors.append("allow_additional_buy must be boolean")

    rp = data.get("risk_profile")
    if rp is not None:
        norm = str(rp).strip().upper()
        if norm in VALID_RISK_PROFILES:
            out["risk_profile"] = norm
        else:
            # 알 수 없는 값 → BALANCED fallback (frontend normalize 와 정합).
            out["risk_profile"] = "BALANCED"
            errors.append(f"risk_profile must be one of {VALID_RISK_PROFILES}")

    return out, errors


# ─────────────────────────────────────────────────────────────────────────────
# Load / Save / Reset
# ─────────────────────────────────────────────────────────────────────────────


def load_paper_capital_settings() -> PaperCapitalSettingsResult:
    """저장 파일 로드. 없으면 DEFAULT, 깨졌으면 DEFAULT_CORRUPTED + warning."""
    path = get_paper_capital_settings_path()
    label = config_dir_label()
    if not path.exists():
        return PaperCapitalSettingsResult(
            settings=dict(DEFAULT_SETTINGS), source="DEFAULT",
            reason_message="저장된 설정이 없어 기본값을 사용합니다.",
            config_label=label,
        )
    try:
        raw = path.read_text(encoding="utf-8")
        doc = json.loads(raw)
        data = doc.get("settings", {}) if isinstance(doc, dict) else {}
    except Exception as exc:  # noqa: BLE001 — 깨진 JSON / IO 오류.
        _log.warning("[settings] load failed (%s) — falling back to default",
                     type(exc).__name__)
        return PaperCapitalSettingsResult(
            settings=dict(DEFAULT_SETTINGS), source="DEFAULT_CORRUPTED",
            reason_message="설정 파일을 읽을 수 없어 기본값으로 복구했습니다.",
            errors=[f"corrupted_settings_file: {type(exc).__name__}"],
            config_label=label,
        )
    settings, errors = validate_paper_capital_settings(data)
    return PaperCapitalSettingsResult(
        settings=settings, source="PERSISTED",
        reason_message="저장된 Paper 자금 설정을 불러왔습니다.",
        errors=errors, config_label=label,
    )


def save_paper_capital_settings(data: dict[str, Any]) -> PaperCapitalSettingsResult:
    """검증 + secret scan 통과 시 atomic write 로 저장.

    secret-like key/value → `SecretInSettingsError` (저장 거부). 검증 오류가 있어도
    *유효한 필드만* 저장하고 errors carry (잘못된 값은 default 유지).
    """
    scan_for_secrets(data or {})
    # 허용 외 키는 무시 (저장 0건) — 자금 기준만 보관.
    settings, errors = validate_paper_capital_settings(data)

    doc = {
        "version": SCHEMA_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings,
        "safety": {
            "is_live_authorization": False,
            "is_order_signal": False,
            "contains_secret": False,
        },
    }
    path = get_paper_capital_settings_path()
    _atomic_write_json(path, doc)
    return PaperCapitalSettingsResult(
        settings=settings, source="PERSISTED",
        reason_message="Paper 자금 설정이 저장되었습니다.",
        errors=errors, config_label=config_dir_label(),
    )


def reset_paper_capital_settings() -> PaperCapitalSettingsResult:
    """저장 파일 삭제 + 기본값 반환."""
    path = get_paper_capital_settings_path()
    try:
        if path.exists():
            path.unlink()
    except Exception:  # noqa: BLE001
        pass
    return PaperCapitalSettingsResult(
        settings=dict(DEFAULT_SETTINGS), source="DEFAULT",
        reason_message="Paper 자금 설정을 기본값으로 되돌렸습니다.",
        config_label=config_dir_label(),
    )


def _atomic_write_json(path: Path, doc: dict[str, Any]) -> None:
    """임시 파일에 쓰고 os.replace 로 atomic rename — 부분 쓰기 방지."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


__all__ = [
    "SCHEMA_VERSION", "SETTINGS_FILENAME", "DEFAULT_SETTINGS", "VALID_RISK_PROFILES",
    "SecretInSettingsError", "InvalidPaperCapitalSettingsError",
    "PaperCapitalSettingsResult",
    "get_config_dir", "get_paper_capital_settings_path", "config_dir_label",
    "scan_for_secrets", "validate_paper_capital_settings",
    "load_paper_capital_settings", "save_paper_capital_settings",
    "reset_paper_capital_settings",
]
