import csv
from datetime import datetime
from pathlib import Path

from app.backtest.types import Bar


REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


def load_bars_from_csv(path: str | Path, symbol: str) -> list[Bar]:
    """ISO 8601 timestamp + 정수 OHLCV 컬럼을 가진 CSV에서 봉을 읽는다.

    Korean stock prices are integer KRW so OHLC columns are coerced to int.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)

    bars: list[Bar] = []
    with p.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV is missing required columns: {missing}")
        for row in reader:
            bars.append(Bar(
                symbol=symbol,
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=int(row["open"]),
                high=int(row["high"]),
                low=int(row["low"]),
                close=int(row["close"]),
                volume=int(row["volume"]),
            ))
    return bars
