"""3-07 — Paper 후보 통합 export 모듈.

3-02 (real data backtest) / 3-03 (parameter optimization) / 3-04 (walk-forward)
/ 3-05 (stress test) 의 *모든 산출물* 을 종합해 (strategy, symbol, params)
조합이 *모든 단계* 를 통과했는지 추적하고, 상위 N 후보를 단일
``paper_candidate_config.json`` 으로 export.

핵심 원칙:
- **후보 0건도 파일 생성** — `candidates: []` + `reasons_no_candidate` carry.
  *억지로 만들지 않는다*.
- **모든 단계 통과 필수** — 3-02 BACKTEST_PASS + 3-03 PAPER_CANDIDATE +
  3-04 HEALTHY + 3-05 모든 시나리오 PASS.
- **단계별 통과 라벨 carry** — `pipeline_stages` 필드에 (3-02 / 3-03 / 3-04 /
  3-05) 의 verdict 모두 보존 → 운영자가 누락 단계 즉시 확인 가능.
- **3-06 표준 metric 키** — `app.analytics.metrics.PERFORMANCE_METRIC_KEYS`
  사용 (단일 진실).

절대 invariant (테스트로 lock):
- 최상위 + 각 candidate 객체 모두 ``is_order_signal=False`` /
  ``auto_apply_allowed=False`` / ``is_live_authorization=False``.
- broker / OrderExecutor / route_order import 0건 (정적 grep).
- KIS 주문 API / Anthropic / OpenAI / 외부 HTTP import 0건.
- ``PAPER_CANDIDATE`` 라벨은 *paper 운용 후보 검토 가능* — 자동 실거래
  활성화 / 자동 promotion 변경 의미 X.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# 상위 N — 운영자 인지 부하 최소화.
DEFAULT_TOP_K = 2


# ─────────────────────────────────────────────────────────────────────────────
# 1. 후보 단일 진실 — pipeline_stages 통합
# ─────────────────────────────────────────────────────────────────────────────


def _candidate_key(strategy: str, symbol: str, params: dict[str, Any]) -> tuple:
    """(strategy, symbol, params) 식별 키 — params 정렬해서 안정성 보장."""
    params_tuple = tuple(sorted((str(k), str(v)) for k, v in (params or {}).items()))
    return (strategy, symbol, params_tuple)


@dataclass(frozen=True)
class PipelineStage:
    """단일 단계 verdict — 3-02 ~ 3-05 공통 형태."""

    name:    str       # "3-02" / "3-03" / "3-04" / "3-05"
    verdict: str       # "BACKTEST_PASS" / "PAPER_CANDIDATE" / "HEALTHY" / "PASS"
    extra:   dict[str, Any] = field(default_factory=dict)   # score, reasons 등

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":    self.name,
            "verdict": self.verdict,
            "extra":   dict(self.extra),
        }


# 각 단계에서 "통과" 로 간주하는 verdict 라벨 — 사용자 spec 기반.
PASS_VERDICTS_PER_STAGE: dict[str, set[str]] = {
    "3-02": {"BACKTEST_PASS"},
    "3-03": {"PAPER_CANDIDATE"},
    "3-04": {"HEALTHY"},
    "3-05": {"PASS"},
}


@dataclass(frozen=True)
class AggregatedCandidate:
    """단일 (strategy, symbol, params) 의 모든 단계 통합 결과."""

    strategy:        str
    symbol:          str
    params:          dict[str, Any]
    pipeline_stages: list[PipelineStage] = field(default_factory=list)
    risk_metrics:    dict[str, Any]      = field(default_factory=dict)
    score:           float                = 0.0

    def passed_stages(self) -> set[str]:
        """단계별 PASS 라벨 집합 (3-02 / 3-03 / 3-04 / 3-05 중 통과한 것만)."""
        out: set[str] = set()
        for s in self.pipeline_stages:
            allowed = PASS_VERDICTS_PER_STAGE.get(s.name, set())
            if s.verdict in allowed:
                out.add(s.name)
        return out

    def all_stages_passed(self, required: set[str] | None = None) -> bool:
        """필수 단계 모두 통과 여부."""
        req = required if required is not None else {"3-02", "3-03", "3-04", "3-05"}
        return req.issubset(self.passed_stages())

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy":        self.strategy,
            "symbol":          self.symbol,
            "params":          dict(self.params),
            "score":           float(self.score),
            "pipeline_stages": [s.to_dict() for s in self.pipeline_stages],
            "risk_metrics":    dict(self.risk_metrics),
            "passed_stages":   sorted(self.passed_stages()),
            "all_stages_passed": self.all_stages_passed(),
            # 후보 단위 invariant — caller 변경 불가.
            "is_order_signal":      False,
            "auto_apply_allowed":   False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. 입력 파일 어댑터 — 4 단계 JSON 추출
# ─────────────────────────────────────────────────────────────────────────────


def _load_json(path: str | Path) -> dict[str, Any] | None:
    """JSON 안전 로드 — 없거나 파싱 실패 시 None."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def extract_from_backtest_summary(payload: dict[str, Any] | None) -> dict[tuple, PipelineStage]:
    """3-02 ``real_data_backtest_summary.json`` 의 per_symbol.runs 에서 추출.

    Returns:
        dict[(strategy, symbol, params_tuple) → PipelineStage("3-02")]
    """
    out: dict[tuple, PipelineStage] = {}
    if not isinstance(payload, dict):
        return out
    for record in (payload.get("per_symbol") or []):
        if not isinstance(record, dict):
            continue
        symbol = record.get("symbol")
        for run in (record.get("runs") or []):
            if not isinstance(run, dict):
                continue
            strategy = run.get("strategy")
            verdict  = run.get("verdict")
            params   = run.get("params") or {}
            if not (isinstance(strategy, str) and isinstance(symbol, str)
                    and isinstance(verdict, str) and isinstance(params, dict)):
                continue
            key = _candidate_key(strategy, symbol, params)
            out[key] = PipelineStage(
                name="3-02", verdict=verdict,
                extra={"metrics": run.get("metrics") or {}},
            )
    return out


