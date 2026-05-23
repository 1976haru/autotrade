"""P-30: Paper Gate 성과 리포트 export (JSON / Markdown).

생성 리포트는 `reports/paper_gate/` 에 저장한다 (`.gitignore` 로 미추적 —
운영 로그는 git 미커밋). export 파일에 secret / 계좌 / API key 는 없다 (리포트
dict 가 이미 sanitize 됨).

## 절대 invariant
- 본 모듈은 *직렬화/파일 작성 전용* — broker / OrderExecutor / route_order
  import 0건.
- 과장된 수익 약속 표현(보장/확정/무조건 류) 0건.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_GRADE_LABEL = {
    "INSUFFICIENT_SAMPLE": "표본 부족 (실전 전환 검토 불가)",
    "BLOCKED_BY_RISK": "리스크로 인해 차단",
    "NOT_READY": "실전 전환 보류",
    "READY_FOR_EXTENDED_PAPER": "추가 Paper 검증 필요",
    "READY_FOR_SMALL_LIVE_CANARY_REVIEW": "소액 실전(canary) 검토 가능",
}


def report_to_json(report: dict[str, Any]) -> str:
    """리포트 dict → JSON 문자열."""
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)


def _pct(v: Any) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _signed(v: Any) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _num(v: Any) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def report_to_markdown(report: dict[str, Any]) -> str:
    """리포트 dict → Markdown 문자열 (14 섹션)."""
    s = report.get("sample", {})
    p = report.get("performance", {})
    rd = report.get("readiness", {})
    grade = rd.get("grade", "UNKNOWN")
    period = report.get("period", {})
    L: list[str] = []
    L.append("# Paper Gate 성과 리포트")
    L.append("")
    L.append(f"- report_id: `{report.get('report_id', '')}`")
    L.append(f"- created_at: {report.get('created_at', '')}")
    L.append(f"- 기간: {period.get('start_date')} ~ {period.get('end_date')} "
             f"(거래일 {report.get('trading_days', 0)}일)")
    L.append("")

    L.append("## 1. 실행 요약")
    L.append(f"- 실전 전환 가능성 등급: **{grade}** "
             f"({_GRADE_LABEL.get(grade, grade)})")
    L.append(f"- 소액 실전(canary) 검토 가능 여부: "
             f"{'예 (자동 전환 아님 · 수동 승인 필요)' if rd.get('can_review_live_canary') else '아니오'}")
    L.append("")

    L.append("## 2. 표본 크기")
    L.append(f"- 총 판단수: {s.get('total_decisions', 0)}")
    L.append(f"- 총 주문수: {s.get('total_orders', 0)}")
    L.append(f"- 체결수: {s.get('filled_orders', 0)}")
    L.append(f"- 평가 거래수(성과 라벨링 완료): {s.get('evaluated_trades', 0)}")
    L.append(f"- 100건 기준 충족: {'예' if s.get('meets_100_sample') else '아니오'}")
    L.append("")

    L.append("## 3. 전체 성과 (Paper 추정)")
    L.append(f"- 승률: {_pct(p.get('win_rate'))}")
    L.append(f"- 평균 수익률: {_signed(p.get('average_return'))}")
    L.append(f"- 평균 이익 / 평균 손실: {_signed(p.get('average_win'))} / {_signed(p.get('average_loss'))}")
    L.append(f"- 손익비(payoff): {_num(p.get('payoff_ratio'))}")
    L.append(f"- profit factor: {_num(p.get('profit_factor'))}")
    L.append(f"- 최대 낙폭(MDD): {_pct(p.get('max_drawdown'))}")
    L.append(f"- 최대 연속 손실: {p.get('max_consecutive_losses', 0)}회")
    L.append(f"- 기대값(expectancy): {_signed(p.get('expectancy'))}")
    L.append("")

    L.append("## 4. 전략별 성과")
    for b in report.get("strategy_performance", {}).get("strategies", []):
        L.append(f"- {b.get('strategy')}: 평가 {b.get('evaluated_count', 0)}건 · "
                 f"승률 {_pct(b.get('win_rate'))} · PF {_num(b.get('profit_factor'))} · "
                 f"MDD {_pct(b.get('max_drawdown'))}")
    L.append("")

    L.append("## 5. 매수불가 사유 TOP")
    for r in report.get("blocked_reasons_top", []):
        L.append(f"- {r.get('reason_code')}: {r.get('count')}건")
    L.append("")

    L.append("## 6. 주문·체결 품질")
    oq = report.get("order_quality", {})
    L.append(f"- 상태 분포: {oq.get('by_order_status', {})}")
    L.append(f"- 평균 체결 지연: {oq.get('avg_latency_ms')}ms · "
             f"평균 슬리피지: {oq.get('avg_slippage_bps')}bps")
    L.append(f"- 거절 {oq.get('rejected_count', 0)}건 · 부분체결 {oq.get('partial_fill_count', 0)}건")
    L.append("")

    L.append("## 7. 매도 사유별 성과")
    for code, v in report.get("sell_reason_performance", {}).items():
        L.append(f"- {code}: {v.get('count')}건 · 평가 {v.get('evaluated')}건 · "
                 f"평균 {_signed(v.get('average_return'))} · 승률 {_pct(v.get('win_rate'))}")
    L.append("")

    L.append("## 8. 복기 결과 (PostTradeReview)")
    rv = report.get("review_summary", {})
    L.append(f"- 등급 분포: {rv.get('by_grade', {})}")
    L.append(f"- 태그 분포: {rv.get('by_tag', {})}")
    L.append("")

    L.append("## 9. 포트폴리오 정합성")
    pi = report.get("portfolio_integrity", {})
    L.append(f"- 점검 {pi.get('checked', 0)}건 · 불일치 의심 {pi.get('mismatches', 0)}건 · "
             f"상태 {pi.get('status')}")
    L.append("")

    L.append("## 10. 실전 전환 가능성 등급")
    L.append(f"- 등급: **{grade}**")
    for r in rd.get("reasons", []):
        L.append(f"  - {r}")
    L.append("")

    L.append("## 11. 실전 전환 전 필수 보완")
    for a in report.get("required_actions", []):
        L.append(f"- {a}")
    L.append("")

    L.append("## 12. 한계 및 주의사항")
    L.append("- 수익률은 episode 추정 성과(P-25)이며 실제 계좌 잔고가 아닙니다.")
    L.append("- 모의 체결 품질은 실제와 다를 수 있습니다 (슬리피지/부분체결/호가 공백).")
    L.append("- 100건 미만이면 실전 전환 검토 불가입니다.")
    L.append("")

    L.append("## 13. 수익을 보장하지 않음 고지")
    L.append("- 본 리포트는 Paper 모의매매 성과 분석이며 실제 계좌 성과가 아닙니다.")
    L.append("- 실전 전환은 별도 수동 승인과 Live 자금 검토가 필요합니다.")
    L.append("- 등급이 좋아도 자동으로 실전 전환되지 않으며, 수익을 보장하지 않습니다.")
    L.append("")
    return "\n".join(L)


def export_report(
    report: dict[str, Any],
    *,
    out_dir: str | Path = "reports/paper_gate",
    now: datetime | None = None,
    write_json: bool = True,
    write_markdown: bool = True,
) -> dict[str, str]:
    """리포트를 reports/paper_gate/ 에 md/json 으로 저장. 작성 경로 dict 반환.

    broker / 주문 호출 0건. secret 0건 (리포트 dict 가 이미 sanitize 됨).
    """
    created = now or datetime.now(timezone.utc)
    stamp = created.strftime("%Y%m%d_%H%M")
    base = Path(out_dir)
    base.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    if write_markdown:
        md_path = base / f"paper_gate_report_{stamp}.md"
        md_path.write_text(report_to_markdown(report), encoding="utf-8")
        written["markdown"] = str(md_path)
    if write_json:
        json_path = base / f"paper_gate_report_{stamp}.json"
        json_path.write_text(report_to_json(report), encoding="utf-8")
        written["json"] = str(json_path)
    return written


__all__ = ["report_to_json", "report_to_markdown", "export_report"]
