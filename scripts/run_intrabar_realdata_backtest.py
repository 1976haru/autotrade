#!/usr/bin/env python3
"""실제 1분봉 intrabar 백테스트 리포트 생성 (CHECKLIST-04 P2, 백테스트 전용).

  python scripts/run_intrabar_realdata_backtest.py [--write-latest]

→ reports/backtest/intrabar_realdata_backtest_{result,latest}.{json,md}
  + trade_replay_sample.json

실제 1분봉이 없으면 NOT_READY 로 정직 보고. 실 KIS / broker / 주문 호출 0건, read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.system.intrabar_realdata_backtest import run_realdata_backtest  # noqa: E402

_OUT = Path("reports/backtest")
_TEN = ["005930", "000660", "005380", "000270", "012330",
        "042700", "066570", "006400", "035420", "051910"]


def _md(r: dict) -> str:
    cov = r["coverage"]
    L = [f"# Intrabar 실데이터 백테스트 (연구용) — verdict: {r['verdict']}", "",
         f"> {r['disclaimer']}", "",
         f"- confidence: **{r['confidence_level']}** · 1분봉 종목 {cov['symbol_count_1m']}",
         f"- replayable_trades: {cov['replayable_trades']}"]
    if r["available"]:
        e = r["execution_5m_vs_1m"]
        L += ["", "## 5분봉 vs 1분봉 replay",
              f"- 5m: {e['five_minute']}", f"- 1m: {e['intrabar_1m']}",
              f"- pf_delta={e['pf_delta']} return_delta={e['return_delta']}",
              "", "## earliest-first vs composite",
              f"- {r['ranking']}", "", "## 비용/슬리피지 stress",
              *[f"- {s}" for s in r["cost_slippage_stress"]],
              "", f"## ambiguous {r['ambiguous_trade_count']} / stop-first "
              f"{r['conservative_stop_first_count']}",
              "", "## regime attribution", f"- {r['regime_attribution']}",
              "## symbol group attribution", f"- {r['symbol_group_attribution']}"]
    L += ["", "## 결론", *[f"- {c}" for c in r["conclusion"]],
          "", "## 다음 단계", *[f"- {c}" for c in r["next_steps"]],
          "", f"- is_live_authorization={r['is_live_authorization']} · "
          f"no_profit_guarantee={r['no_profit_guarantee']}"]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Intrabar realdata backtest report")
    p.add_argument("--one-min-dir", default="data/market/robust_intraday_1m_subset")
    p.add_argument("--five-min-dir", default="data/market/intraday_ohlcv/kis_6m")
    p.add_argument("--symbols", default=",".join(_TEN))
    p.add_argument("--write-latest", action="store_true")
    p.add_argument("--out-dir", default=str(_OUT))
    args = p.parse_args(argv)

    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    r = run_realdata_backtest(one_min_dir=args.one_min_dir,
                              five_min_dir=args.five_min_dir, symbols=syms)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "intrabar_realdata_backtest_result.json").write_text(
        json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "intrabar_realdata_backtest_result.md").write_text(_md(r), encoding="utf-8")
    (out / "trade_replay_sample.json").write_text(
        json.dumps(r.get("trade_replay_sample", []), ensure_ascii=False, indent=2),
        encoding="utf-8")
    if args.write_latest:
        (out / "intrabar_realdata_backtest_latest.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] verdict={r['verdict']} confidence={r['confidence_level']} "
          f"available={r['available']} symbols_1m={r['coverage']['symbol_count_1m']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