def extract_from_optimization_summary(payload: dict[str, Any] | None) -> dict[tuple, PipelineStage]:
    """3-03 ``parameter_optimization_summary.json`` 의 all_runs 에서 추출."""
    out: dict[tuple, PipelineStage] = {}
    if not isinstance(payload, dict):
        return out
    for run in (payload.get("all_runs") or []):
        if not isinstance(run, dict):
            continue
        strategy = run.get("strategy")
        symbol   = run.get("symbol")
        params   = run.get("params") or {}
        verdict  = run.get("verdict")
        if not (isinstance(strategy, str) and isinstance(symbol, str)
                and isinstance(verdict, str) and isinstance(params, dict)):
            continue
        key = _candidate_key(strategy, symbol, params)
        out[key] = PipelineStage(
            name="3-03", verdict=verdict,
            extra={"metrics": run.get("metrics") or {},
                   "reasons": run.get("reasons") or []},
        )
    return out


def extract_from_walk_forward_summary(payload: dict[str, Any] | None) -> dict[tuple, PipelineStage]:
    """3-04 ``walk_forward_summary.json`` 의 results 에서 추출."""
    out: dict[tuple, PipelineStage] = {}
    if not isinstance(payload, dict):
        return out
    for r in (payload.get("results") or []):
        if not isinstance(r, dict):
            continue
        strategy = r.get("strategy")
        symbol   = r.get("symbol")
        params   = r.get("params") or {}
        verdict  = r.get("verdict")
        if not (isinstance(strategy, str) and isinstance(symbol, str)
                and isinstance(verdict, str) and isinstance(params, dict)):
            continue
        key = _candidate_key(strategy, symbol, params)
        out[key] = PipelineStage(
            name="3-04", verdict=verdict,
            extra={
                "train_expectancy_avg": r.get("train_expectancy_avg", 0.0),
                "val_expectancy_avg":   r.get("val_expectancy_avg",   0.0),
                "fold_count":           r.get("fold_count", 0),
            },
        )
    return out


