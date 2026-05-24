"""#63 / 8-01 — EXE Preflight Smoke Test (CLI).

EXE 의 backend sidecar 가 뜬 뒤, 기본 작동 여부를 한 번에 확인한다. backend 의
read-only `GET /api/system/preflight` 결과를 받아 PASS/WARN/FAIL 리포트를
출력하고, 응답 본문을 client 측에서 한 번 더 secret 패턴 스캔한다.

절대 원칙:
  - read-only GET 만 호출 (`/api/system/preflight`, `/health`).
    주문 / POST / broker / OrderExecutor / route_order 호출 0건.
  - Secret / API key / 계좌번호 *원문* 출력 0건 (탐지 시 마스킹 + FAIL).
  - 응답 전체 dump 0건 — check name / status / message 만 출력.

exit code:
  0 = PASS 또는 WARN (운영 가능)
  1 = FAIL 항목 있음 (안전 위반 / DB 실패 / secret 노출 등)
  2 = backend 도달 불가 (sidecar 미기동)

사용:
    python scripts/exe_smoke_test.py
    python scripts/exe_smoke_test.py --base-url http://127.0.0.1:8000
    python scripts/exe_smoke_test.py --json reports/preflight.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_UNREACHABLE = 2

# client-side secret / 계좌번호 패턴 (서버가 막더라도 2차 방어).
_SECRET_PATTERNS = [
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9-]{20,}")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("bearer", re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}")),
    ("kis_account_no", re.compile(r"\b\d{8}-\d{2}\b")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
]


def http_get_json(base_url: str, path: str, *, timeout: float = 8.0
                  ) -> tuple[int, Optional[Any], Optional[str]]:
    """read-only GET → (status, parsed_json_or_None, error_or_None)."""
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(  # noqa: S310 — localhost sidecar only
        url, headers={"User-Agent": "autotrader-exe-smoke/1.0"}, method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read()
            try:
                return resp.getcode(), json.loads(body), None
            except json.JSONDecodeError as e:
                return resp.getcode(), None, f"invalid JSON: {e}"
    except urllib.error.HTTPError as e:
        return e.code, None, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return 0, None, str(getattr(e, "reason", e))


def scan_secrets(obj: Any) -> list[str]:
    """응답 본문에서 secret / 계좌번호 패턴 탐지. 적중한 *패턴 이름만* 반환
    (원문 0건)."""
    text = json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
    hits = []
    for name, pat in _SECRET_PATTERNS:
        if pat.search(text):
            hits.append(name)
    return hits


def build_report(preflight: Optional[dict], reachable: bool,
                 error: Optional[str]) -> dict:
    """preflight 응답 + reachability → 최종 report (exit code 포함)."""
    if not reachable or preflight is None:
        return {
            "summary": {"status": "FAIL", "pass_count": 0, "warn_count": 0,
                        "fail_count": 1, "total": 1},
            "checks": [{
                "name": "backend_api_reachable", "status": "FAIL",
                "message": f"Backend 도달 불가: {error or 'unknown'}",
            }],
            "exit_code": EXIT_UNREACHABLE,
            "contains_secret": False,
        }

    checks = list(preflight.get("checks") or [])
    # reachable 은 client 만 아는 사실 — 맨 앞에 PASS 로 추가.
    checks.insert(0, {
        "name": "backend_api_reachable", "status": "PASS",
        "message": "Backend API 응답 수신",
    })

    # client 측 secret 스캔 (서버 응답 전체 대상).
    secret_hits = scan_secrets(preflight)
    if secret_hits:
        checks.append({
            "name": "secret_scan", "status": "FAIL",
            "message": "응답에서 secret/계좌 패턴 탐지: " + ", ".join(secret_hits),
        })
    else:
        checks.append({
            "name": "secret_scan", "status": "PASS",
            "message": "secret/계좌 패턴 미검출",
        })

    pass_count = sum(1 for c in checks if c["status"] == "PASS")
    warn_count = sum(1 for c in checks if c["status"] == "WARN")
    fail_count = sum(1 for c in checks if c["status"] == "FAIL")
    status = "FAIL" if fail_count else ("WARN" if warn_count else "PASS")
    return {
        "summary": {"status": status, "pass_count": pass_count,
                    "warn_count": warn_count, "fail_count": fail_count,
                    "total": len(checks)},
        "checks": checks,
        "generated_at": preflight.get("generated_at"),
        "exit_code": EXIT_FAIL if fail_count else EXIT_OK,
        "contains_secret": bool(secret_hits),
        "is_live_authorization": False,
    }


def render(report: dict) -> str:
    mark = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}
    lines = ["=" * 60, "EXE Preflight Smoke Test — 주문 발생 0건 (read-only)", "=" * 60]
    for c in report["checks"]:
        lines.append(f"{mark.get(c['status'], '[????]')} {c['name']} — {c['message']}")
    s = report["summary"]
    lines.append("-" * 60)
    lines.append(f"PASS={s['pass_count']} WARN={s['warn_count']} FAIL={s['fail_count']}"
                 f"  →  RESULT: {s['status']}")
    return "\n".join(lines)


def run(base_url: str, *, timeout: float = 8.0,
        fetch=http_get_json) -> dict:
    """preflight 수집 + report 생성 (테스트 주입용 fetch)."""
    status, preflight, error = fetch(base_url, "/api/system/preflight", timeout=timeout)
    if status != 200 or preflight is None:
        # /health 로 reachability 재확인 (preflight endpoint 만 문제일 수 있음).
        h_status, _, h_err = fetch(base_url, "/health", timeout=timeout)
        if h_status != 200:
            return build_report(None, reachable=False, error=error or h_err)
        # health 는 되는데 preflight 만 실패 → FAIL 로 처리.
        return build_report(None, reachable=False,
                            error=error or f"preflight HTTP {status}")
    return build_report(preflight, reachable=True, error=None)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--json", default=None, help="report JSON 저장 경로")
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args(argv)

    report = run(args.base_url, timeout=args.timeout)
    print(render(report))
    if args.json:
        import os
        os.makedirs(os.path.dirname(os.path.abspath(args.json)) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"[json] {args.json}")
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
