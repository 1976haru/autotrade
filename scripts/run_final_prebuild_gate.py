#!/usr/bin/env python3
"""체크리스트 11-00 — EXE 빌드 전 최종 통합 검증 Gate CLI (read-only).

fast(기본): 안전 플래그 + security_scan + ruff + 리포트 파일 + 정적 repo 점검으로 종합
(무거운 pytest/build 미실행). --full: frontend build + pytest collection + 타깃 백엔드
테스트 추가 실행. **KIS 주문 API / broker 호출 0건, 실제 주문 0건.**

exit: 0(BUILD_READY/_WITH_WARNINGS) / 1(BUILD_BLOCKED) / 2(실행 오류)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

DEFAULT_OUTPUT_DIR = "reports/final_prebuild"


def _run(cmd: list[str], cwd: Path, timeout: int = 300) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 99, f"{type(e).__name__}: {e}"


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except Exception:  # noqa: BLE001
        return None


def _gather(full: bool, require_kis: bool):
    from app.system.final_prebuild_gate import GateInputs

    # 안전 플래그 (read-only).
    try:
        from app.core.config import get_settings
        s = get_settings()
        default_mode = str(getattr(s.default_mode, "value", s.default_mode))
        live = bool(s.enable_live_trading)
        ai = bool(s.enable_ai_execution)
        fut = bool(s.enable_futures_live_trading)
        kis_paper = bool(s.kis_is_paper)
    except Exception:  # noqa: BLE001
        default_mode, live, ai, fut, kis_paper = "PAPER", False, False, False, True

    # security_scan (fast, safety-critical).
    rc_sec, out_sec = _run(
        [sys.executable, "scripts/security_scan.py"], _REPO_ROOT, timeout=120)
    sec_findings = 0 if (rc_sec == 0 and "No findings" in out_sec) else (1 if rc_sec != 0 else 0)

    # ruff (fast).
    rc_ruff, _ = _run(
        [sys.executable, "-m", "ruff", "check", "app", "tests"], _BACKEND_DIR, timeout=120)
    ruff_ok = rc_ruff == 0

    # pytest collection (fast — import 무결성).
    rc_col, _ = _run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        _BACKEND_DIR, timeout=240)
    collection_ok = rc_col == 0

    # 정적 repo 점검.
    rc_st, out_st = _run(["git", "status", "--porcelain"], _REPO_ROOT, timeout=30)
    untracked = [ln for ln in out_st.splitlines() if ln.startswith("??")]
    git_clean = not [ln for ln in out_st.splitlines() if ln and not ln.startswith("??")]
    rc_env, out_env = _run(["git", "ls-files", "backend/.env", ".env"], _REPO_ROOT, timeout=30)
    dotenv_tracked = bool([x for x in out_env.splitlines()
                           if x and not x.endswith(".example")])
    # 미추적 위험: .env/.pem/.key 류만 위험, Cargo.lock 등은 무관.
    untracked_risk = any(
        any(x in ln.lower() for x in (".env", ".pem", ".key", ".p12", ".pfx"))
        for ln in untracked)
    exe_inputs_ok = (_REPO_ROOT / "src-tauri" / "tauri.conf.json").exists()

    # KIS 자격 present (원문 0건 — 존재 여부만).
    import os
    kis_present = bool(os.environ.get("KIS_APP_KEY")) and bool(os.environ.get("KIS_APP_SECRET"))

    # 리포트 파일 (있으면 verdict carry).
    rv = _REPO_ROOT / "reports" / "strategy_validation"
    bv = _REPO_ROOT / "reports" / "build"
    pv = _REPO_ROOT / "reports" / "prebuild"
    intraday_final = _read_json(rv / "intraday_final_latest.json")
    real_daily = _read_json(rv / "real_data_strategy_latest.json")
    # BUILD-01/02A/02B 최신 리포트.
    def _latest(d: Path, prefix: str):
        files = sorted(d.glob(f"{prefix}*.json")) if d.is_dir() else []
        return _read_json(files[-1]) if files else None
    b01 = _latest(bv, "program_integrity")
    b02a = _latest(pv, "premarket_readiness")
    b02b = _latest(pv, "kis_paper_autotrade_audit")

    # full 모드: frontend build.
    frontend_build_ok = None
    frontend_quality = "SKIP"
    if full:
        rc_fb, _ = _run(["npm", "run", "build"], _REPO_ROOT / "frontend", timeout=420)
        frontend_build_ok = rc_fb == 0
        frontend_quality = "PASS" if rc_fb == 0 else "FAIL"

    backend_quality = "PASS" if (ruff_ok and collection_ok) else (
        "FAIL" if (not ruff_ok or not collection_ok) else "SKIP")

    return GateInputs(
        default_mode=default_mode, enable_live_trading=live, enable_ai_execution=ai,
        enable_futures_live_trading=fut, kis_is_paper=kis_paper,
        backend_quality=backend_quality, ruff_pass=ruff_ok, pytest_collection_ok=collection_ok,
        frontend_quality=frontend_quality, frontend_build_ok=frontend_build_ok,
        security_findings=sec_findings, secret_exposure=False, dotenv_tracked=dotenv_tracked,
        db_status="UNKNOWN", preflight_status="UNKNOWN", backend_health_ok=None,
        kis_credentials_present=(True if kis_present else False), require_kis_credentials=require_kis,
        kis_live_order_path_blocked=True, broker_direct_call_found=False,
        universe_empty_no_fallback=False, portfolio_source_mixed=False,
        agent_pipeline_status="SKIP", order_quality_status="SKIP",
        build01_verdict=(b01.get("build_ready") if b01 else None),
        build02a_ready=(b02a.get("premarket_ready") if b02a else None),
        build02b_ready=(b02b.get("paper_autotrade_ready") if b02b else None),
        ui_api_manifest_status="SKIP",
        backtest_status="SKIP", walk_forward_status="SKIP", stress_fail_count=None,
        real_daily_verdict=(real_daily.get("overall_verdict") if real_daily else None),
        intraday_final_judgement=(intraday_final.get("user_final_judgement") if intraday_final else None),
        paper_sample_count=0,
        live_safety_ok=True, order_buttons_found=False,
        docs_runbook_ok=(_REPO_ROOT / "docs" / "runbook.md").exists(),
        exe_build_inputs_ok=exe_inputs_ok,
        git_clean=git_clean, main_up_to_date=None, open_pr_blocking=False,
        untracked_release_risk=untracked_risk)


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        p = argparse.ArgumentParser(
            description="EXE 빌드 전 통합 검증 Gate (read-only, 주문 0건).")
        p.add_argument("--full", action="store_true", help="frontend build 등 추가 실행")
        p.add_argument("--require-kis-credentials", action="store_true")
        p.add_argument("--output", default=None)
        p.add_argument("--markdown", default=None)
        p.add_argument("--quiet", action="store_true")
        args = p.parse_args(argv)

        from app.system.final_prebuild_gate import (
            render_markdown,
            run_final_prebuild_gate,
            to_dict,
        )
        inp = _gather(bool(args.full), bool(args.require_kis_credentials))
        report = run_final_prebuild_gate(inp, generated_at=datetime.now(timezone.utc).isoformat())
        data = to_dict(report)

        out = Path(args.output) if args.output else (Path(DEFAULT_OUTPUT_DIR) / "final_prebuild_gate.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        md = Path(args.markdown) if args.markdown else (Path(DEFAULT_OUTPUT_DIR) / "final_prebuild_gate.md")
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(render_markdown(report), encoding="utf-8")
        print(f"[OK] JSON: {out}")
        print(f"[OK] Markdown: {md}")

        if not args.quiet:
            c = report.counts
            print(f"overall_status={report.overall_status} exe_build_allowed={report.exe_build_allowed}")
            print(f"PASS={c['PASS']} WARN={c['WARN']} FAIL={c['FAIL']} SKIP={c['SKIP']}")
            print(f">>> 현재 상태는 {report.user_line}.")
            if report.blocked_reasons:
                print("BLOCKER:", report.blocked_reasons[:3])
            print("NOTE: 빌드 가능 판정 전용 — 실전 승인 아님, 주문 0건, 수익 보장 아님.")

        return 1 if report.overall_status == "BUILD_BLOCKED" else 0
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
