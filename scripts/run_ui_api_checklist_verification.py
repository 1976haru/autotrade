#!/usr/bin/env python3
"""FINAL-UI-API-01 — 체크리스트 기능 UI/API 통합 검증 CLI (read-only).

체크리스트 카드들이 실제 탭에 mount 되고 API client method + backend GET route 에
연결되어 있는지 검증한다.

- manifest mode (기본, offline): 서버 없이 정적으로 카드↔탭↔client↔route 매니페스트 검증.
- http mode: 실행 중인 backend 의 **read-only GET endpoint 만** 호출해 상태/secret 확인.

CLAUDE.md 절대 원칙:
- read-only. broker / OrderExecutor / route_order / KIS 실제 API / 주문 호출 0건.
- http mode 는 GET endpoint 만 — POST / 주문 / start / approve endpoint 0건.
- secret / 계좌 원문 출력 0건 (응답 secret 발견 시 마스킹 + FAIL 처리, 원문 미출력).
- 안전 flag / `.env` 변경 0건.

사용:
    python scripts/run_ui_api_checklist_verification.py --mode manifest \\
        --markdown reports/final_audit/ui_api_check.md
    python scripts/run_ui_api_checklist_verification.py --mode http \\
        --base-url http://127.0.0.1:8000 --markdown reports/final_audit/ui_api_http_check.md

exit code:
    0: PASS / WARN 허용
    1: FAIL
    2: 실행 오류
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.system.ui_api_checklist import (  # noqa: E402
    FAIL,
    WARN,
    evaluate_http_results,
    http_targets,
    render_markdown,
    run_manifest_checks,
)

# 응답 본문 secret 스캔용 보수적 패턴 (원문 출력 금지 — 발견 여부 bool 만 사용).
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"\b\d{8}-\d{2}\b"),  # 한국 계좌번호 8-2
]


def _scan_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


def _http_get(base_url: str, path: str, timeout: float = 10.0) -> tuple[int, bool]:
    """GET 호출 → (status, secret_found). 네트워크 오류는 status 0."""
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, _scan_secret(body)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = ""
        return e.code, _scan_secret(body)
    except (urllib.error.URLError, OSError, TimeoutError):
        return 0, False


def _report_to_dict(report) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": report.mode,
        "overall_verdict": report.overall_verdict,
        "ui_api_ready": report.ui_api_ready,
        "counts": report.counts,
        "is_live_authorization": report.is_live_authorization,
        "broker_order_sent": report.broker_order_sent,
        "contains_secret": report.contains_secret,
        "items": [
            {
                "name": i.name, "card": i.card, "tab": i.tab,
                "mounted": i.mounted, "client_wired": i.client_wired,
                "route_present": i.route_present, "has_card_test": i.has_card_test,
                "verdict": i.verdict, "detail": i.detail,
            }
            for i in report.items
        ],
        "http_results": [
            {
                "name": r.name, "http_path": r.http_path, "status": r.status,
                "ok": r.ok, "secret_found": r.secret_found,
                "verdict": r.verdict, "detail": r.detail,
            }
            for r in report.http_results
        ],
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="체크리스트 UI/API 통합 검증 (read-only, 실전 아님).")
    p.add_argument("--mode", choices=["manifest", "http"], default="manifest")
    p.add_argument("--base-url", default="http://127.0.0.1:8000",
                   help="http mode 에서 호출할 backend base URL")
    p.add_argument("--strict", action="store_true",
                   help="WARN 도 실패로 간주 (exit 1)")
    p.add_argument("--output", default=None, help="JSON 리포트 경로")
    p.add_argument("--markdown", default=None, help="Markdown 리포트 경로")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        args = _parse_args(argv)
        if args.mode == "manifest":
            report = run_manifest_checks(_REPO_ROOT)
        else:
            raw: dict[str, tuple[int, bool]] = {}
            for it in http_targets():
                raw[it.http_path] = _http_get(args.base_url, it.http_path)
            report = evaluate_http_results(raw)

        data = _report_to_dict(report)
        md = render_markdown(report)

        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] JSON 리포트: {out}")
        if args.markdown:
            mdp = Path(args.markdown)
            mdp.parent.mkdir(parents=True, exist_ok=True)
            mdp.write_text(md, encoding="utf-8")
            print(f"[OK] Markdown 리포트: {mdp}")

        c = report.counts
        print(f"mode={report.mode} overall={report.overall_verdict} "
              f"ui_api_ready={report.ui_api_ready}")
        print(f"PASS={c.get(PASS_KEY,0)} WARN={c.get(WARN,0)} FAIL={c.get(FAIL,0)}")
        print("NOTE: UI/API 검증 전용 — 실제 KIS API 0건, 주문 0건, 실전 승인 아님.")

        if report.overall_verdict == FAIL:
            return 1
        if args.strict and c.get(WARN, 0) > 0:
            return 1
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


PASS_KEY = "PASS"

if __name__ == "__main__":
    raise SystemExit(main())
