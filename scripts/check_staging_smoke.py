"""체크리스트 #67: Staging smoke test.

`docker compose -f docker-compose.staging.yml up -d`로 띄운 staging stack의
backend / frontend / 안전 invariant를 한 번에 확인.

절대 원칙:
- 본 스크립트는 *staging 컨테이너*만 호출 (localhost:18000 / 15173).
- 실 broker / KIS / 외부 API 호출 0건. backend의 /api/status는 read-only.
- LIVE flag가 true이면 본 스크립트는 *실패한다* — staging 정책 lock.

사용:
    python scripts/check_staging_smoke.py
    python scripts/check_staging_smoke.py --backend http://staging.example:18000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

# Windows 콘솔에서 한국어/UTF-8 출력 안전 — cp949 fallback 회피.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


DEFAULT_BACKEND  = "http://localhost:18000"
DEFAULT_FRONTEND = "http://localhost:15173"


def _http_get(url: str, *, timeout: float = 5.0) -> tuple[int, bytes]:
    """단순 HTTP GET — 외부 의존성 없이 stdlib만 사용.

    연결 실패 / DNS 오류 등은 status=0 + 빈 body로 graceful return.
    호출자는 status != 200 분기에서 FAIL로 처리.
    """
    req = urllib.request.Request(  # noqa: S310 — staging localhost only
        url,
        headers={"User-Agent": "autotrader-staging-smoke/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"")
    except (urllib.error.URLError, OSError, TimeoutError):
        # staging 컨테이너가 안 떠 있거나 네트워크 도달 불가 — smoke FAIL
        # 시그널로 status=0 반환. 트레이스백 노출하지 않음.
        return 0, b""


def _check(name: str, ok: bool, detail: str = "") -> bool:
    marker = "✓" if ok else "✗"
    print(f"  {marker} {name}" + (f"  ({detail})" if detail else ""))
    return ok


def check_backend(base_url: str) -> bool:
    print(f"[backend] {base_url}")
    all_ok = True

    # 1) /api/status — 200
    status, body = _http_get(f"{base_url}/api/status")
    all_ok &= _check("GET /api/status responded", status == 200,
                     f"status={status}")
    if status != 200:
        return False

    # 2) JSON 파싱
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        return _check("response is valid JSON", False, str(e))
    all_ok &= _check("response is valid JSON", True)

    # 3) staging 안전 invariant — LIVE flag 모두 false
    flags = {
        "enable_live_trading":         payload.get("enable_live_trading"),
        "enable_ai_execution":         payload.get("enable_ai_execution"),
        "enable_futures_live_trading": payload.get("enable_futures_live_trading"),
    }
    for key, val in flags.items():
        all_ok &= _check(
            f"{key} is False (staging invariant)",
            val is False,
            f"got {val!r}",
        )

    # 4) mode는 SIMULATION 또는 PAPER (LIVE_* 금지)
    mode = payload.get("default_mode") or ""
    is_safe_mode = mode in ("SIMULATION", "PAPER")
    all_ok &= _check(
        "default_mode is SIMULATION or PAPER (no LIVE)",
        is_safe_mode,
        f"got {mode!r}",
    )

    # 5) /docs OpenAPI 노출 — staging은 docs 접근 가능 (운영은 별도 정책)
    docs_status, _ = _http_get(f"{base_url}/docs")
    all_ok &= _check("GET /docs reachable", docs_status == 200,
                     f"status={docs_status}")

    return all_ok


def check_frontend(base_url: str) -> bool:
    print(f"[frontend] {base_url}")
    all_ok = True
    status, body = _http_get(base_url)
    all_ok &= _check("GET / responded", status == 200, f"status={status}")
    if status == 200:
        # SPA shell에 root div 존재
        text = body.decode("utf-8", errors="ignore")
        all_ok &= _check('SPA shell contains <div id="root">',
                         '<div id="root">' in text or 'id="root"' in text)
    return all_ok


def _check_no_secret_in_status(base_url: str) -> bool:
    """/api/status 응답에 token / chat_id / app_secret 값이 *값으로* 노출되지
    않음을 검사."""
    status, body = _http_get(f"{base_url}/api/status")
    if status != 200:
        return _check("status reachable for secret check", False)
    text = body.decode("utf-8", errors="ignore").lower()
    forbidden_substrings = [
        "bearer ", "sk-", "psto", "psk-",     # 일반 token prefix
        "telegram_bot_token", "kis_app_secret", "anthropic_api_key",
    ]
    leak = [needle for needle in forbidden_substrings if needle in text]
    return _check(
        "no secret-looking patterns in /api/status response",
        not leak,
        f"found: {leak}" if leak else "",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend",  default=DEFAULT_BACKEND)
    parser.add_argument("--frontend", default=DEFAULT_FRONTEND)
    parser.add_argument("--no-frontend", action="store_true",
                        help="frontend 컨테이너 검사 생략 (backend만 검증)")
    args = parser.parse_args()

    print("=" * 60)
    print("staging smoke test — 실 broker / 외부 API 호출 0건")
    print("=" * 60)

    backend_ok  = check_backend(args.backend)
    secret_ok   = _check_no_secret_in_status(args.backend)
    frontend_ok = True
    if not args.no_frontend:
        frontend_ok = check_frontend(args.frontend)

    all_ok = backend_ok and secret_ok and frontend_ok

    print("-" * 60)
    print("RESULT: " + ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
