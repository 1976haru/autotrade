"""#57 / 7-05 — backend sidecar build metadata (read-only).

사용자가 실행 중인 backend sidecar 가 어느 main commit / 빌드 시각인지 확인할
수 있도록 build metadata 를 표준 모양으로 반환한다.

값 우선순위 (위가 우선):
  1. `app.system.build_stamp.BUILD_STAMP` — 빌드 시 생성되는 모듈
     (`scripts/generate_build_stamp.py`, gitignore). PyInstaller 가
     `--collect-submodules app` 로 bundle → packaged EXE 에서도 정확.
  2. 환경변수 `AUTOTRADE_*` — launcher / CI 가 주입.
  3. `git` 명령 — dev 환경(.git 존재).
  4. "unknown" fallback — 어떤 경우에도 예외를 던지지 않는다.

절대 invariant:
  - 응답에 Secret / API key / 계좌번호 0건 (commit hash / branch / time / channel
    만). broker / OrderExecutor / route_order import 0건.
  - `is_live_authorization=False`, `contains_secret=False` 불변.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

_UNKNOWN = "unknown"


def _load_stamp() -> dict:
    """빌드 시 생성된 build_stamp 모듈 (없으면 빈 dict)."""
    try:
        from app.system import build_stamp  # type: ignore
        s = getattr(build_stamp, "BUILD_STAMP", None)
        if isinstance(s, dict):
            return s
    except Exception:  # noqa: BLE001
        pass
    return {}


def _git(*args: str) -> Optional[str]:
    """git 명령 결과 (실패/미설치/`.git` 부재 시 None). 예외 0건."""
    try:
        out = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0:
            v = out.stdout.strip()
            return v or None
    except Exception:  # noqa: BLE001
        return None
    return None


def _pick(stamp: dict, key: str, env: str, git_value: Optional[str]) -> str:
    v = stamp.get(key)
    if v:
        return str(v)
    e = os.getenv(env)
    if e and e.strip():
        return e.strip()
    if git_value:
        return git_value
    return _UNKNOWN


def get_build_info() -> dict:
    """표준 build metadata dict. read-only, 예외 0건."""
    stamp = _load_stamp()

    commit_full = _pick(stamp, "commit_full", "AUTOTRADE_GIT_COMMIT_FULL",
                        _git("rev-parse", "HEAD"))
    short_from_full = (
        commit_full[:7] if commit_full and commit_full != _UNKNOWN else None
    )
    commit = _pick(stamp, "commit", "AUTOTRADE_GIT_COMMIT",
                  short_from_full or _git("rev-parse", "--short", "HEAD"))
    branch = _pick(stamp, "branch", "AUTOTRADE_GIT_BRANCH",
                  _git("rev-parse", "--abbrev-ref", "HEAD"))
    build_time = _pick(stamp, "build_time", "AUTOTRADE_BUILD_TIME", None)
    version = _pick(stamp, "version", "AUTOTRADE_APP_VERSION", None)

    channel = _pick(stamp, "channel", "AUTOTRADE_BUILD_CHANNEL", None)
    if channel == _UNKNOWN:
        channel = "paper-beta"
    source = _pick(stamp, "source", "AUTOTRADE_BUILD_SOURCE", None)
    if source == _UNKNOWN:
        source = "github-actions" if os.getenv("GITHUB_ACTIONS") else "local-build"

    # dirty: stamp → env → git status --porcelain → False.
    if "is_dirty" in stamp:
        is_dirty = bool(stamp["is_dirty"])
    else:
        env_dirty = os.getenv("AUTOTRADE_GIT_DIRTY")
        if env_dirty is not None:
            is_dirty = env_dirty.strip().lower() in ("1", "true", "yes", "on")
        else:
            porcelain = _git("status", "--porcelain")
            is_dirty = bool(porcelain) if porcelain is not None else False

    return {
        "version":               version,
        "channel":               channel,
        "commit":                commit,
        "commit_full":           commit_full,
        "branch":                branch,
        "build_time":            build_time,
        "source":                source,
        "is_dirty":              is_dirty,
        "is_live_authorization": False,
        "contains_secret":       False,
    }
