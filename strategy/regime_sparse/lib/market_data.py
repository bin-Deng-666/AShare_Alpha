"""Market / index loaders for regime_sparse backtest."""

from __future__ import annotations

import duckdb
import pandas as pd

from config.params import CATALOG_PATH, DATA_ROOT

HS300_PARQUET = DATA_ROOT / "parquet" / "reference" / "index_bars" / "000300.parquet"


def load_hs300(start: str, end: str | None = None) -> pd.DataFrame:
    """HS300 daily bars; ``ret`` is close-to-close (aligned with strategy equity MTM)."""
    path = str(HS300_PARQUET).replace("'", "''")
    end_clause = f"AND date <= DATE '{end}'" if end else ""
    df = duckdb.sql(
        f"""
        SELECT date, open, close
        FROM read_parquet('{path}')
        WHERE date >= DATE '{start}' {end_clause}
        ORDER BY date
        """
    ).df()
    df["date"] = pd.to_datetime(df["date"])
    df["ret"] = df["close"].pct_change()
    return df.dropna(subset=["ret"])


def load_bars(start: str, end: str | None = None) -> pd.DataFrame:
    """OHLC bars. If ``end`` is set, load a few extra sessions so last T+1 pending can fill."""
    con = duckdb.connect(str(CATALOG_PATH), read_only=True)
    if end:
        # Buffer past end so portfolio can flush pending on the next open.
        df = con.execute(
            """
            SELECT code, date, open, high, low, close, is_limit_up, is_limit_down
            FROM stock_daily
            WHERE date >= ?
              AND date <= CAST(? AS DATE) + INTERVAL 10 DAY
            """,
            [start, end],
        ).df()
    else:
        df = con.execute(
            """
            SELECT code, date, open, high, low, close, is_limit_up, is_limit_down
            FROM stock_daily WHERE date >= ?
            """,
            [start],
        ).df()
    con.close()
    return df
