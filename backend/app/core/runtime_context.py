"""INSTALL-UX-FIX-01 — 실행 컨텍스트 감지 (read-only).

설치된 EXE(packaged) / GitHub Actions(CI) / 소스 개발(source) 을 구분해, *소스 환경 전용*
점검(EXE 빌드 입력 파일·docs·report 스크립트 존재 등)이 설치본에서 FAIL 로 잘못 표시되지
않도록 한다. **실제 런타임 안전 점검(안전 플래그·live 주문 차단·security)은 컨텍스트와
무관하게 그대로 유지** 한다.

본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API 를 import 하지 않는다.
"""

from __future__ import annotations

import os
import sys

SOURCE_DEV = "SOURCE_DEV"          # 소스 저장소 개발 환경
PACKAGED_RUNTIME = "PACKAGED_RUNTIME"   # 설치된 EXE 런타임 (PyInstaller 등)
CI_BUILD = "CI_BUILD"              # GitHub Actions 등 CI 빌드


def detect_app_runtime(env: dict[str, str] | None = None) -> str:
    """현재 실행 컨텍스트를 SOURCE_DEV / PACKAGED_RUNTIME / CI_BUILD 로 판별.

    우선순위: 명시 env(AUTOTRADE_RUNTIME) > PyInstaller frozen/build_stamp(packaged) >
    CI 환경변수 > 소스 개발.
    """
    e = env if env is not None else os.environ
    explicit = str(e.get("AUTOTRADE_RUNTIME", "") or "").strip().lower()
    if explicit in ("packaged", "exe", "packaged_runtime", "desktop"):
        return PACKAGED_RUNTIME
    if explicit in ("ci", "ci_build", "github_actions"):
        return CI_BUILD
    if explicit in ("source", "source_dev", "dev"):
        return SOURCE_DEV
    # PyInstaller 패키징 / build_stamp 번들 → packaged.
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        return PACKAGED_RUNTIME
    try:
        import app.system.build_stamp  # noqa: F401  (빌드 시 생성, 번들에만 존재)
        return PACKAGED_RUNTIME
    except Exception:  # noqa: BLE001
        pass
    if str(e.get("GITHUB_ACTIONS", "")).lower() == "true" or str(e.get("CI", "")).lower() == "true":
        return CI_BUILD
    return SOURCE_DEV


def is_source_dev(runtime: str | None = None) -> bool:
    """소스 개발 환경 여부 — 소스 전용 점검(빌드 입력/문서/스크립트 존재)은 여기서만 FAIL."""
    return (runtime or detect_app_runtime()) == SOURCE_DEV
