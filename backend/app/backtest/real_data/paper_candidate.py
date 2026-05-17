"""3-03 grid search 결과 → ``paper_candidate_config.json`` 생성기.

PAPER_CANDIDATE verdict 받은 (strategy, symbol, params) 조합 중 상위 N 개를
선정해 운영자 검토 자료로 export. 후보 0건이어도 *반드시* 파일은 생성한다 —
``reasons_no_candidate`` 에 사유 명시.

paper_candidate JSON 스키마:
```jsonc
{
  "generated_at":           "...",
  "is_order_signal":        false,
  "auto_apply_allowed":     false,
  "is_live_authorization":  false,
  "candidate_count":        0|1|2|...,
  "candidates": [
    {
      "strategy":          "sma_crossover",
      "symbol":            "005930",
      "params":            { "short": 5, "long": 20 },
      "score":             0.0123,
      "risk_metrics":      { ... },
      "validation_status": "PAPER_CANDIDATE",
      "reasons":           ["all_filters_passed"],
      "is_order_signal":      false,
      "auto_apply_allowed":   false
    }
  ],
  "reasons_no_candidate":  [...]   // candidate_count=0 일 때만 채워짐
}
```

절대 invariant:
- 최상위 + 각 candidate 모두 `is_order_signal=false` / `auto_apply_allowed=false` /
  `is_live_authorization=false` (caller 변경 불가).
- 후보 0건도 파일 생성. 사유 없이 빈 후보 강제 생성 0건.
- broker / OrderExecutor / route_order import 0건.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.backtest.real_data.optimization_verdicts import OptimizationVerdict


# 상위 N 후보만 export — 운영자 인지 부하 최소화.
DEFAULT_TOP_K = 2


@dataclass(frozen=True)
class CandidateInput:
    """grid search 결과 entry — paper_candidate 후보 input."""

    strategy:          str
    symbol:            str
    params:            dict[str, Any]
    risk_metrics:      dict[str, Any]
    validation_status: OptimizationVerdict
    reasons:           list[str]
    score:             float                  # 정렬 키 (보통 expectancy / risk_adjusted_score).
    extra:             dict[str, Any]         = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy":          self.strategy,
            "symbol":            self.symbol,
            "params":            dict(self.params),
            "score":             float(self.score),
            "risk_metrics":      dict(self.risk_metrics),
            "validation_status": self.validation_status.value,
            "reasons":           list(self.reasons),
            "extra":             dict(self.extra),
            # candidate 단위 invariant — caller 변경 불가.
            "is_order_signal":      False,
            "auto_apply_allowed":   False,
        }


@dataclass(frozen=True)
class PaperCandidateConfig:
    """최종 export 객체."""

    generated_at:         str
    candidates:           list[CandidateInput]
    reasons_no_candidate: list[str]
    metadata:             dict[str, Any] = field(default_factory=dict)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":           self.generated_at,
            # 최상위 invariant — JSON consumer 측에서도 안전.
            "is_order_signal":        False,
            "auto_apply_allowed":     False,
            "is_live_authorization":  False,
            "candidate_count":        self.candidate_count,
            "candidates":             [c.to_dict() for c in self.candidates],
            "reasons_no_candidate":   list(self.reasons_no_candidate),
            "metadata":               dict(self.metadata),
        }


def build_paper_candidate_config(
    classified: list[CandidateInput],
    *,
    top_k: int = DEFAULT_TOP_K,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> PaperCandidateConfig:
    """검증된 grid run 중 PAPER_CANDIDATE verdict 만 추출 → 상위 N 정렬.

    Args:
        classified: 모든 grid run 결과 (verdict 분류 완료 상태).
        top_k: 후보 개수 상한 (default 2). 0 또는 음수는 0 으로 clamp.
        metadata: 운영자 검토용 컨텍스트 — secret 금지.
        now: 시점 테스트 주입.

    Returns:
        PaperCandidateConfig — 후보 0건이면 ``reasons_no_candidate`` 채움.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    passed = [c for c in classified if c.validation_status == OptimizationVerdict.PAPER_CANDIDATE]
    # score 내림차순.
    cap = max(0, int(top_k))
    passed_sorted = sorted(passed, key=lambda c: c.score, reverse=True)[:cap]

    reasons_no_candidate: list[str] = []
    if not passed_sorted:
        # 사유 — 가장 흔한 verdict 분포 + 안내 문구.
        verdict_counts: dict[str, int] = {}
        for c in classified:
            v = c.validation_status.value
            verdict_counts[v] = verdict_counts.get(v, 0) + 1
        if not classified:
            reasons_no_candidate.append("no_grid_runs_evaluated")
        else:
            for v, cnt in sorted(verdict_counts.items(), key=lambda x: -x[1]):
                reasons_no_candidate.append(f"{v}: {cnt} run(s)")
            reasons_no_candidate.append(
                "no_strategy_symbol_params_passed_all_filters"
            )

    return PaperCandidateConfig(
        generated_at=now.isoformat(),
        candidates=passed_sorted,
        reasons_no_candidate=reasons_no_candidate,
        metadata=metadata or {},
    )


def write_paper_candidate_config(
    config: PaperCandidateConfig, out_path: str | Path,
) -> Path:
    """JSON 저장 — 디렉토리 없으면 생성."""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        config.to_dict(), indent=2, ensure_ascii=False, sort_keys=False,
    )
    p.write_text(text, encoding="utf-8")
    return p


def read_paper_candidate_config(path: str | Path) -> dict[str, Any]:
    """저장된 JSON 로드 — 운영자 / 후속 도구 입력용."""
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))
