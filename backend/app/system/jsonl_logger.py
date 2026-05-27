"""구조화 JSONL 로그 + Secret sanitize.

매 라인 1 JSON. 기록 *전* sanitize 적용 — secret 키/값은 ***MASKED*** 또는
[REDACTED] 로 치환(드롭이 아니라 마스킹). logs/*.jsonl 에 secret 원문 0건.

허용 표기: `*_present=true`, `***MASKED***`, `[REDACTED]`.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 로그에 절대 원문이 나오면 안 되는 키 (소문자 부분일치).
_SECRET_KEY_PARTS: tuple[str, ...] = (
    "app_key", "app_secret", "kis_app_key", "kis_app_secret",
    "account_no", "authorization", "access_token", "refresh_token",
    "token", "secret", "password", "passwd", "api_key", "apikey",
)
# `*_present` 같은 boolean 플래그는 허용 (원문 아님).
_ALLOWED_SUFFIXES: tuple[str, ...] = ("_present", "_present_flag")

_MASKED = "***MASKED***"
_REDACTED = "[REDACTED]"

# 값 자체가 secret 패턴이면 [REDACTED].
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}"),  # JWT
    re.compile(r"\b\d{8}-\d{2}\b"),  # 한국 계좌번호 8-2
)


def _is_secret_key(key: str) -> bool:
    k = str(key).lower()
    if any(k.endswith(suf) for suf in _ALLOWED_SUFFIXES):
        return False
    return any(part in k for part in _SECRET_KEY_PARTS)


def _redact_value(val: str) -> str:
    out = val
    for pat in _SECRET_VALUE_PATTERNS:
        out = pat.sub(_REDACTED, out)
    return out


def sanitize_for_log(obj: Any) -> Any:
    """재귀 sanitize — secret 키는 ***MASKED***, secret 패턴 값은 [REDACTED]."""
    if isinstance(obj, dict):
        clean: dict[str, Any] = {}
        for k, v in obj.items():
            # secret 키는 *문자열 값* 만 마스킹 — bool/숫자/None (예:
            # is_live_authorization=False, *_present=true) 은 secret 이 아니므로 보존.
            if _is_secret_key(str(k)) and isinstance(v, str) and v:
                clean[str(k)] = _MASKED
            else:
                clean[str(k)] = sanitize_for_log(v)
        return clean
    if isinstance(obj, (list, tuple)):
        return [sanitize_for_log(x) for x in obj]
    if isinstance(obj, str):
        return _redact_value(obj)
    return obj


class JsonlLogger:
    """append-only JSONL 로거. 기록 전 sanitize. thread-safe."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def log(self, level: str, event: str, **fields: Any) -> dict[str, Any]:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": str(level).upper(),
            "event": str(event),
            **fields,
            # 안전 불변 — 로그는 실거래 권한과 무관.
            "is_live_authorization": False,
        }
        clean = sanitize_for_log(record)
        line = json.dumps(clean, ensure_ascii=False)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return clean

    def info(self, event: str, **f: Any) -> dict[str, Any]:
        return self.log("INFO", event, **f)

    def warn(self, event: str, **f: Any) -> dict[str, Any]:
        return self.log("WARN", event, **f)

    def error(self, event: str, **f: Any) -> dict[str, Any]:
        return self.log("ERROR", event, **f)
