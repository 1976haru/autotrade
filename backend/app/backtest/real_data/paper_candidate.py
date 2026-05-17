"""paper_candidate_config.json 생성기.

검증 통과 (verdict=PAPER_CANDIDATE) 한 (strategy, symbol) 조합 중 상위 1~2 를
선정해 ``reports/paper_candidate_config.json`` 으로 export.

후보 0건이어도 *반드시* 파일은 생성한다 — 후보 0개 + 사유를 명시한 JSON.
이로써 caller / 운영자가 "후보가 있는데 빠뜨렸는지" vs "후보가 없어서 빈
파일인지" 를 즉시 구분 가능.

paper_candidate JSON 스키마:
```
{
  "generated_at": "2026-05-17T05:00:00+00:00",
  "is_order_signal": false,
  "auto_apply_allowed": false,
  "is_live_authorization": false,
  "candidate_count": 0|1|2,
  "candidates": [
    {
      "strategy": "sma_crossover",
      "symbol":   "005930",
      "params":   { ... },
      "score":    0.0123,
      "risk_metrics": { ... 13 metric keys ... },
      "validation_status": "PAPER_CANDIDATE",
      "reasons":  ["all_filters_passed"],
      ...
    }
  ],
  "reasons_no_candidate": [...]  // candidate_count=0 일 때만
}
```

절대 invariant:
- `is_order_signal = false` / `auto_apply_allowed = false` /
  `is_live_authorization = false` — 항상 false. caller 변경 불가.
- 본 export 는 운영자 *검토 자료* 일 뿐. 자동 paper trader 시작 / 자동 실거래
  허가 / mode 변경 0건.
- broker / OrderExecutor / route_order import 0건.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.backtest.real_data.filters import BacktestVerdict


# 상위 N 후보만 export — 운영자 인지 부하 최소화.
DEFAULT_TOP_K = 2


@dataclass(frozen=True)
class CandidateInput:
    """단일 (strategy, symbol) 백테스트 결과 — paper_candidate 후보 input."""

    strategy:         str
    symbol:           str
    params:           dict[str, Any]
    risk_metrics:     dict[str, Any]
    validation_status: BacktestVerdict
    reasons:          list[str]
    score:            float                    # 일반적으로 risk_adjusted_score.
    extra:            dict[str, Any] = field(default_factory=dict)

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
            # 후보 객체 단위 invariant — caller 변경 불가.
            "is_order_signal":      False,
            "auto_apply_allowed":   False,
        }


@dataclass(frozen=True)
class PaperCandidateConfig:
    """최종 export 객체."""

    generated_at:        str                       # ISO 8601 UTC
    candidates:          list[CandidateInput]
    reasons_no_candidate: list[str]                 # candidates 비어 있을 때 사유
    metadata:            dict[str, Any] = field(default_factory=dict)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":           self.generated_at,
            # 최상위 invariant — JSON 소비 측이 잘못 해석해도 lock.
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
    """검증된 (strategy, symbol) 후보 중 PAPER_CANDIDATE verdict 만 추출.

    Args:
        classified: 모든 (strategy, symbol) 백테스트 결과 (verdict 분류된 상태).
        top_k: 후보 개수 상한 (default 2).
        metadata: 운영자가 추가하고 싶은 컨텍스트 — secret 금지.
        now: 시점 테스트 주입.

    Returns:
        PaperCandidateConfig — 후보 0건이면 ``reasons_no_candidate`` 가 채워짐.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    passed = [c for c in classified if c.validation_status == BacktestVerdict.PAPER_CANDIDATE]
    # score 내림차순 — 운영자가 상위 후보부터 검토.
    passed_sorted = sorted(passed, key=lambda c: c.score, reverse=True)[:max(0, int(top_k))]

    reasons_no_candidate: list[str] = []
    if not passed_sorted:
        # 사유 집계 — 가장 흔한 거부 사유 상위 5종.
        verdict_counts: dict[str, int] = {}
        for c in classified:
            v = c.validation_status.value
            verdict_counts[v] = verdict_counts.get(v, 0) + 1
        if not classified:
            reasons_no_candidate.append("no_backtest_runs_evaluated")
        else:
            for v, cnt in sorted(verdict_counts.items(), key=lambda x: -x[1]):
                reasons_no_candidate.append(f"{v}: {cnt} run(s)")
            reasons_no_candidate.append(
                "no_strategy_symbol_passed_all_filters"
            )

    return PaperCandidateConfig(
        generated_at=now.isoformat(),
        candidates=passed_sorted,
        reasons_no_candidate=reasons_no_candidate,
        metadata=metadata or {},
    )


def write_paper_candidate_config(
    config: PaperCandidateConfig,
    out_path: str | Path,
) -> Path:
    """JSON 으로 저장. 디렉토리 없으면 생성. 절대 raise 하지 않는다."""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = config.to_dict()
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)
    p.write_text(text, encoding="utf-8")
    return p


def read_paper_candidate_config(path: str | Path) -> dict[str, Any]:
    """저장된 JSON 을 dict 로 로드 — 운영자 / 다음 단계 도구 입력용."""
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))
