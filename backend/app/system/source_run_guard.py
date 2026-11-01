"""P0-SAFETY-HARDENING P0-1 — 소스 실행이 설치 EXE 의 운영 KIS 인증정보를 쓰지 못하게 한다.

예전: scripts\\run_watchdog.bat → watchdog_restart_backend.bat → app_desktop_launcher.py(소스)가
%APPDATA%\\Autotrade\\.env(운영 계좌 키·계좌)를 로드하고 나머지는 pydantic 이 backend\\.env 에서
채워(자동매매 플래그 ON) 소스 DB(긴급정지 OFF)로 운영 계좌를 거래할 수 있었다.

규칙: 설치 EXE(PyInstaller frozen)가 아닌 실행에서, 실효 설정의 KIS_APP_KEY 또는 계좌번호(앞 8자리)가
%APPDATA%\\Autotrade\\.env 와 같으면 시작 거부. 판단 실패도 거부(fail-closed).
런처(포트·상태파일 손대기 전)와 FastAPI lifespan(uvicorn 직접 실행 경로) 두 곳에서 같은 함수로 검사.
secret 원문 출력 0 — 일치한 항목 *이름* 만. broker / 주문 경로 import 0.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

OPS_ENV_RELATIVE = Path("Autotrade") / ".env"


def is_installed_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def ops_env_path() -> Path | None:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / OPS_ENV_RELATIVE if appdata else None


def _read_env(p: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _fp(v: str | None) -> str:
    v = (v or "").strip()
    return hashlib.sha256(v.encode("utf-8")).hexdigest() if v else ""


def _digits8(v: str | None) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())[:8]


def ops_credential_overlap(settings: Any) -> list[str]:
    """소스 실행의 실효 설정이 운영 .env 와 겹치는 항목 이름. 설치 앱이거나 운영 .env 가 없으면 []."""
    if is_installed_app():
        return []
    p = ops_env_path()
    if p is None or not p.is_file():
        return []
    ops = _read_env(p)
    matched = []
    if ops.get("KIS_APP_KEY") and _fp(ops["KIS_APP_KEY"]) == _fp(getattr(settings, "kis_app_key", "")):
        matched.append("KIS_APP_KEY")
    acct = _digits8(ops.get("KIS_ACCOUNT_NO"))
    if len(acct) == 8 and acct == _digits8(getattr(settings, "kis_account_no", "")):
        matched.append("KIS_ACCOUNT_NO")
    return matched


def source_run_block_reason(settings: Any = None) -> str | None:
    """시작 거부 사유(None = 허용). 판단 실패는 거부."""
    if is_installed_app():
        return None
    try:
        if settings is None:
            from app.core.config import get_settings
            settings = get_settings()
        matched = ops_credential_overlap(settings)
    except Exception as exc:  # noqa: BLE001
        return (f"SOURCE_OPS_CHECK_FAILED: {type(exc).__name__} — 운영 인증정보 분리 여부를 "
                "확인할 수 없어 시작하지 않습니다")
    if not matched:
        return None
    return (f"SOURCE_RUN_USES_OPS_CREDENTIALS: {', '.join(matched)} 가 설치 앱 운영 설정"
            f"({ops_env_path()})과 같습니다 — 소스 실행은 운영 계좌와 완전히 분리된 인증정보로만 허용됩니다")
