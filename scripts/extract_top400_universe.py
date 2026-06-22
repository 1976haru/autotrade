"""시총 상위 400 보통주 코드 추출 — 종목 풀 확장(100→400)용.

소스: fdr.StockListing('KRX') (Marcap=시가총액). 코퍼스(_tmp_kr_daily_corpus.py)
제외 규칙 재사용: 우선주·SPAC 제외 + ETF/ETN/리츠 제외 + 6자리 + KOSPI/KOSDAQ/
KOSDAQ GLOBAL(KONEX 제외). read-only — 주문/네트워크 주문 0건, 시세 listing 조회만.

산출: data/market/universe_top400.json (codes/names) + 연속성 리포트(현재 100 ⊂ 400?).
실행: python scripts/extract_top400_universe.py
"""
import os, re, json, sys
import FinanceDataReader as fdr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

SPAC_PAT = r"스팩|기업인수목적"
PREF_PAT = r"우[A-Z0-9]?$|우선주$"           # 우선주(명말 '우'/'우B')
# ★리츠 는 '끝매칭'(롯데리츠 등) — 부분매칭은 메리츠금융지주 오제외. ETF 브랜드는 접두 ^.
ETF_PAT  = r"ETF$|ETN$|리츠$|REIT|레버리지|인버스|^KODEX|^TIGER|^ARIRANG|^KBSTAR|^KOSEF|^PLUS |^ACE |^SOL |^RISE |^HANARO"


def extract_top_n(n: int = 400):
    df = fdr.StockListing("KRX").copy()
    df["Code"] = df["Code"].astype(str)
    df = df[df["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])]   # KONEX 제외
    df = df[df["Code"].str.fullmatch(r"\d{6}")]
    nm = df["Name"].astype(str)
    m_spac = nm.str.contains(SPAC_PAT, regex=True)
    m_pref = nm.str.contains(PREF_PAT, regex=True)
    m_etf = nm.str.contains(ETF_PAT, regex=True)
    excl = {"spac": int(m_spac.sum()), "pref": int((m_pref & ~m_spac).sum()),
            "etf": int((m_etf & ~m_spac & ~m_pref).sum())}
    df = df[~m_spac & ~m_pref & ~m_etf].dropna(subset=["Marcap"])
    df = df.sort_values("Marcap", ascending=False).reset_index(drop=True)
    top = df.head(n)
    return top, excl, len(df)


def main():
    top, excl, pool = extract_top_n(400)
    codes = top["Code"].tolist()
    names = dict(zip(codes, top["Name"].astype(str).tolist()))

    from app.universe.default_universe import FALLBACK_MARKET_CAP_TOP100 as CUR
    s400 = set(codes)
    missing = [c for c in CUR if c not in s400]

    print(f"보통주 풀 {pool} (제외 spac={excl['spac']} pref={excl['pref']} etf={excl['etf']})")
    print(f"top400: 1위 {names[codes[0]]}({codes[0]}) ~ 400위 {names[codes[-1]]}({codes[-1]})"
          f" / 400위 시총 {int(top.iloc[-1]['Marcap'])/1e8:.0f}억")
    print(f"연속성: 현재 100 ∩ 400 = {len(set(CUR) & s400)}/100 · 미포함 {len(missing)}: {missing}")

    out = os.path.join(ROOT, "data", "market", "universe_top400.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"count": len(codes), "codes": codes, "names": names,
                   "current100_not_in_400": missing}, f, ensure_ascii=False, indent=0)
    print("저장:", out)


if __name__ == "__main__":
    main()
