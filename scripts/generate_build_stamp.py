#!/usr/bin/env python
"""#57 / 7-05 — backend sidecar build stamp 생성기.

빌드 시점(주로 `build_backend_sidecar.ps1` / CI)의 git 메타데이터를
`backend/app/system/build_stamp.py` 로 굽는다. PyInstaller 가
`--collect-submodules app` 로 bundle 하므로 packaged EXE 의 backend 가
`/api/system/build-info` 에서 정확한 commit / 빌드 시각을 보고할 수 있다.

값 우선순위: 환경변수(`AUTOTRADE_*`) → git 명령 → "unknown".

절대 원칙:
  - Secret / API key / 계좌번호 0건 — git hash / branch / time / channel 만.
  - 생성 파일은 gitignore (빌드 산출물, 커밋 금지).
  - git 명령 실패해도 예외 없이 "unknown" — 빌드를 막지 않는다.

사용:
    python scripts/generate_build_stamp.py
    python scripts/generate_build_stamp.py --channel paper-beta --source github-actions
"""

from __future__ import annotations

import argparse
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_UNKNOWN = "unknown"


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            v = out.stdout.strip()
            return v or None
    except Exception:  # noqa: BLE001
        return None
    return None


def _pick(env: str, git_value: str | None, default: str = _UNKNOWN) -> str:
    e = os.getenv(env)
    if e and e.strip():
        return e.strip()
    if git_value:
        return git_value
    return default


def build_stamp(channel: str | None, source: str | None) -> dict:
    commit_full = _pick("AUTOTRADE_GIT_COMMIT_FULL", _git("rev-parse", "HEAD"))
    short = commit_full[:7] if commit_full != _UNKNOWN else None
    commit = _pick("AUTOTRADE_GIT_COMMIT", short or _git("rev-parse", "--short", "HEAD"))
    branch = _pick("AUTOTRADE_GIT_BRANCH", _git("rev-parse", "--abbrev-ref", "HEAD"))
    porcelain = _git("status", "--porcelain")
    env_dirty = os.getenv("AUTOTRADE_GIT_DIRTY")
    if env_dirty is not None:
        is_dirty = env_dirty.strip().lower() in ("1", "true", "yes", "on")
    else:
        is_dirty = bool(porcelain) if porcelain is not None else False
    return {
        "version":     _pick("AUTOTRADE_APP_VERSION", None),
        "channel":     channel or os.getenv("AUTOTRADE_BUILD_CHANNEL") or "paper-beta",
        "commit":      commit,
        "commit_full": commit_full,
        "branch":      branch,
        "build_time":  os.getenv("AUTOTRADE_BUILD_TIME")
                       or datetime.now(timezone.utc).isoformat(),
        "source":      source or os.getenv("AUTOTRADE_BUILD_SOURCE")
                       or ("github-actions" if os.getenv("GITHUB_ACTIONS") else "local-build"),
        "is_dirty":    is_dirty,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default=None)
    ap.add_argument("--source", default=None)
    ap.add_argument(
        "--out", default=None,
        help="출력 경로 (기본: backend/app/system/build_stamp.py)",
    )
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    out = Path(args.out) if args.out else (
        repo_root / "backend" / "app" / "system" / "build_stamp.py"
    )
    stamp = build_stamp(args.channel, args.source)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 주의: Python 모듈로 import 되므로 *Python 리터럴* 로 써야 한다
    # (json.dumps 는 true/false 를 내보내 NameError 발생). pformat 은
    # True/False/유효 dict 리터럴을 보장한다.
    import pprint
    out.write_text(
        '"""AUTO-GENERATED build stamp (#57 / 7-05). DO NOT COMMIT — gitignored.\n'
        'Secret 0건 — git hash / branch / time / channel 만."""\n\n'
        f"BUILD_STAMP = {pprint.pformat(stamp, sort_dicts=False, width=100)}\n",
        encoding="utf-8",
    )
    # commit / branch / time 만 출력 — secret 0건.
    print(f"[build-stamp] {out}")
    print(f"  commit={stamp['commit']} branch={stamp['branch']} "
          f"channel={stamp['channel']} dirty={stamp['is_dirty']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
