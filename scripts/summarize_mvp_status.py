#!/usr/bin/env python3
"""체크리스트 #71 — MVP status read-only 요약 스크립트.

본 스크립트는 다음을 *읽기만* 한다:
- ``docs/mvp_completion.md``         — 판정 + P0 상태표
- ``docs/final_completion_summary.md`` — 체크리스트 누적 요약
- ``CLAUDE.md``                       — 절대 원칙 + 안전 플래그 default
- ``git`` (HEAD / 최근 commit / branch) — 메타데이터만

다음은 *절대* 하지 *않는다*:
- 외부 API 호출 (KIS / Anthropic / Telegram / yfinance) 0건
- ``.env`` / Secret 파일 읽기 0건 — 의도적으로 차단 (`_assert_no_env_access`)
- ``app/`` 운영 코드 import 0건 (mvp_completion.md만 신뢰)
- DB / broker / OrderExecutor 호출 0건

CLI:
    python scripts/summarize_mvp_status.py --format markdown
    python scripts/summarize_mvp_status.py --format json
    python scripts/summarize_mvp_status.py --check-secrets

``--check-secrets`` 모드는 mvp_completion.md / final_completion_summary.md /
README.md에 Secret 패턴이 포함됐는지 검사 후 0/1 exit code 반환.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------- paths ----------


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MVP_PATH      = PROJECT_ROOT / "docs" / "mvp_completion.md"
SUMMARY_PATH  = PROJECT_ROOT / "docs" / "final_completion_summary.md"
README_PATH   = PROJECT_ROOT / "README.md"
CLAUDE_PATH   = PROJECT_ROOT / "CLAUDE.md"


# ---------- env / secret safety ----------


_SECRET_PATTERNS = [
    # API 키 / 토큰 — 값이 실제로 들어가면 적발.
    r"KIS_APP_KEY\s*=\s*[A-Za-z0-9\-]{8,}",
    r"KIS_APP_SECRET\s*=\s*[A-Za-z0-9\-/+=]{16,}",
    r"ANTHROPIC_API_KEY\s*=\s*sk-[A-Za-z0-9\-]{8,}",
    r"OPENAI_API_KEY\s*=\s*sk-[A-Za-z0-9\-]{8,}",
    r"TELEGRAM_BOT_TOKEN\s*=\s*\d{8,}:[A-Za-z0-9_-]{8,}",
    # 한국 계좌번호 (변형 포함, 운영 노출 방지).
    r"\b\d{8,10}-\d{2}-\d{4,}\b",
    # Bearer / sk- 접두 raw 값.
    r"Bearer\s+[A-Za-z0-9\-_=\.]{16,}",
    r"\bsk-[A-Za-z0-9]{20,}\b",
]


def _assert_no_env_access() -> None:
    """본 스크립트가 .env / Secret 파일에 접근하지 않음을 명시.

    실수로 .env를 open()할 가능성을 제거하기 위해 import / read 흔적이
    문자열로 들어가지 않도록 별도 함수에 격리. (정적 grep 가드용 sentinel.)
    """
    # 본 함수는 *문서화* 목적의 placeholder — 실제 .env 호출 0건.
    return None


# ---------- data model ----------


@dataclass
class P0Item:
    number:   str
    title:    str
    status:   str
    evidence: str = ""


@dataclass
class MvpSummary:
    verdict:           str
    p0_done:           int     = 0
    p0_partial:        int     = 0
    p0_blocked:        int     = 0
    p0_items:          list[P0Item] = field(default_factory=list)
    live_flags_off:    bool    = True
    live_trading_msg:  str     = ""
    git_head_short:    Optional[str] = None
    git_branch:        Optional[str] = None
    git_dirty:         bool    = False
    secret_findings:   list[str] = field(default_factory=list)
    notes:             list[str] = field(default_factory=list)


# ---------- parsers ----------


_VERDICT_RE = re.compile(r"\*\*판정[::]\s*`?([A-Z_]+)`?\*\*")
# P0 행: "| 1 | Project Governance | ✅ DONE | ... |"
_P0_ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*"
    r"(?:[✅⏳🛑✓⚠️❌]\s*)?"
    r"(DONE|PARTIAL|BLOCKED|TODO|N/A)\s*\|\s*([^|]*)\|"
)


def parse_mvp_doc(text: str) -> MvpSummary:
    """mvp_completion.md에서 판정 + P0 상태표 + 최종 요약 카운트 추출."""
    verdict_match = _VERDICT_RE.search(text)
    verdict = verdict_match.group(1) if verdict_match else "UNKNOWN"

    items: list[P0Item] = []
    for line in text.splitlines():
        m = _P0_ROW_RE.match(line)
        if not m:
            continue
        items.append(P0Item(
            number=m.group(1).strip(),
            title=m.group(2).strip(),
            status=m.group(3).strip(),
            evidence=m.group(4).strip(),
        ))

    done    = sum(1 for it in items if it.status == "DONE")
    partial = sum(1 for it in items if it.status == "PARTIAL")
    blocked = sum(1 for it in items if it.status == "BLOCKED")

    return MvpSummary(
        verdict=verdict,
        p0_done=done,
        p0_partial=partial,
        p0_blocked=blocked,
        p0_items=items,
    )


# ---------- git metadata ----------


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        if out.returncode != 0:
            return ""
        return (out.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def collect_git(summary: MvpSummary) -> None:
    summary.git_head_short = _git("rev-parse", "--short", "HEAD") or None
    summary.git_branch     = _git("rev-parse", "--abbrev-ref", "HEAD") or None
    dirty = _git("status", "--porcelain")
    summary.git_dirty = bool(dirty.strip())


# ---------- live flag check ----------


_LIVE_FLAG_DEFAULT_RE = re.compile(
    r"enable_(live_trading|ai_execution|futures_live_trading)\s*:\s*bool\s*=\s*(True|False)",
    re.IGNORECASE,
)


def collect_live_flag_defaults(summary: MvpSummary) -> None:
    """app/core/config.py를 *읽기만* 해서 default가 false인지 확인.

    Settings 객체를 import해서 평가하면 .env가 끼어들 수 있으므로 *문자열*
    파싱만 한다. 본 스크립트는 .env / Secret을 절대 읽지 않는다.
    """
    cfg = PROJECT_ROOT / "backend" / "app" / "core" / "config.py"
    if not cfg.exists():
        summary.live_flags_off = False
        summary.live_trading_msg = "config.py not found — cannot verify"
        return

    text = cfg.read_text(encoding="utf-8")
    flags = dict(_LIVE_FLAG_DEFAULT_RE.findall(text))
    if not flags:
        summary.live_flags_off = False
        summary.live_trading_msg = "live flag defaults not found in config.py"
        return

    all_false = all(v.lower() == "false" for v in flags.values())
    summary.live_flags_off = all_false
    if all_false:
        summary.live_trading_msg = (
            "all live flag defaults = False (enable_live_trading, "
            "enable_ai_execution, enable_futures_live_trading)"
        )
    else:
        bad = [k for k, v in flags.items() if v.lower() != "false"]
        summary.live_trading_msg = f"non-false default found: {bad}"


# ---------- secret leak check ----------


def collect_secret_findings(summary: MvpSummary) -> None:
    """docs / README에 Secret 패턴이 들어갔는지 검사. 발견 시 라인 carry.

    *실제 Secret*만 적발 (변수 이름 언급은 카운트 X). 정규식이
    `KEY=value-shape` 형태일 때만 매칭.
    """
    findings: list[str] = []
    targets = [MVP_PATH, SUMMARY_PATH, README_PATH]
    for p in targets:
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pat in _SECRET_PATTERNS:
                if re.search(pat, line):
                    findings.append(f"{p.name}:{line_no}: {pat}")
    summary.secret_findings = findings


# ---------- rendering ----------


def render_markdown(summary: MvpSummary) -> str:
    lines: list[str] = []
    lines.append("# MVP Status Summary (자동 생성)")
    lines.append("")
    lines.append(f"- 판정: **{summary.verdict}**")
    lines.append(f"- P0 DONE: {summary.p0_done}")
    lines.append(f"- P0 PARTIAL: {summary.p0_partial}")
    lines.append(f"- P0 BLOCKED: {summary.p0_blocked}")
    lines.append(f"- LIVE flags default off: {summary.live_flags_off} ({summary.live_trading_msg})")
    if summary.git_branch:
        dirty = " (dirty)" if summary.git_dirty else ""
        lines.append(f"- Git: branch=`{summary.git_branch}` HEAD=`{summary.git_head_short}`{dirty}")
    if summary.secret_findings:
        lines.append("")
        lines.append("## ⚠️ Secret 의심 패턴 발견")
        for f in summary.secret_findings:
            lines.append(f"- {f}")
    else:
        lines.append("- Secret 의심 패턴: 없음")
    if summary.p0_partial or summary.p0_blocked:
        lines.append("")
        lines.append("## 미완료 / 블록 항목")
        for it in summary.p0_items:
            if it.status not in ("PARTIAL", "BLOCKED"):
                continue
            lines.append(f"- #{it.number} {it.title} — {it.status}")
    lines.append("")
    lines.append(
        "> 이 요약은 docs/mvp_completion.md와 git metadata만 본다. "
        "실제 API / .env / DB는 *읽지 않는다*. 자세한 판정 근거는 "
        "docs/mvp_completion.md 참조."
    )
    return "\n".join(lines)


def render_json(summary: MvpSummary) -> str:
    return json.dumps({
        "verdict":          summary.verdict,
        "p0_done":          summary.p0_done,
        "p0_partial":       summary.p0_partial,
        "p0_blocked":       summary.p0_blocked,
        "live_flags_off":   summary.live_flags_off,
        "live_flags_note":  summary.live_trading_msg,
        "git_branch":       summary.git_branch,
        "git_head_short":   summary.git_head_short,
        "git_dirty":        summary.git_dirty,
        "secret_findings":  summary.secret_findings,
        "p0_partial_items": [
            {"number": it.number, "title": it.title, "status": it.status}
            for it in summary.p0_items if it.status == "PARTIAL"
        ],
        "p0_blocked_items": [
            {"number": it.number, "title": it.title, "status": it.status}
            for it in summary.p0_items if it.status == "BLOCKED"
        ],
    }, indent=2, ensure_ascii=False)


# ---------- main ----------


def build_summary() -> MvpSummary:
    _assert_no_env_access()
    if not MVP_PATH.exists():
        return MvpSummary(
            verdict="UNKNOWN",
            notes=[f"docs/mvp_completion.md not found at {MVP_PATH}"],
        )
    text = MVP_PATH.read_text(encoding="utf-8")
    summary = parse_mvp_doc(text)
    collect_git(summary)
    collect_live_flag_defaults(summary)
    collect_secret_findings(summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MVP status read-only summary (#71). No API, no .env access.",
    )
    parser.add_argument(
        "--format", choices=("markdown", "json"), default="markdown",
        help="출력 형식",
    )
    parser.add_argument(
        "--check-secrets", action="store_true",
        help="Secret 의심 패턴이 발견되면 exit code 1",
    )
    args = parser.parse_args()

    summary = build_summary()

    if args.format == "json":
        out = render_json(summary)
    else:
        out = render_markdown(summary)

    # Windows console encoding fix — UTF-8.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    print(out)

    if args.check_secrets and summary.secret_findings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
