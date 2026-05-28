"""ACUS 무인(unattended) 파이프라인 오케스트레이터 — 8 stage + 체크포인트 재개.

사람 입력 요구 0건. 중간 오류는 progress.md 에 기록 후 후속 stage 가 가능한 부분만
계속 진행. 시작 시 ``started_at.txt`` 생성, 완료 시 ``completed_at.txt`` 생성.
broker / OrderExecutor / route_order 호출 0건. 안전 flag 변경 0건.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.acus import (
    backtest_agent,
    data_collection,
    final_report,
    integration as integ,
    liquidity_agent,
    news_agent,
    regime_agent,
    risk_agent,
)
from app.acus.checkpoints import (
    DEFAULT_ROOT,
    STAGE_NAMES,
    CheckpointStore,
    write_progress,
)
from app.acus.deps import PipelineDeps, default_deps


def _candidate_meta_from(stage_01: dict[str, Any], symbols: list[str]) -> list[dict[str, Any]]:
    keep = {s for s in symbols}
    return [p for p in (stage_01.get("per_symbol") or []) if p.get("symbol") in keep]


def _run_stage(store: CheckpointStore, stage: str, fn: Callable[[], dict[str, Any]],
               *, completed: list[str], total: int, started_at: str,
               resume: bool) -> "tuple[dict[str, Any], str | None]":
    """단일 stage 실행 — 체크포인트 있으면 재사용, 없으면 실행 후 저장. 오류 → (None, err_str)."""
    if resume and store.exists(stage):
        loaded = store.load(stage)
        if loaded is not None:
            completed.append(stage)
            write_progress(store.root, current_stage=stage, completed_stages=completed,
                           total_stages=total, started_at=started_at,
                           note=f"`{stage}` 는 기존 checkpoint 재사용(resume).")
            return loaded, None

    write_progress(store.root, current_stage=stage, completed_stages=completed,
                   total_stages=total, started_at=started_at,
                   note=f"`{stage}` 실행 중…")
    try:
        payload = fn()
    except Exception as exc:  # noqa: BLE001 — stage 격리
        err = f"{type(exc).__name__}: {exc}"
        write_progress(store.root, current_stage=stage, completed_stages=completed,
                       total_stages=total, error=err, started_at=started_at,
                       note=f"`{stage}` 실행 중 오류 — 후속 stage 일부 skip 될 수 있음.")
        return {"stage": stage, "error": err, "skipped": True}, err

    store.save(stage, payload)
    completed.append(stage)
    write_progress(store.root, current_stage=stage, completed_stages=completed,
                   total_stages=total, started_at=started_at,
                   note=f"`{stage}` 완료 → checkpoint 저장.")
    return payload, None


def run_pipeline(
    *,
    input_dir: str | Path,
    symbols: list[str] | None = None,
    deps: PipelineDeps | None = None,
    root: str | Path | None = None,
    resume: bool = True,
    news_cost_cap_usd: float = news_agent.DEFAULT_COST_CAP_USD,
    min_bars: int = 100,
    min_days: int = 5,
    liquidity_max_spread: float = liquidity_agent.MAX_SPREAD,
    liquidity_min_avg_turnover_krw: float = liquidity_agent.MIN_AVG_TURNOVER_KRW,
) -> dict[str, Any]:
    """전체 파이프라인 실행. 결과 dict 반환(최종 리포트 포함). 어떤 단계에서도 사람 입력 요구 X."""
    deps = deps or default_deps()
    store = CheckpointStore(Path(root) if root else DEFAULT_ROOT)
    store.ensure()
    started_at = store.read_started_at() if resume else None
    if not started_at:
        started_at = store.write_started_at()

    total = len(STAGE_NAMES)
    completed: list[str] = []
    notes: list[str] = []
    errors: list[str] = []

    # ── Stage 01 ──────────────────────────────────────────────────────────
    s01, e = _run_stage(
        store, "stage_01_collection_ready",
        lambda: data_collection.collect_universe(
            input_dir, symbols=symbols, min_bars=min_bars, min_days=min_days, deps=deps),
        completed=completed, total=total, started_at=started_at, resume=resume)
    if e:
        errors.append(e)

    ready_meta = [p for p in (s01.get("per_symbol") or []) if p.get("ready_for_backtest")]

    # ── Stage 02 ──────────────────────────────────────────────────────────
    s02, e = _run_stage(
        store, "stage_02_backtest_agent",
        lambda: backtest_agent.run_backtest_agent(ready_meta, deps=deps),
        completed=completed, total=total, started_at=started_at, resume=resume)
    if e:
        errors.append(e)

    candidate_symbols = s02.get("candidates") or []
    cand_meta = _candidate_meta_from(s01, candidate_symbols)

    # ── Stage 03 ──────────────────────────────────────────────────────────
    s03, e = _run_stage(
        store, "stage_03_regime_agent",
        lambda: regime_agent.run_regime_agent(cand_meta, deps=deps),
        completed=completed, total=total, started_at=started_at, resume=resume)
    if e:
        errors.append(e)

    # ── Stage 04 ──────────────────────────────────────────────────────────
    s04, e = _run_stage(
        store, "stage_04_liquidity_agent",
        lambda: liquidity_agent.run_liquidity_agent(
            cand_meta, deps=deps,
            max_spread=liquidity_max_spread,
            min_avg_turnover_krw=liquidity_min_avg_turnover_krw),
        completed=completed, total=total, started_at=started_at, resume=resume)
    if e:
        errors.append(e)

    # ── Stage 05 ── (Claude — 실패해도 NEWS_UNKNOWN, 파이프라인 계속) ─────
    s05, e = _run_stage(
        store, "stage_05_news_agent",
        lambda: news_agent.run_news_agent(
            cand_meta, analyze_news=deps.analyze_news, cost_cap_usd=news_cost_cap_usd),
        completed=completed, total=total, started_at=started_at, resume=resume)
    if e:
        errors.append(e)

    # ── Stage 06 ──────────────────────────────────────────────────────────
    s06, e = _run_stage(
        store, "stage_06_risk_agent",
        lambda: risk_agent.run_risk_agent(cand_meta, deps=deps),
        completed=completed, total=total, started_at=started_at, resume=resume)
    if e:
        errors.append(e)

    # ── Stage 07 ──────────────────────────────────────────────────────────
    s07, e = _run_stage(
        store, "stage_07_integration",
        lambda: integ.run_integration(s01, s02, s03, s04, s05, s06),
        completed=completed, total=total, started_at=started_at, resume=resume)
    if e:
        errors.append(e)

    # ── Stage 08 ──────────────────────────────────────────────────────────
    completed_at_ts = datetime.now(timezone.utc).isoformat()
    if errors:
        notes.append(f"오류 {len(errors)}건 발생 — 가능한 부분만 진행: {'; '.join(errors[:3])}")
    if s05.get("cost_capped"):
        notes.append(f"NewsAgent 비용 한도 ${news_cost_cap_usd} 도달 — 일부 종목 NEWS_UNKNOWN.")
    if not s05.get("api_configured"):
        notes.append("ANTHROPIC_API_KEY 미설정 — 모든 종목 NEWS_UNKNOWN (제외 아님).")

    s08, e = _run_stage(
        store, "stage_08_final_report",
        lambda: final_report.build_final_report(
            s07, started_at=started_at, completed_at=completed_at_ts, notes=notes,
        ).to_dict(),
        completed=completed, total=total, started_at=started_at, resume=resume)
    if e:
        errors.append(e)

    # 최종 markdown 도 생성(검색 편의)
    try:
        report_obj = final_report.build_final_report(
            s07, started_at=started_at, completed_at=completed_at_ts, notes=notes)
        (store.root / "acus_final_report.md").write_text(
            final_report.render_markdown(report_obj), encoding="utf-8")
        (store.root / "acus_final_report.json").write_text(
            json.dumps(report_obj.to_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"final_report_render: {type(exc).__name__}: {exc}")

    completed_at = store.write_completed_at()
    write_progress(store.root, current_stage="DONE", completed_stages=completed,
                   total_stages=total, started_at=started_at,
                   note=f"완료. completed_at={completed_at}. 최종 리포트: acus_final_report.md")
    return {
        "started_at": started_at,
        "completed_at": completed_at,
        "errors": errors,
        "final_report": s08,
        "integration": s07,
        "stages": {
            "stage_01": s01, "stage_02": s02, "stage_03": s03, "stage_04": s04,
            "stage_05": s05, "stage_06": s06, "stage_07": s07, "stage_08": s08,
        },
    }
