"""P-31: decision_episode 학습/분석 데이터 export (CSV / JSONL).

P-21 이후 저장된 decision episode 를 엑셀(CSV) / 파이썬·학습(JSONL) 분석용 파일로
내보낸다. **민감정보(secret / account / API key / token)는 절대 export 하지
않는다** — 키 단위(forbidden key) + 값 단위(sk-/ghp_/Bearer/JWT/계좌번호 패턴)
이중 가드(fail-closed).

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *직렬화/파일 작성 전용* — broker / OrderExecutor / route_order /
  외부 HTTP / 실 계좌 잔고 조회 import 0건.
- export 결과는 *분석용이며 주문 신호가 아니다* — 결과 dict 의
  `is_order_signal=False` / `is_live_authorization=False` / `contains_secret=False`.
- 생성 파일은 `exports/decision_episodes/` 에 저장하며 git 미커밋(.gitignore).
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agents.agent_memory import SecretLeakError, sanitize_dict

# export 에서 절대 허용되지 않는 키 (대소문자 무시, 정규화 비교).
FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "kis_app_key", "kis_app_secret", "app_key", "app_secret", "api_key",
    "apikey", "secret", "account_no", "accountno", "account_number",
    "access_token", "refresh_token", "password", "passwd", "authorization",
    "bearer", "token", "private_key", "privatekey", "client_secret",
})


class ExportSafetyError(ValueError):
    """export payload 에 금지 키/민감값이 포함된 경우 (fail-closed)."""


def _norm_key(k: Any) -> str:
    return str(k).strip().lower().replace("-", "_").replace(" ", "_")


def reject_forbidden_keys(record: Any, *, path: str = "") -> None:
    """record(dict/list) 를 재귀 순회하며 금지 키가 있으면 ExportSafetyError."""
    if isinstance(record, dict):
        for k, v in record.items():
            if _norm_key(k) in FORBIDDEN_KEYS:
                raise ExportSafetyError(f"forbidden key in export payload: {path}{k}")
            reject_forbidden_keys(v, path=f"{path}{k}.")
    elif isinstance(record, (list, tuple)):
        for i, v in enumerate(record):
            reject_forbidden_keys(v, path=f"{path}[{i}].")


def assert_export_safe(record: dict[str, Any]) -> dict[str, Any]:
    """export 전 최종 가드 — 금지 키 거부 + 값 단위 secret sanitize(fail-closed).

    값에 sk-/ghp_/Bearer/JWT/계좌번호 등 패턴이 있으면 SecretLeakError.
    통과 시 sanitize 적용된 안전 dict 반환.
    """
    reject_forbidden_keys(record)
    return sanitize_dict(record, field_name="export")   # secret 값 적중 시 raise


def sanitize_export_record(record: dict[str, Any]) -> dict[str, Any]:
    """단일 export 레코드를 안전화 (금지 키 거부 + 값 sanitize)."""
    return assert_export_safe(record)


# ─────────────────────────────────────────────────────────────────────────────
# 필터 옵션
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionEpisodeExportOptions:
    """export 필터 — 모두 optional. 미지정 시 전체."""

    start_date:     str | None = None        # YYYY-MM-DD (created_at 기준, 포함)
    end_date:       str | None = None        # YYYY-MM-DD (포함)
    symbols:        tuple[str, ...] = ()
    strategies:     tuple[str, ...] = ()
    final_actions:  tuple[str, ...] = ()
    risk_profiles:  tuple[str, ...] = ()
    outcome_labels: tuple[str, ...] = ()
    review_tags:    tuple[str, ...] = ()
    max_rows:       int = 10_000


def _ep_date(ep: dict[str, Any]) -> str | None:
    ts = ep.get("created_at")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date().isoformat()
    except (ValueError, TypeError):
        return None


def _ep_strategies(ep: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    sel = ep.get("selected_strategies")
    if isinstance(sel, (list, tuple)):
        out |= {str(s).upper() for s in sel}
    votes = ep.get("votes")
    if isinstance(votes, list):
        for v in votes:
            if isinstance(v, dict) and v.get("strategy"):
                out.add(str(v["strategy"]).upper())
    return out


def filter_episodes(
    episodes: list[dict[str, Any]], options: DecisionEpisodeExportOptions,
) -> list[dict[str, Any]]:
    """옵션 기준 episode AND 필터 (deterministic)."""
    out: list[dict[str, Any]] = []
    syms = {s.upper() for s in options.symbols}
    strats = {s.upper() for s in options.strategies}
    actions = {s.upper() for s in options.final_actions}
    profiles = {s.upper() for s in options.risk_profiles}
    labels = {s.upper() for s in options.outcome_labels}
    tags = {s.upper() for s in options.review_tags}
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        d = _ep_date(ep)
        if options.start_date and (d is None or d < options.start_date):
            continue
        if options.end_date and (d is None or d > options.end_date):
            continue
        if syms and str(ep.get("symbol", "")).upper() not in syms:
            continue
        if actions and str(ep.get("final_action", "")).upper() not in actions:
            continue
        if strats and not (_ep_strategies(ep) & strats):
            continue
        if profiles:
            rp = str((ep.get("council") or {}).get("risk_profile", "")).upper()
            if rp not in profiles:
                continue
        if labels:
            ol = str((ep.get("outcome") or {}).get("label")
                     or (ep.get("outcome_summary") or {}).get("label", "")).upper()
            if ol not in labels:
                continue
        if tags:
            ep_tags = {str(t).upper() for t in ((ep.get("review") or {}).get("tags") or [])}
            if not (ep_tags & tags):
                continue
        out.append(ep)
        if len(out) >= max(1, int(options.max_rows)):
            break
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 레코드 매핑 (allowlist — 금지 키는 애초에 포함하지 않음)
# ─────────────────────────────────────────────────────────────────────────────

CSV_COLUMNS: tuple[str, ...] = (
    "episode_id", "created_at", "symbol", "mode", "final_action",
    "confidence", "quality_score", "reason_code",
    "market_price", "market_regime", "data_status",
    "top_strategy", "buy_vote_count", "sell_vote_count", "hold_vote_count",
    "selected_strategies", "risk_profile",
    "order_status", "fill_status", "latency_ms", "slippage_bps", "broker_order_no",
    "outcome_status", "outcome_label", "return_5m", "return_30m", "return_close",
    "mfe", "mae",
    "sell_reason_code", "sell_reason_category",
    "review_grade", "review_primary_tag", "review_tags",
)


def episode_to_csv_row(ep: dict[str, Any]) -> dict[str, Any]:
    """episode dict → flat CSV row (allowlist 필드만)."""
    ms = ep.get("market_summary") or {}
    vs = ep.get("vote_summary") or {}
    council = ep.get("council") or {}
    oq = ep.get("order_quality_summary") or {}
    os_ = ep.get("outcome_summary") or {}
    sr = ep.get("sell_reason_summary") or {}
    rv = ep.get("review_summary") or {}
    review_tags = (ep.get("review") or {}).get("tags") or []
    return {
        "episode_id":      ep.get("episode_id"),
        "created_at":      ep.get("created_at"),
        "symbol":          ep.get("symbol"),
        "mode":            ep.get("mode"),
        "final_action":    ep.get("final_action"),
        "confidence":      ep.get("confidence"),
        "quality_score":   ep.get("quality_score"),
        "reason_code":     ep.get("reason_code"),
        "market_price":    ms.get("price"),
        "market_regime":   ms.get("market_regime") or council.get("market_regime"),
        "data_status":     ms.get("data_status"),
        "top_strategy":    vs.get("top_strategy"),
        "buy_vote_count":  vs.get("buy_vote_count"),
        "sell_vote_count": vs.get("sell_vote_count"),
        "hold_vote_count": vs.get("hold_vote_count"),
        "selected_strategies": "|".join(ep.get("selected_strategies") or []),
        "risk_profile":    council.get("risk_profile"),
        "order_status":    oq.get("order_status"),
        "fill_status":     oq.get("fill_status"),
        "latency_ms":      oq.get("latency_ms"),
        "slippage_bps":    oq.get("slippage_bps"),
        "broker_order_no": ep.get("broker_order_no"),   # 모의 주문 추적용 (secret 아님)
        "outcome_status":  os_.get("status"),
        "outcome_label":   os_.get("label"),
        "return_5m":       os_.get("return_5m"),
        "return_30m":      os_.get("return_30m"),
        "return_close":    os_.get("return_close"),
        "mfe":             os_.get("max_favorable_excursion"),
        "mae":             os_.get("max_adverse_excursion"),
        "sell_reason_code":     sr.get("reason_code"),
        "sell_reason_category": sr.get("category"),
        "review_grade":         rv.get("grade"),
        "review_primary_tag":   rv.get("primary_tag"),
        "review_tags":          "|".join(str(t) for t in review_tags),
    }


def episode_to_jsonl_record(ep: dict[str, Any]) -> dict[str, Any]:
    """episode dict → JSONL 안전 레코드 (구조 유지, 금지 키/secret 차단)."""
    record = {
        "episode_id":      ep.get("episode_id"),
        "created_at":      ep.get("created_at"),
        "symbol":          ep.get("symbol"),
        "mode":            ep.get("mode"),
        "final_action":    ep.get("final_action"),
        "confidence":      ep.get("confidence"),
        "quality_score":   ep.get("quality_score"),
        "reason_code":     ep.get("reason_code"),
        "selected_strategies": ep.get("selected_strategies") or [],
        "market_summary":  ep.get("market_summary") or {},
        "vote_summary":    ep.get("vote_summary") or {},
        "votes":           ep.get("votes") or [],
        "order_quality_summary": ep.get("order_quality_summary") or {},
        "outcome_summary": ep.get("outcome_summary") or {},
        "sell_reason_summary": ep.get("sell_reason_summary") or {},
        "review_summary":  ep.get("review_summary") or {},
        "broker_order_no": ep.get("broker_order_no"),
        "is_order_signal":       False,
        "is_live_authorization": False,
    }
    # 이중 가드: 금지 키 거부 + 값 단위 secret sanitize (fail-closed).
    return assert_export_safe(record)


# ─────────────────────────────────────────────────────────────────────────────
# writer
# ─────────────────────────────────────────────────────────────────────────────


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M%S")


def export_decision_episodes_csv(
    episodes: list[dict[str, Any]],
    options: DecisionEpisodeExportOptions | None = None,
    *,
    output_dir: str | Path = "exports/decision_episodes",
    now: datetime | None = None,
) -> dict[str, Any]:
    """필터된 episode → CSV (utf-8-sig, 엑셀 호환). 결과 메타 dict 반환."""
    opts = options or DecisionEpisodeExportOptions()
    rows = [episode_to_csv_row(e) for e in filter_episodes(episodes, opts)]
    # 안전 가드 — CSV row 도 금지 키/secret 검사.
    for r in rows:
        assert_export_safe(r)
    base = Path(output_dir)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"decision_episodes_{_stamp(now)}.csv"
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(CSV_COLUMNS), extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    path.write_text(buf.getvalue(), encoding="utf-8-sig")
    return _result("csv", len(rows), path)


def export_decision_episodes_jsonl(
    episodes: list[dict[str, Any]],
    options: DecisionEpisodeExportOptions | None = None,
    *,
    output_dir: str | Path = "exports/decision_episodes",
    now: datetime | None = None,
) -> dict[str, Any]:
    """필터된 episode → JSONL (UTF-8, 한 줄당 episode 1개). 결과 메타 dict 반환."""
    opts = options or DecisionEpisodeExportOptions()
    records = [episode_to_jsonl_record(e) for e in filter_episodes(episodes, opts)]
    base = Path(output_dir)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"decision_episodes_{_stamp(now)}.jsonl"
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    path.write_text(("\n".join(lines) + ("\n" if lines else "")), encoding="utf-8")
    return _result("jsonl", len(records), path)


def export_decision_episodes(
    episodes: list[dict[str, Any]],
    options: DecisionEpisodeExportOptions | None = None,
    *,
    fmt: str = "csv",
    output_dir: str | Path = "exports/decision_episodes",
    now: datetime | None = None,
) -> dict[str, Any]:
    """format dispatcher — 'csv' 또는 'jsonl'."""
    f = str(fmt or "csv").strip().lower()
    if f == "jsonl":
        return export_decision_episodes_jsonl(episodes, options,
                                              output_dir=output_dir, now=now)
    if f == "csv":
        return export_decision_episodes_csv(episodes, options,
                                            output_dir=output_dir, now=now)
    raise ValueError(f"unsupported export format: {fmt}")


def _result(fmt: str, row_count: int, path: Path) -> dict[str, Any]:
    return {
        "format":       fmt,
        "row_count":    int(row_count),
        "file_path":    str(path),
        "contains_secret":       False,
        "is_order_signal":       False,
        "is_live_authorization": False,
        "advisory_disclaimer": (
            "분석용 데이터 export — 실제 계좌정보/API key 는 포함되지 않으며 주문 "
            "신호가 아닙니다. CSV 는 엑셀, JSONL 은 파이썬/학습 분석용입니다."
        ),
    }


__all__ = [
    "DecisionEpisodeExportOptions", "ExportSafetyError",
    "FORBIDDEN_KEYS", "CSV_COLUMNS",
    "reject_forbidden_keys", "assert_export_safe", "sanitize_export_record",
    "filter_episodes", "episode_to_csv_row", "episode_to_jsonl_record",
    "export_decision_episodes_csv", "export_decision_episodes_jsonl",
    "export_decision_episodes",
    "SecretLeakError",
]
