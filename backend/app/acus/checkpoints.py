"""ACUS 체크포인트 + 진행상황 + 시작/완료 타임스탬프 (read-write to reports/acus/).

각 stage 가 끝나면 ``reports/acus/checkpoints/{stage}.json`` 에 저장.
다음 실행은 마지막 checkpoint 부터 재개(중간 오류 → resume).
``reports/acus/progress.md`` 는 운영자(하루) 가 GitHub/핸드폰에서 볼 수 있게 자동 갱신.
broker / 외부 HTTP 호출 0건. 파일 시스템 read/write 만.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path("reports/acus")
CHECKPOINTS_DIRNAME = "checkpoints"

STAGE_NAMES: "tuple[str, ...]" = (
    "stage_01_collection_ready",
    "stage_02_backtest_agent",
    "stage_03_regime_agent",
    "stage_04_liquidity_agent",
    "stage_05_news_agent",
    "stage_06_risk_agent",
    "stage_07_integration",
    "stage_08_final_report",
)


@dataclass(frozen=True)
class CheckpointStore:
    root: Path

    @property
    def ckpt_dir(self) -> Path:
        return self.root / CHECKPOINTS_DIRNAME

    def ensure(self) -> None:
        self.ckpt_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, stage: str) -> Path:
        return self.ckpt_dir / f"{stage}.json"

    def exists(self, stage: str) -> bool:
        return self.path_for(stage).is_file()

    def load(self, stage: str) -> "dict[str, Any] | None":
        p = self.path_for(stage)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def save(self, stage: str, payload: "dict[str, Any]") -> Path:
        self.ensure()
        p = self.path_for(stage)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
        tmp.replace(p)  # atomic on same FS
        return p

    def write_started_at(self) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        (self.root / "started_at.txt").write_text(ts, encoding="utf-8")
        return ts

    def read_started_at(self) -> "str | None":
        p = self.root / "started_at.txt"
        return p.read_text(encoding="utf-8").strip() if p.is_file() else None

    def write_completed_at(self) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        (self.root / "completed_at.txt").write_text(ts, encoding="utf-8")
        return ts


def render_progress_markdown(*, current_stage: str, completed_stages: "list[str]",
                             total_stages: int, error: "str | None",
                             started_at: "str | None", note: str = "") -> str:
    pct = round(len(completed_stages) / max(1, total_stages) * 100, 1)
    lines: list[str] = []
    lines.append("# ACUS 진행 상황")
    lines.append("")
    lines.append("> 본 파일은 무인 파이프라인이 자동 갱신합니다. 운영자(하루) 가 핸드폰/GitHub")
    lines.append("> 에서 진행 상황을 확인하는 용도이며, 안 보아도 자동으로 끝까지 진행됩니다.")
    lines.append("> ⚠ 실거래 승인 아님 · 주문 0건 · 수익 보장 아님.")
    lines.append("")
    lines.append(f"- started_at: `{started_at or '(아직 시작 안 함)'}`")
    lines.append(f"- 현재 stage: **{current_stage}**")
    lines.append(f"- 진행률: **{pct}%** ({len(completed_stages)}/{total_stages})")
    lines.append("")
    lines.append("## 완료된 stage")
    for s in completed_stages:
        lines.append(f"- ✅ `{s}`")
    if not completed_stages:
        lines.append("- (아직 없음)")
    lines.append("")
    if error:
        lines.append("## 발생한 오류")
        lines.append(f"```\n{error}\n```")
        lines.append("")
    if note:
        lines.append("## 노트")
        lines.append(note)
        lines.append("")
    return "\n".join(lines)


def write_progress(root: Path, *, current_stage: str, completed_stages: "list[str]",
                   total_stages: int, error: "str | None" = None,
                   started_at: "str | None" = None, note: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    md = render_progress_markdown(
        current_stage=current_stage, completed_stages=completed_stages,
        total_stages=total_stages, error=error, started_at=started_at, note=note,
    )
    p = root / "progress.md"
    p.write_text(md, encoding="utf-8")
    return p
