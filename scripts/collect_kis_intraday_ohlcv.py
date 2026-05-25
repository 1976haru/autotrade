#!/usr/bin/env python3
"""KIS-INTRADAY-100-VALIDATION-01 — KIS read-only 분봉 수집 CLI.

주식일별분봉조회 [국내주식-213] (GET /uapi/domestic-stock/v1/quotations/
inquire-time-dailychartprice, TR FHKST03010230) 로 국내주식 분봉(1분)을 수집해
5분봉으로 resample 후 표준 OHLCV CSV 로 저장한다.

CLAUDE.md 절대 원칙 (본 스크립트):
- **read-only 시세 조회만.** broker.place_order / route_order / OrderExecutor 호출 0건.
- KIS 주문 API(order-cash 등) 호출 0건 — `inquire_time_dailychartprice` 만 사용.
- 안전 flag(ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / KIS_IS_PAPER) 변경 0건.
- appkey / appsecret / access token / 계좌번호 원문 출력 0건.
- 수집 실패를 성공처럼 보고 0건 — 실패는 reason 과 함께 기록(PARTIAL_SUCCESS).
- 품질 FAIL 은 본 스크립트가 판정하지 않음(수집만) — 품질검증은 후속 단계.

exit code:
    0: 1종목 이상 수집 성공 (PARTIAL_SUCCESS 포함)
    1: 전 종목 수집 실패 (네트워크 / 자격 / API 오류)
    2: 실행 오류 (인자 / 환경)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_KST = timezone(timedelta(hours=9))
DEFAULT_OUTPUT_DIR = "data/market/intraday_ohlcv/kis"


def _eprint(*a: object) -> None:
    print(*a, file=sys.stderr, flush=True)


# 초당 거래건수 초과(EGW00201) → 짧은 backoff 재시도 가능.
_RATE_LIMIT_MARKERS = ("초당 거래건수", "EGW00201")
# 토큰 발급 1분당 1회(EGW00133) → 토큰은 캐시 재사용으로 회피 (재시도 금지).
_TOKEN_LIMIT_MARKER = "EGW00133"


def _token_cache_path() -> Path:
    return _REPO_ROOT / "data" / "market" / ".kis_token_cache.json"


def _seed_cached_token(client, is_paper: bool) -> bool:
    """gitignored 캐시에서 유효 토큰을 재사용 — 토큰 1분당 1회 제한 회피."""
    p = _token_cache_path()
    if not p.exists():
        return False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    if d.get("is_paper") != is_paper or not d.get("access_token") or not d.get("expires_at"):
        return False
    try:
        exp = datetime.fromisoformat(d["expires_at"])
    except ValueError:
        return False
    if datetime.now(timezone.utc) >= exp - timedelta(seconds=120):
        return False
    client._token = d["access_token"]
    client._token_expires_at = exp
    return True


def _save_cached_token(client, is_paper: bool) -> None:
    if not (getattr(client, "_token", None) and getattr(client, "_token_expires_at", None)):
        return
    p = _token_cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "access_token": client._token,
        "expires_at": client._token_expires_at.isoformat(),
        "is_paper": is_paper,
    }), encoding="utf-8")


async def _call_with_retry(coro_factory, sleep: float, retries: int = 3):
    """read-only 호출 — 초당 거래건수 초과(EGW00201) 시 backoff 재시도.

    토큰 발급 제한(EGW00133)은 재시도하지 않고 그대로 raise (캐시로 회피해야 함).
    """
    delay = max(sleep, 0.5)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return await coro_factory()
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            last = e
            if _TOKEN_LIMIT_MARKER in msg:
                raise
            if any(m in msg for m in _RATE_LIMIT_MARKERS):
                wait = delay * (2 ** attempt)
                _eprint(f"    [rate-limit] backoff {wait:.1f}s (attempt {attempt + 1})")
                time.sleep(wait)
                continue
            raise
    raise last if last else RuntimeError("retry exhausted")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KIS read-only 분봉 수집 (주문 0건).")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--symbols", default=None, help="콤마 구분 종목코드")
    g.add_argument("--symbols-file", default=None, help="한 줄당 종목코드 파일")
    g.add_argument("--from-universe", action="store_true",
                   help="kis_intraday_universe 로 100종목 자동 구성")
    p.add_argument("--end", default=None, help="조회 종료일 YYYYMMDD (기본: 오늘 KST)")
    p.add_argument("--num-days", type=int, default=20, help="수집할 거래일 수 (backward)")
    p.add_argument("--bar-size", default="5m", choices=["1m", "5m"], help="저장 bar 크기")
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--sleep-seconds", type=float, default=0.25, help="호출 간 대기")
    p.add_argument("--max-symbols", type=int, default=0, help="0=전체")
    p.add_argument("--max-calls-per-day", type=int, default=6, help="종목/일 backward 호출 상한")
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--markdown", default=None)
    return p.parse_args(argv)


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    from app.market_data.kis_intraday_universe import (
        build_kis_intraday_validation_universe,
        validate_symbol,
    )
    syms: list[str] = []
    if args.from_universe:
        syms = list(build_kis_intraday_validation_universe(100).symbols)
    elif args.symbols_file:
        text = Path(args.symbols_file).read_text(encoding="utf-8")
        syms = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    elif args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        syms = list(build_kis_intraday_validation_universe(100).symbols)
    syms = [s for s in syms if validate_symbol(s)]
    if args.max_symbols and args.max_symbols > 0:
        syms = syms[: args.max_symbols]
    # 순서 보존 dedupe.
    seen: set[str] = set()
    return [s for s in syms if not (s in seen or seen.add(s))]


async def _discover_trading_days(
    client, ref_symbol: str, end: str, num_days: int, sleep: float, max_calls_per_day: int,
) -> list[str]:
    """ref_symbol 로 거래일(데이터 존재 일자)을 backward 로 num_days 개 찾는다."""
    from app.market_data.kis_intraday_fetch import parse_minute_rows

    days: list[str] = []
    cur = datetime.strptime(end, "%Y%m%d").replace(tzinfo=_KST)
    guard = 0
    while len(days) < num_days and guard < num_days * 2 + 20:
        guard += 1
        date = cur.strftime("%Y%m%d")
        cur = cur - timedelta(days=1)
        if datetime.strptime(date, "%Y%m%d").weekday() >= 5:  # 주말 skip
            continue
        try:
            data = await _call_with_retry(
                lambda d=date: client.inquire_time_dailychartprice(ref_symbol, date=d, hour="153000"),
                sleep)
        except Exception as e:  # noqa: BLE001
            _eprint(f"  [discover] {date} ERROR {type(e).__name__}: {str(e)[:120]}")
            time.sleep(sleep)
            continue
        pr = parse_minute_rows(data.get("output2"), ref_symbol)
        if pr.records:
            days.append(date)
        time.sleep(sleep)
    return sorted(days)


def _prev_weekday(date_yyyymmdd: str) -> str:
    d = datetime.strptime(date_yyyymmdd, "%Y%m%d") - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def _earliest_key(records: list[dict]) -> tuple[str, str]:
    """가장 이른 bar 의 (date, HHMMSS) — timestamp ISO 에서 추출."""
    best_date, best_hour = "99999999", "999999"
    for r in records:
        ts = str(r.get("timestamp", ""))  # 2026-05-21T15:30:00+09:00
        if len(ts) < 19:
            continue
        d = ts[0:4] + ts[5:7] + ts[8:10]
        h = ts[11:13] + ts[14:16] + ts[17:19]
        if (d, h) < (best_date, best_hour):
            best_date, best_hour = d, h
    return best_date, best_hour


async def _collect_symbol_backward(
    client, symbol: str, end_date: str, oldest_date: str, sleep: float, max_calls: int,
) -> tuple[list[dict], int]:
    """한 종목의 분봉을 (date,hour) 연속 backward walk 로 oldest_date 까지 수집.

    KIS 분봉(FID_PW_DATA_INCU_YN=Y)은 한 호출에 120 bar 를 일자 경계를 넘어 과거로 반환한다.
    가장 이른 bar 바로 직전 (date,hour) 로 다음 호출을 이어붙인다.
    """
    from app.market_data.kis_intraday_fetch import parse_minute_rows, prev_minute_hour

    recs: list[dict] = []
    date, hour = end_date, "153000"
    calls = 0
    empty_streak = 0
    prev_earliest: tuple[str, str] | None = None
    while calls < max_calls:
        calls += 1
        try:
            data = await _call_with_retry(
                lambda d=date, h=hour: client.inquire_time_dailychartprice(symbol, date=d, hour=h),
                sleep)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"{type(e).__name__}: {str(e)[:120]}") from e
        pr = parse_minute_rows(data.get("output2"), symbol)
        time.sleep(sleep)
        if not pr.records:
            # 휴일/빈 구간 — 이전 거래일 15:30 으로 점프 (몇 번까지만).
            empty_streak += 1
            if empty_streak > 3 or date <= oldest_date:
                break
            date, hour = _prev_weekday(date), "153000"
            continue
        empty_streak = 0
        recs.extend(pr.records)
        e_date, e_hour = _earliest_key(pr.records)
        if e_date <= oldest_date:
            break
        if prev_earliest is not None and (e_date, e_hour) >= prev_earliest:
            break  # 진전 없음
        prev_earliest = (e_date, e_hour)
        nh = prev_minute_hour(e_hour)
        if nh >= e_hour or nh < "090000":
            date, hour = _prev_weekday(e_date), "153000"
        else:
            date, hour = e_date, nh
    return recs, calls


async def _run(args: argparse.Namespace) -> dict:
    from app.core.config import Settings
    from app.brokers.kis_client import KisClient
    from app.core.rate_limiter import SlidingWindowRateLimiter
    from app.market_data.kis_intraday_fetch import dedupe_by_timestamp, resample_1m_to_5m

    _env_file = _BACKEND_DIR / ".env"
    s = Settings(_env_file=str(_env_file)) if _env_file.exists() else Settings()
    if not (s.kis_app_key and s.kis_app_secret):
        raise RuntimeError("KIS 자격(app_key/app_secret) 미설정 — 수집 불가")
    # KIS 모의(paper) 시세 호출은 초당 건수 제한이 빡빡 — 보수적으로 2 calls/sec.
    rl = SlidingWindowRateLimiter(max_calls=2, window_seconds=1.1)
    client = KisClient(s.kis_app_key, s.kis_app_secret, is_paper=s.kis_is_paper, rate_limiter=rl)
    if _seed_cached_token(client, s.kis_is_paper):
        _eprint("[collect] 캐시된 access token 재사용 (토큰 재발급 안 함)")

    symbols = _resolve_symbols(args)
    end = args.end or datetime.now(_KST).strftime("%Y%m%d")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    _eprint(f"[collect] symbols={len(symbols)} end={end} num_days={args.num_days} "
            f"bar={args.bar_size} paper={s.kis_is_paper}")

    ref = symbols[0] if symbols else "005930"
    trading_days = await _discover_trading_days(
        client, ref, end, args.num_days, args.sleep_seconds, args.max_calls_per_day)
    _save_cached_token(client, s.kis_is_paper)
    _eprint(f"[collect] trading_days={len(trading_days)} ({trading_days[:3]}..{trading_days[-1:]})")
    if not trading_days:
        return {
            "status": "FAIL", "requested": len(symbols), "succeeded": 0, "failed": len(symbols),
            "trading_days": [], "bar_size": args.bar_size, "per_symbol": [],
            "reasons": ["거래일을 찾지 못함 — 네트워크/자격/날짜 확인"],
            "total_bars": 0,
        }

    oldest_date = trading_days[0]
    # 종목당 호출 상한: 거래일 수 × 4(하루 ≈ 4 call) + 여유.
    max_calls_symbol = args.num_days * 5 + 6
    per_symbol: list[dict] = []
    succeeded = 0
    total_bars = 0
    for i, sym in enumerate(symbols, 1):
        err: str | None = None
        try:
            one_min, _calls = await _collect_symbol_backward(
                client, sym, end, oldest_date, args.sleep_seconds, max_calls_symbol)
        except RuntimeError as e:
            err = str(e)
            one_min = []
        one_min = dedupe_by_timestamp(one_min)
        days_with_data = len({str(r["timestamp"])[:10] for r in one_min})
        if args.bar_size == "5m":
            bars = dedupe_by_timestamp(resample_1m_to_5m(one_min))
        else:
            bars = one_min
        if err and not bars:
            per_symbol.append({"symbol": sym, "status": "FAILED", "bars": 0,
                               "days_with_data": days_with_data, "reason": err})
            _eprint(f"  [{i}/{len(symbols)}] {sym} FAILED {err}")
            continue
        if not bars:
            per_symbol.append({"symbol": sym, "status": "NO_DATA", "bars": 0,
                               "days_with_data": days_with_data, "reason": "빈 응답(상장폐지/거래정지 의심)"})
            _eprint(f"  [{i}/{len(symbols)}] {sym} NO_DATA")
            continue
        # CSV 저장.
        fname = f"{sym}_{args.bar_size}.csv"
        fpath = out_dir / fname
        with fpath.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume", "symbol"])
            for b in bars:
                w.writerow([b["timestamp"], b["open"], b["high"], b["low"],
                            b["close"], b["volume"], b["symbol"]])
        succeeded += 1
        total_bars += len(bars)
        status = "OK" if not err else "PARTIAL_SUCCESS"
        per_symbol.append({"symbol": sym, "status": status, "bars": len(bars),
                           "days_with_data": days_with_data,
                           "reason": err or "", "file": str(fpath)})
        _eprint(f"  [{i}/{len(symbols)}] {sym} {status} bars={len(bars)} days={days_with_data}")

    failed = len(symbols) - succeeded
    overall = "OK" if failed == 0 and succeeded else (
        "PARTIAL_SUCCESS" if succeeded else "FAIL")
    return {
        "status": overall, "requested": len(symbols), "succeeded": succeeded, "failed": failed,
        "trading_days": trading_days, "trading_day_count": len(trading_days),
        "bar_size": args.bar_size, "end": end, "output_dir": str(out_dir),
        "total_bars": total_bars, "per_symbol": per_symbol,
        # 안전 불변값.
        "is_live_authorization": False, "broker_order_sent": False, "order_created": False,
        "kis_order_api_called": False, "contains_secret": False,
        "reasons": [r for r in ([] if succeeded else ["전 종목 수집 실패"])],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _render_md(rep: dict) -> str:
    lines = [
        "# KIS 분봉 수집 결과 (read-only, 주문 0건)",
        "",
        f"- status: **{rep['status']}**",
        f"- 요청 {rep['requested']} / 성공 {rep['succeeded']} / 실패 {rep['failed']}",
        f"- 거래일 {rep.get('trading_day_count', 0)}일 · bar {rep.get('bar_size')} · "
        f"total_bars {rep.get('total_bars', 0)}",
        "",
        "| symbol | status | bars | days | reason |",
        "|---|---|---|---|---|",
    ]
    for p in rep.get("per_symbol", []):
        lines.append(f"| {p['symbol']} | {p['status']} | {p['bars']} | "
                     f"{p.get('days_with_data', 0)} | {p.get('reason', '')[:40]} |")
    lines += ["", "> KIS 주식일별분봉조회 (FHKST03010230) read-only · 주문 API 호출 0건 · "
              "is_live_authorization=false · 수익 보장 아님."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        args = _parse_args(argv)
        rep = asyncio.run(_run(args))
        out = Path(args.json_out) if args.json_out else (
            _REPO_ROOT / "reports/strategy_validation/kis_intraday_collect.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] JSON: {out}")
        if args.markdown:
            mp = Path(args.markdown)
            mp.parent.mkdir(parents=True, exist_ok=True)
            mp.write_text(_render_md(rep), encoding="utf-8")
            print(f"[OK] Markdown: {mp}")
        print(f"status={rep['status']} succeeded={rep['succeeded']}/{rep['requested']} "
              f"total_bars={rep.get('total_bars', 0)} trading_days={rep.get('trading_day_count', 0)}")
        return 0 if rep["succeeded"] else 1
    except Exception as e:  # noqa: BLE001
        _eprint(f"[ERROR] {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