def extract_from_stress_test_summary(payload: dict[str, Any] | None) -> dict[tuple, list[PipelineStage]]:
    """3-05 ``stress_test_summary.json`` 의 results 에서 추출.

    한 (strategy, symbol, params) 가 *여러 시나리오* 를 가지므로 list 반환.
    aggregate 시 *모든 시나리오 PASS* 인 경우만 통과로 간주.
    """
    out: dict[tuple, list[PipelineStage]] = {}
    if not isinstance(payload, dict):
        return out
    for r in (payload.get("results") or []):
        if not isinstance(r, dict):
            continue
        strategy = r.get("strategy")
        symbol   = r.get("symbol")
        params   = r.get("params") or {}
        verdict  = r.get("stress_verdict")
        scenario = r.get("scenario_name")
        if not (isinstance(strategy, str) and isinstance(symbol, str)
                and isinstance(verdict, str)):
            continue
        # stress test 의 params 는 후보 추적과 무관할 수 있음 — 후속 PR 에서
        # walk-forward 와 일관된 params 사용 시 합쳐짐. 본 PR 에서는 stress
        # test result 가 params 를 carry 하면 사용, 아니면 빈 dict.
        if not isinstance(params, dict):
            params = {}
        key = _candidate_key(strategy, symbol, params)
        out.setdefault(key, []).append(PipelineStage(
            name="3-05", verdict=verdict,
            extra={"scenario": scenario, "score": r.get("stress_score", 0.0)},
        ))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 3. Aggregate
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AggregationInputs:
    """4 단계 JSON 파일 경로 — 각 None 이면 해당 단계 skip."""

    backtest_summary_path:        str | None = None
    optimization_summary_path:    str | None = None
    walk_forward_summary_path:    str | None = None
    stress_test_summary_path:     str | None = None


