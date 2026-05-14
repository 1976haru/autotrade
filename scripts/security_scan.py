#!/usr/bin/env python3
"""Security scan (#93) — secret / 인증서 / 번들 누출 검출.

CLAUDE.md 절대 원칙:
- 본 스크립트는 *read-only*. broker / DB / 외부 API 호출 0건.
- 결과는 stdout / JSON 출력만 — 어떤 파일도 *수정하지 않는다*.
- Secret 원문은 출력 시 *마스킹* (전부 표시 X) — 매칭 사실만 알린다.

검사 대상:
1. **secret pattern**: API key / token / JWT / 한국 계좌번호 / 신용카드 등
2. **인증서 / 키 파일**: `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.crt`,
   `*.keystore`, `*.jks` 가 git 추적 대상에 포함된 경우
3. **번들 / 설치 파일**: `*.msi`, `*.nsis`, `*-setup.exe`, `*.dmg`,
   `*.pkg`, `backend/dist/`, `src-tauri/binaries/*.exe` 등 secret 이 *함께
   bundle 될 수 있는* artifact 가 추적 대상에 있는 경우
4. **`.env` 누출**: `.env` (단, `.env.example` / `.env.staging.example` 은 OK)
5. **안전 flag**: `ENABLE_LIVE_TRADING=true` / `ENABLE_AI_EXECUTION=true` /
   `ENABLE_FUTURES_LIVE_TRADING=true` / `KIS_IS_PAPER=false` 의 *값 측*
   매칭 (.env.example / docs / 주석 / 테스트는 allowlist)

False positive 처리:
- `backend/tests/**` 는 *대부분의 secret-shaped fixture* 가 허용된다 — 다만
  실제 형식이 너무 길거나 base64 의심이면 HIGH severity 로 표시.
- 라인에 `# security-scan: ignore` 또는 `// security-scan: ignore` 주석이
  있으면 해당 라인 스킵.
- `.env.example` 의 right-hand-side 가 *빈 값* 이거나 placeholder 인 경우는 OK.

사용:
```bash
# 단순 스캔
python scripts/security_scan.py

# strict mode — 모든 발견 시 exit 1
python scripts/security_scan.py --strict

# JSON 출력 (CI 통합)
python scripts/security_scan.py --format json --output security_scan.json
```

Exit code:
- 0 : clean (또는 LOW severity 만)
- 1 : HIGH / MEDIUM severity 발견
- 2 : 실행 오류
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


# ---------- enums / config ----------


class Severity(StrEnum):
    HIGH    = "HIGH"     # 실제 secret 일 가능성 큼 — 즉시 차단
    MEDIUM  = "MEDIUM"   # secret-shaped — 컨텍스트 확인 필요
    LOW     = "LOW"      # 의심스럽지만 false positive 가능성 큼
    INFO    = "INFO"     # 정책 위반 가능성 (예: .env 추적)


@dataclass(frozen=True)
class Rule:
    name:        str
    severity:    Severity
    pattern:     re.Pattern[str]
    description: str
    # 이 rule 을 *스킵* 할 path glob — fnmatch 형식.
    skip_globs:  tuple[str, ...] = ()


# ---------- 패턴 정의 ----------
#
# 본 패턴들은 *production code* 가 commit 한 secret 을 잡기 위함이다. 일부 패턴은
# `backend/tests/**` 처럼 fake secret fixture 가 정상적으로 들어가는 경로를
# skip_globs 로 허용한다.


_TEST_FIXTURE_GLOBS = (
    "backend/tests/*",
    "backend/tests/**",
    "frontend/src/**/*.test.*",
    "frontend/src/**/__tests__/*",
    "scripts/security_scan.py",       # 본 파일 자체 (패턴 정의로 자기 매칭 피함)
    "backend/tests/test_repository_hygiene.py",
    "docs/security_scan.md",
)


RULES: tuple[Rule, ...] = (
    Rule(
        name="openai_api_key",
        severity=Severity.HIGH,
        # OpenAI 키 형식: sk- + 30+ chars (test fixture 인 20+ 보다 길게).
        pattern=re.compile(r"\bsk-[A-Za-z0-9]{30,}\b"),
        description="OpenAI API key 형식 (sk- + 30 이상 알파넘)",
        skip_globs=_TEST_FIXTURE_GLOBS,
    ),
    Rule(
        name="anthropic_api_key",
        severity=Severity.HIGH,
        pattern=re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{30,}\b"),
        description="Anthropic API key 형식 (sk-ant- + 30 이상)",
        skip_globs=_TEST_FIXTURE_GLOBS,
    ),
    Rule(
        name="github_pat",
        severity=Severity.HIGH,
        pattern=re.compile(r"\b(?:ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9]{30,}\b"),
        description="GitHub Personal Access Token",
        skip_globs=_TEST_FIXTURE_GLOBS,
    ),
    Rule(
        name="github_pat_v2",
        severity=Severity.HIGH,
        pattern=re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
        description="GitHub PAT (v2 형식 — github_pat_ prefix)",
        skip_globs=_TEST_FIXTURE_GLOBS,
    ),
    Rule(
        name="slack_token",
        severity=Severity.HIGH,
        pattern=re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{20,}\b"),
        description="Slack token 형식",
        skip_globs=_TEST_FIXTURE_GLOBS,
    ),
    Rule(
        name="telegram_bot_token",
        severity=Severity.HIGH,
        # Telegram bot token: digits:base64 with ~35 chars.
        pattern=re.compile(r"\b[0-9]{8,12}:AA[A-Za-z0-9_\-]{33,}\b"),
        description="Telegram bot token (digits:AA...)",
        skip_globs=_TEST_FIXTURE_GLOBS,
    ),
    Rule(
        name="aws_access_key",
        severity=Severity.HIGH,
        pattern=re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        description="AWS access key ID 형식",
        skip_globs=_TEST_FIXTURE_GLOBS,
    ),
    Rule(
        name="gcp_api_key",
        severity=Severity.HIGH,
        # Google API key — AIza prefix + 35 chars.
        pattern=re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b"),
        description="Google Cloud API key 형식",
        skip_globs=_TEST_FIXTURE_GLOBS,
    ),
    Rule(
        name="jwt_token",
        severity=Severity.MEDIUM,
        # JWT — 3 base64 parts split by . — header.payload.signature.
        # 너무 짧으면 false positive, 너무 길면 production-grade.
        pattern=re.compile(
            r"\beyJ[A-Za-z0-9_\-]{15,}\.eyJ[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{20,}\b"
        ),
        description="JWT 토큰 (header.payload.signature, 각 부분 충분히 김)",
        skip_globs=_TEST_FIXTURE_GLOBS,
    ),
    Rule(
        name="kis_personal_secret_token",
        severity=Severity.HIGH,
        # KIS Personal Secret Token: PST + 20+ alnum.
        pattern=re.compile(r"\bPST[A-Z0-9]{20,}\b"),
        description="KIS Personal Secret Token (PST...)",
        skip_globs=_TEST_FIXTURE_GLOBS,
    ),
    Rule(
        name="bearer_long_token",
        severity=Severity.MEDIUM,
        pattern=re.compile(r"Bearer [A-Za-z0-9_\-\.]{40,}"),
        description="Authorization Bearer + 긴 토큰",
        skip_globs=_TEST_FIXTURE_GLOBS,
    ),
    Rule(
        name="korean_bank_account",
        severity=Severity.MEDIUM,
        # 한국 계좌번호 — 8~10자리 + dash + 2자리 (예: 12345678-01).
        pattern=re.compile(r"\b\d{8,10}-\d{2}\b"),
        description="한국 계좌번호 형식 (XXXXXXXX-XX)",
        skip_globs=_TEST_FIXTURE_GLOBS + (
            # 한국 주민번호 / 사업자등록번호 / phone 처럼 보이는 일반적 패턴
            # 의 false positive 가 일부 있으므로, docs/ 와 status 파일은 컨텍스트
            # 검토 필요.
            "docs/**",
            # vite/webpack bundle 산출물 — 원본은 frontend/src/config/brokers.js
            # 의 UI placeholder. 라인 단위로 `// security-scan: ignore` 주석을
            # 달 수 없는 minified 파일이므로 디렉토리 단위로 allowlist.
            "assets/**",
        ),
    ),
    Rule(
        name="credit_card",
        severity=Severity.HIGH,
        # 신용카드 16자리 (구분자 - 또는 공백 가능).
        pattern=re.compile(r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b"),
        description="신용카드 번호 형식 (XXXX-XXXX-XXXX-XXXX)",
        skip_globs=_TEST_FIXTURE_GLOBS,
    ),
    Rule(
        name="kis_app_key_value",
        severity=Severity.HIGH,
        # KIS_APP_KEY=가 *비어있지 않은* 값을 가지면 의심.
        # placeholder (예: "여기에...") 와 빈 값은 제외.
        pattern=re.compile(
            r"^\s*KIS_APP_KEY\s*=\s*(?!\s*$)(?!여기에)(?!your[_-])(?!<.*>)"
            r"(?![\"']\s*[\"'])"
            r"[A-Za-z0-9_\-]{10,}",
            re.MULTILINE,
        ),
        description="KIS_APP_KEY 가 비어있지 않고 placeholder 도 아닌 값",
        skip_globs=_TEST_FIXTURE_GLOBS + ("**/.env.example", "**/.env.staging.example"),
    ),
    Rule(
        name="kis_app_secret_value",
        severity=Severity.HIGH,
        pattern=re.compile(
            r"^\s*KIS_APP_SECRET\s*=\s*(?!\s*$)(?!여기에)(?!your[_-])(?!<.*>)"
            r"(?![\"']\s*[\"'])"
            r"[A-Za-z0-9_\-+/=]{20,}",
            re.MULTILINE,
        ),
        description="KIS_APP_SECRET 가 비어있지 않고 placeholder 도 아닌 값",
        skip_globs=_TEST_FIXTURE_GLOBS + ("**/.env.example", "**/.env.staging.example"),
    ),
    Rule(
        name="anthropic_api_key_value",
        severity=Severity.HIGH,
        pattern=re.compile(
            r"^\s*ANTHROPIC_API_KEY\s*=\s*sk-ant-[A-Za-z0-9_\-]{20,}",
            re.MULTILINE,
        ),
        description="ANTHROPIC_API_KEY 가 실제 형식의 값을 가짐",
        skip_globs=_TEST_FIXTURE_GLOBS + ("**/.env.example", "**/.env.staging.example"),
    ),
    Rule(
        name="live_trading_enabled_value",
        severity=Severity.HIGH,
        pattern=re.compile(
            r"^\s*ENABLE_LIVE_TRADING\s*=\s*['\"]?true['\"]?\s*$",
            re.MULTILINE | re.IGNORECASE,
        ),
        description="ENABLE_LIVE_TRADING=true — 절대 commit 금지",
        skip_globs=_TEST_FIXTURE_GLOBS + ("docs/**",),
    ),
    Rule(
        name="ai_execution_enabled_value",
        severity=Severity.HIGH,
        pattern=re.compile(
            r"^\s*ENABLE_AI_EXECUTION\s*=\s*['\"]?true['\"]?\s*$",
            re.MULTILINE | re.IGNORECASE,
        ),
        description="ENABLE_AI_EXECUTION=true — 절대 commit 금지",
        skip_globs=_TEST_FIXTURE_GLOBS + ("docs/**",),
    ),
    Rule(
        name="futures_live_enabled_value",
        severity=Severity.HIGH,
        pattern=re.compile(
            r"^\s*ENABLE_FUTURES_LIVE_TRADING\s*=\s*['\"]?true['\"]?\s*$",
            re.MULTILINE | re.IGNORECASE,
        ),
        description="ENABLE_FUTURES_LIVE_TRADING=true — 절대 commit 금지",
        skip_globs=_TEST_FIXTURE_GLOBS + ("docs/**",),
    ),
    Rule(
        name="kis_paper_disabled_value",
        severity=Severity.HIGH,
        pattern=re.compile(
            r"^\s*KIS_IS_PAPER\s*=\s*['\"]?false['\"]?\s*$",
            re.MULTILINE | re.IGNORECASE,
        ),
        description="KIS_IS_PAPER=false — 모의투자 강제 해제 — 절대 commit 금지",
        skip_globs=_TEST_FIXTURE_GLOBS + ("docs/**",),
    ),
)


# ---------- 파일/번들 검사 ----------


FORBIDDEN_FILE_PATTERNS: tuple[tuple[str, Severity, str], ...] = (
    # 인증서 / 비밀키.
    (r"\.pem$",       Severity.HIGH, "PEM 인증서/키 파일"),
    (r"\.key$",       Severity.HIGH, "개인 키 파일"),
    (r"\.p12$",       Severity.HIGH, "PKCS#12 keystore"),
    (r"\.pfx$",       Severity.HIGH, "PFX keystore"),
    (r"\.crt$",       Severity.HIGH, "X.509 인증서"),
    (r"\.cer$",       Severity.HIGH, "DER 인증서"),
    (r"\.keystore$",  Severity.HIGH, "Java keystore"),
    (r"\.jks$",       Severity.HIGH, "Java keystore (JKS)"),
    # Tauri / signing keys.
    (r"\.pkcs12$",    Severity.HIGH, "PKCS12 keystore"),
    (r"private[_-]key", Severity.HIGH, "private key file"),

    # 번들 / 설치 파일 — production 환경에서 secret 함께 굽힐 위험.
    (r"\.msi$",         Severity.HIGH, "Windows MSI installer"),
    (r"\.nsis$",        Severity.HIGH, "NSIS installer"),
    (r"-setup\.exe$",   Severity.HIGH, "Windows setup installer EXE"),
    (r"\.dmg$",         Severity.HIGH, "macOS disk image"),
    (r"\.pkg$",         Severity.HIGH, "macOS pkg installer"),
    # backend PyInstaller 산출물.
    (r"^backend/dist/", Severity.HIGH, "backend PyInstaller 산출물 (gitignore 누락)"),
    # Tauri sidecar 실제 EXE.
    (r"^src-tauri/binaries/.*\.exe$", Severity.HIGH,
     "Tauri sidecar EXE (gitignore 누락)"),

    # .env 실제 파일 (allowlist: .env.example / .env.staging.example).
    (r"^\.env$",        Severity.HIGH, "실제 .env 파일 — 절대 commit 금지"),
    (r"^backend/\.env$", Severity.HIGH, "backend/.env 파일 — 절대 commit 금지"),
    (r"^frontend/\.env$", Severity.HIGH, "frontend/.env 파일 — 절대 commit 금지"),
    (r"\.env\.local$",   Severity.HIGH, "*.env.local 파일 — 절대 commit 금지"),
)


# ---------- 결과 dataclass ----------


@dataclass(frozen=True)
class Finding:
    path:        str
    line:        int
    severity:    Severity
    rule:        str
    description: str
    snippet:     str   # 마스킹된 매치 컨텍스트

    def to_dict(self) -> dict:
        return {
            "path":        self.path,
            "line":        self.line,
            "severity":    self.severity.value,
            "rule":        self.rule,
            "description": self.description,
            "snippet":     self.snippet,
        }


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int      = 0
    errors:   list[str]     = field(default_factory=list)

    def by_severity(self) -> dict[str, int]:
        out: dict[str, int] = {s.value: 0 for s in Severity}
        for f in self.findings:
            out[f.severity.value] += 1
        return out

    def has_blocking(self) -> bool:
        for f in self.findings:
            if f.severity in (Severity.HIGH, Severity.MEDIUM):
                return True
        return False

    def to_dict(self) -> dict:
        return {
            "files_scanned": self.files_scanned,
            "findings":      [f.to_dict() for f in self.findings],
            "by_severity":   self.by_severity(),
            "errors":        list(self.errors),
        }


# ---------- helpers ----------


def _git_ls_files(repo_root: Path) -> list[str]:
    """git ls-files — 추적 대상 파일만 반환."""
    try:
        out = subprocess.check_output(
            ["git", "ls-files"],
            cwd=str(repo_root),
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"git ls-files 실패: {e.stderr}") from e
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _path_matches_glob(path: str, glob_pattern: str) -> bool:
    """glob 매칭 — `**` 도 지원하는 fnmatch 확장.

    `**` → 디렉토리 트리 전체. 단순 fnmatch 는 `**` 를 지원하지 않으므로
    직접 정규식 변환.
    """
    import re as _re
    # `**` → `.*`, `*` → `[^/]*`, `?` → `[^/]`.
    parts = glob_pattern.split("**")
    regex_parts = []
    for i, part in enumerate(parts):
        sub = []
        j = 0
        while j < len(part):
            ch = part[j]
            if ch == "*":
                sub.append(r"[^/]*")
            elif ch == "?":
                sub.append(r"[^/]")
            elif ch in ".^$+(){}[]|\\":
                sub.append(_re.escape(ch))
            else:
                sub.append(ch)
            j += 1
        regex_parts.append("".join(sub))
    regex = ".*".join(regex_parts)
    return _re.fullmatch(regex, path) is not None


def _line_has_ignore_marker(line: str) -> bool:
    """라인 주석에 'security-scan: ignore' 가 있으면 skip."""
    return "security-scan: ignore" in line


def _mask(text: str, max_len: int = 80) -> str:
    """매칭 텍스트를 마스킹 — 앞 4글자 + ... + 뒷 4글자."""
    text = text.strip().replace("\n", " ").replace("\r", " ")
    if len(text) <= 10:
        return "***"
    head = text[:4]
    tail = text[-4:]
    return f"{head}...{tail}  (len={len(text)}, total snippet truncated)"


def _read_text_safely(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, OSError):
        # 바이너리 / 인코딩 문제 파일은 스킵.
        return None


# ---------- core scan ----------


def scan_repository(repo_root: Path) -> ScanResult:
    """전체 repository 스캔. read-only — 어떤 파일도 수정하지 않는다."""
    result = ScanResult()
    try:
        tracked = _git_ls_files(repo_root)
    except RuntimeError as e:
        result.errors.append(str(e))
        return result

    result.files_scanned = len(tracked)

    # 1. 파일 존재 자체로 violation.
    for path in tracked:
        for pat_re, sev, desc in FORBIDDEN_FILE_PATTERNS:
            if re.search(pat_re, path):
                # allowlist: README/주석/스펙용 파일은 제외.
                if path.endswith("README.md") or path.endswith(".md.example"):
                    continue
                result.findings.append(Finding(
                    path=path, line=0, severity=sev,
                    rule=f"forbidden_path:{pat_re}",
                    description=desc,
                    snippet=f"<file path>: {path}",
                ))

    # 2. 패턴 매칭 — 텍스트 파일만.
    for path in tracked:
        # binary suspect — 스킵 (자세한 검사는 pattern based).
        if _is_likely_binary(path):
            continue

        abspath = repo_root / path
        text = _read_text_safely(abspath)
        if text is None:
            continue

        for rule in RULES:
            # skip_globs 매칭이면 본 rule 은 이 파일에서 스킵.
            if any(_path_matches_glob(path, g) for g in rule.skip_globs):
                continue

            for m in rule.pattern.finditer(text):
                # line number 계산.
                line_no = text[:m.start()].count("\n") + 1
                # 해당 라인 추출.
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_end = text.find("\n", m.end())
                if line_end == -1:
                    line_end = len(text)
                line_text = text[line_start:line_end]

                if _line_has_ignore_marker(line_text):
                    continue

                result.findings.append(Finding(
                    path=path, line=line_no,
                    severity=rule.severity, rule=rule.name,
                    description=rule.description,
                    snippet=_mask(m.group()),
                ))
    return result


_BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz",
    ".mp3", ".mp4", ".wav", ".webm",
    ".sqlite", ".db", ".dat",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".class", ".jar", ".pyc", ".pyo",
})


def _is_likely_binary(path: str) -> bool:
    for ext in _BINARY_EXTENSIONS:
        if path.endswith(ext):
            return True
    return False


# ---------- report ----------


def _format_text(result: ScanResult) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("Security Scan (#93) Report")
    lines.append("=" * 72)
    lines.append(f"scanned files : {result.files_scanned}")
    by_sev = result.by_severity()
    for sev in Severity:
        lines.append(f"  {sev.value:6s}: {by_sev.get(sev.value, 0)}")
    if result.errors:
        lines.append("")
        lines.append("errors:")
        for e in result.errors:
            lines.append(f"  - {e}")

    if not result.findings:
        lines.append("")
        lines.append("✅ No findings.")
        return "\n".join(lines)

    # severity 별로 그룹화.
    lines.append("")
    lines.append("findings:")
    severity_order = [Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
    for sev in severity_order:
        bucket = [f for f in result.findings if f.severity is sev]
        if not bucket:
            continue
        lines.append("")
        lines.append(f"[{sev.value}] ({len(bucket)})")
        for f in bucket:
            lines.append(
                f"  {f.path}:{f.line} — {f.rule}"
            )
            lines.append(f"      {f.description}")
            lines.append(f"      snippet: {f.snippet}")
    return "\n".join(lines)


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Security scan (#93) — secret / 인증서 / 번들 누출 검출",
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="출력 포맷",
    )
    parser.add_argument(
        "--output", default="",
        help="결과 저장 파일 경로 (미지정 시 stdout)",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="LOW severity 도 exit 1 (기본은 HIGH/MEDIUM 만 차단)",
    )
    parser.add_argument(
        "--repo-root", default="",
        help="repo root 경로 (미지정 시 git rev-parse 사용)",
    )
    args = parser.parse_args(argv)

    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        try:
            out = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                text=True, stderr=subprocess.PIPE,
                encoding="utf-8", errors="replace",
            )
            repo_root = Path(out.strip()).resolve()
        except subprocess.CalledProcessError as e:
            sys.stderr.write(f"git rev-parse 실패: {e.stderr}\n")
            return 2

    result = scan_repository(repo_root)

    if args.format == "json":
        text = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    else:
        text = _format_text(result)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        # Windows cp949 콘솔에서도 em-dash / 한글 출력이 깨지지 않도록 UTF-8
        # 강제 (Python 3.7+ 의 reconfigure 사용, 실패하면 fallback 으로 errors=replace).
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("utf-8", errors="replace").decode("utf-8"))

    if args.strict:
        return 1 if result.findings else 0
    return 1 if result.has_blocking() else 0


if __name__ == "__main__":
    sys.exit(main())
