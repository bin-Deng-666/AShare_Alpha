"""Market / index loaders shared by backtest CLI and experiments."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from config.params import CATALOG_PATH, DATA_ROOT

HS300_PARQUET = DATA_ROOT / "parquet" / "reference" / "index_bars" / "000300.parquet"


def load_hs300(start: str, end: str | None = None) -> pd.DataFrame:
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
    df["next_open"] = df["open"].shift(-1)
    df["ret"] = df["next_open"] / df["open"] - 1.0
    return df.dropna(subset=["ret"])


def load_bars(start: str, end: str | None = None) -> pd.DataFrame:
    con = duckdb.connect(str(CATALOG_PATH), read_only=True)
    if end:
        df = con.execute(
            """
            SELECT code, date, open, high, low, close, is_limit_up, is_limit_down
            FROM stock_daily WHERE date >= ? AND date <= ?
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