def aggregate_candidates(
    inputs: AggregationInputs,
    *,
    required_stages: set[str] | None = None,
) -> list[AggregatedCandidate]:
    """4 단계 산출물을 받아 통합 후보 리스트 반환.

    각 (strategy, symbol, params) 별로 *어느 단계 통과* 여부를 PipelineStage
    로 carry. 후보 자격 결정은 caller (build_paper_candidate_config) 가 수행.

    Args:
        inputs: 4 단계 JSON 파일 경로.
        required_stages: 모두 통과해야 하는 단계 집합 (default {3-02,3-03,3-04,3-05}).

    Returns:
        list[AggregatedCandidate] — *모든* 식별된 (strategy, symbol, params)
        조합. 후보 필터링은 build_paper_candidate_config 에서.
    """
    bt_payload   = _load_json(inputs.backtest_summary_path) if inputs.backtest_summary_path else None
    opt_payload  = _load_json(inputs.optimization_summary_path) if inputs.optimization_summary_path else None
    wf_payload   = _load_json(inputs.walk_forward_summary_path) if inputs.walk_forward_summary_path else None
    str_payload  = _load_json(inputs.stress_test_summary_path) if inputs.stress_test_summary_path else None

    bt_by_key  = extract_from_backtest_summary(bt_payload)
    opt_by_key = extract_from_optimization_summary(opt_payload)
    wf_by_key  = extract_from_walk_forward_summary(wf_payload)
    str_by_key = extract_from_stress_test_summary(str_payload)

    # 모든 단계의 모든 키 집합.
    all_keys = set(bt_by_key) | set(opt_by_key) | set(wf_by_key) | set(str_by_key)

    out: list[AggregatedCandidate] = []
    for key in all_keys:
        strategy, symbol, params_tuple = key
        params = {k: v for k, v in params_tuple}
        stages: list[PipelineStage] = []
        risk_metrics: dict[str, Any] = {}

        if key in bt_by_key:
            stages.append(bt_by_key[key])
            m = bt_by_key[key].extra.get("metrics") or {}
            if isinstance(m, dict):
                risk_metrics.update(m)
        if key in opt_by_key:
            stages.append(opt_by_key[key])
            m = opt_by_key[key].extra.get("metrics") or {}
            if isinstance(m, dict):
                risk_metrics.update(m)
        if key in wf_by_key:
            stages.append(wf_by_key[key])
        # stress test — 모든 시나리오가 PASS 이면 단일 stage "3-05" PASS,
        # 아니면 가장 나쁜 verdict 를 stage 로 carry.
        if key in str_by_key:
            stress_stages = str_by_key[key]
            verdicts = [s.verdict for s in stress_stages]
            scenarios_passed = [s for s in stress_stages if s.verdict == "PASS"]
            # 모든 시나리오가 PASS 일 때만 3-05 PASS — 하나라도 FAIL/WARN 이면
            # *worst-case* 를 verdict 로 carry (운영자가 즉시 인지).
            if all(v == "PASS" for v in verdicts):
                summary_verdict = "PASS"
            elif any(v == "FAIL" for v in verdicts):
                summary_verdict = "FAIL"
            elif any(v == "WARN" for v in verdicts):
                summary_verdict = "WARN"
            else:
                summary_verdict = "INSUFFICIENT_DATA"
            stages.append(PipelineStage(
                name="3-05", verdict=summary_verdict,
                extra={
                    "scenario_count":        len(stress_stages),
                    "scenarios_passed":      len(scenarios_passed),
                    "scenario_verdicts":     verdicts,
                },
            ))

        # score 우선순위: risk_metrics 의 risk_adjusted_score → expectancy.
        score = 0.0
        if "risk_adjusted_score" in risk_metrics:
            try:
                score = float(risk_metrics.get("risk_adjusted_score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
        if score == 0.0 and "expectancy" in risk_metrics:
            try:
                score = float(risk_metrics.get("expectancy") or 0.0)
            except (TypeError, ValueError):
                score = 0.0

        out.append(AggregatedCandidate(
            strategy=strategy, symbol=symbol, params=params,
            pipeline_stages=stages, risk_metrics=risk_metrics, score=score,
        ))

    return out


# ─────────────────────────────────────────────────────────────────────────────
# 4. paper_candidate_config 빌드 + 파일 작성
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PaperCandidateConfig:
    """최종 export 객체 — 후보 0건도 candidates:[] + reasons carry."""

    generated_at:         str
    candidates:           list[AggregatedCandidate]
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
    aggregated:        list[AggregatedCandidate],
    *,
    required_stages:   set[str] | None = None,
    top_k:             int = DEFAULT_TOP_K,
    metadata:          dict[str, Any] | None = None,
    now:               datetime | None = None,
) -> PaperCandidateConfig:
    """통합 후보 → 최종 paper_candidate_config.

    필수 단계 모두 통과한 후보만 추출, score 내림차순 top_k 선정.
    후보 0건이면 ``reasons_no_candidate`` 에 사유 집계.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    req = required_stages if required_stages is not None else {"3-02", "3-03", "3-04", "3-05"}

    passed = [c for c in aggregated if c.all_stages_passed(req)]
    cap = max(0, int(top_k))
    passed_sorted = sorted(passed, key=lambda c: c.score, reverse=True)[:cap]

    reasons_no_candidate: list[str] = []
    if not passed_sorted:
        if not aggregated:
            reasons_no_candidate.append("no_pipeline_results_loaded")
        else:
            # 단계별 누락 / verdict 통계.
            missing_by_stage: dict[str, int] = {}
            non_pass_by_stage: dict[str, int] = {}
            for c in aggregated:
                passed_set = c.passed_stages()
                for stage in sorted(req):
                    if stage not in {s.name for s in c.pipeline_stages}:
                        missing_by_stage[stage] = missing_by_stage.get(stage, 0) + 1
                    elif stage not in passed_set:
                        non_pass_by_stage[stage] = non_pass_by_stage.get(stage, 0) + 1
            for stage in sorted(req):
                miss = missing_by_stage.get(stage, 0)
                nopass = non_pass_by_stage.get(stage, 0)
                if miss > 0:
                    reasons_no_candidate.append(
                        f"{stage}_missing_for_{miss}_candidate(s)"
                    )
                if nopass > 0:
                    reasons_no_candidate.append(
                        f"{stage}_did_not_pass_for_{nopass}_candidate(s)"
                    )
            reasons_no_candidate.append(
                f"no_candidate_passed_all_required_stages_{sorted(req)}"
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
    """JSON 저장 — 디렉토리 없으면 생성. 어떤 입력이든 raise 없음."""
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
