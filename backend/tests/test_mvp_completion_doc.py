"""체크리스트 #71 — mvp_completion.md / summary 스크립트 invariant 가드.

본 테스트는 *문서*와 *스크립트*에 대한 정적 가드만 검사한다.
실제 API 호출, broker, .env 접근 0건 — 운영 코드는 변경되지 않았다.
"""

from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MVP_PATH      = PROJECT_ROOT / "docs" / "mvp_completion.md"
SCRIPT_PATH   = PROJECT_ROOT / "scripts" / "summarize_mvp_status.py"
README_PATH   = PROJECT_ROOT / "README.md"
FINAL_PATH    = PROJECT_ROOT / "docs" / "final_completion_summary.md"


# ---------- doc existence ----------


def test_mvp_completion_md_exists():
    assert MVP_PATH.exists(), "docs/mvp_completion.md should exist"


def test_summarize_mvp_status_script_exists():
    assert SCRIPT_PATH.exists(), "scripts/summarize_mvp_status.py should exist"


# ---------- doc content invariants ----------


def test_mvp_doc_declares_verdict():
    text = MVP_PATH.read_text(encoding="utf-8")
    assert re.search(
        r"MVP_READY_FOR_PAPER_SHADOW|MVP_PARTIAL|MVP_BLOCKED", text,
    ), "verdict marker must be present"


def test_mvp_doc_states_live_trading_is_not_allowed():
    text = MVP_PATH.read_text(encoding="utf-8")
    # 핵심 문구가 반드시 포함되어야 함 — MVP는 실거래 허가가 아니다.
    assert "실거래" in text and "허가" in text
    assert "Paper" in text or "paper" in text
    assert "Shadow" in text or "shadow" in text


def test_mvp_doc_lists_live_flag_invariants():
    text = MVP_PATH.read_text(encoding="utf-8")
    for flag in (
        "ENABLE_LIVE_TRADING",
        "ENABLE_AI_EXECUTION",
        "ENABLE_FUTURES_LIVE_TRADING",
    ):
        assert flag in text, f"safety flag {flag} must be referenced in doc"


def test_mvp_doc_does_not_promise_immediate_live_execution():
    text = MVP_PATH.read_text(encoding="utf-8").lower()
    # "MVP 완료 = 즉시 실거래 허가" 같은 표현이 들어가서는 안 된다.
    # "MVP 완료 ... 실거래 허가" 같은 *허가*표현이 'MVP 완료'와 같은 문장에
    # 모순으로 들어가지 않는지 — 단순화하여 강한 표현 차단.
    forbidden = [
        "즉시 실거래 시작",
        "live 자동매매 허용",
        "ai 자동실행 허용",
    ]
    for f in forbidden:
        assert f not in text, f"forbidden phrase in doc: {f!r}"


# ---------- summarize script invariants ----------


def test_summary_script_does_not_call_external_api():
    """본 스크립트는 외부 API / broker / OrderExecutor / DB engine 호출 0건.

    docstring 안에 *해당 단어 언급*은 허용 — invariant를 *설명*하는 문서이므로.
    실제 import / 호출 형태 ("import X", "X(...)" 패턴)만 차단.
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = [
        "import httpx", "import requests",
        "from anthropic", "from openai", "from telegram",
        "import sqlite3", "create_engine(",
        "broker.place_order(", "broker.cancel_order(",
        "route_order(",
    ]
    for needle in forbidden:
        assert needle not in text, (
            f"summarize_mvp_status.py must not include: {needle!r}"
        )


def test_summary_script_does_not_read_env_files():
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = [".env", "load_dotenv", "DotEnv", "BaseSettings"]
    for needle in forbidden:
        # 단, 패턴 *문서화*에 들어간 ".env" 언급은 주석/docstring에만 허용 —
        # 코드 단에서 open(".env") / read 호출은 *없어야* 한다.
        if needle == ".env":
            # docstring에서 ".env / Secret" 언급은 정상.
            # 단 ``open(".env"`` / `Path(".env"` 식의 read는 금지.
            assert 'open(".env"' not in text
            assert 'Path(".env"' not in text
            continue
        assert needle not in text, (
            f"summary script must not import: {needle!r}"
        )


def test_summary_script_runs_and_produces_verdict(tmp_path):
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--format", "json"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert "MVP_READY_FOR_PAPER_SHADOW" in proc.stdout
    assert "p0_done" in proc.stdout


def test_summary_script_secret_check_exits_clean(tmp_path):
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--check-secrets"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert proc.returncode == 0, (
        "secret check must succeed when docs are clean. stdout:\n"
        + (proc.stdout or "") + "\nstderr:\n" + (proc.stderr or "")
    )


# ---------- README cross-link ----------


def test_readme_links_to_mvp_completion():
    text = README_PATH.read_text(encoding="utf-8")
    assert "mvp_completion.md" in text, (
        "README should link to docs/mvp_completion.md after #71"
    )


def test_final_completion_summary_mentions_71():
    text = FINAL_PATH.read_text(encoding="utf-8")
    assert "#71" in text or "71" in text, (
        "final_completion_summary.md should record #71 entry"
    )


# ---------- secret pattern guard ----------


_SECRET_PATTERNS = [
    r"KIS_APP_KEY\s*=\s*[A-Za-z0-9\-]{8,}",
    r"KIS_APP_SECRET\s*=\s*[A-Za-z0-9\-/+=]{16,}",
    r"ANTHROPIC_API_KEY\s*=\s*sk-[A-Za-z0-9\-]{8,}",
    r"OPENAI_API_KEY\s*=\s*sk-[A-Za-z0-9\-]{8,}",
    r"TELEGRAM_BOT_TOKEN\s*=\s*\d{8,}:[A-Za-z0-9_-]{8,}",
    r"\b\d{8,10}-\d{2}-\d{4,}\b",
    r"Bearer\s+[A-Za-z0-9\-_=\.]{16,}",
]


def test_no_secret_pattern_in_mvp_doc():
    text = MVP_PATH.read_text(encoding="utf-8")
    for pat in _SECRET_PATTERNS:
        m = re.search(pat, text)
        assert m is None, (
            f"secret pattern leaked into mvp_completion.md: {pat!r} -> {m.group(0)!r}"
        )


def test_no_secret_pattern_in_summary_script():
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    # 본 스크립트는 정규식을 *정의*하므로 SECRET_PATTERNS 내부에 정규식이 나오는
    # 것은 정상 — 실제 *값*이 들어갔는지만 따로 검사.
    # 단순화: KIS_APP_KEY=ABC123... 같은 등호+값 패턴이 정규식 문자열 *밖*에
    # 있는지 검사하기는 까다로워, 본 테스트는 *raw 값으로 보이는* 패턴 외
    # 모듈에 들어갈 일 없는 패턴(KIS_APP_KEY=실값) 검사로 충분.
    raw_value_patterns = [
        r"KIS_APP_KEY\s*=\s*[A-Z]{4,}\d{4,}",
        r"sk-ant-[A-Za-z0-9]{20,}",
    ]
    for pat in raw_value_patterns:
        m = re.search(pat, text)
        assert m is None, (
            f"secret-shaped value in summarize_mvp_status.py: {m.group(0) if m else None}"
        )
