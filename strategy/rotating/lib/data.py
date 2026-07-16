"""Reads from the repository's stable DuckDB research catalog."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
CATALOG_PATH = DATA_ROOT / "catalog" / "quant.duckdb"


def _connect(catalog_path: str | Path) -> duckdb.DuckDBPyConnection:
    path = Path(catalog_path)
    if not path.exists():
        raise FileNotFoundError(
            f"research catalog not found at {path}; run "
            "python data/scripts/build_dataset.py catalog"
        )
    return duckdb.connect(str(path), read_only=True)


def load_point_in_time_source(
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    *,
    catalog_path: str | Path = CATALOG_PATH,
) -> pd.DataFrame:
    """Load adjusted research bars with dated tradability, valuation and industry."""
    query = """
      SELECT
        d.code,
        d.date,
        d.exchange,
        d.open,
        d.high,
        d.low,
        d.close,
        d.volume,
        d.amount,
        d.turnover_pct,
        d.trade_status,
        d.is_st,
        d.pe_ttm,
        d.pb_mrq,
        CASE
          WHEN d.pe_ttm > 0 AND d.pb_mrq > 0 THEN d.pb_mrq / d.pe_ttm
          ELSE NULL
        END AS roe,
        d.float_market_cap,
        d.total_market_cap,
        d.limit_up,
        d.limit_down,
        d.is_limit_up,
        d.is_limit_down,
        d.limit_reference_price,
        d.limit_reference_source,
        d.limit_validation_status,
        d.cash_dividend_per_share,
        d.share_distribution_ratio,
        d.rights_issue_ratio,
        d.rights_issue_price,
        d.corporate_action_adjusted,
        d.corporate_action_formula_status,
        r.open AS raw_open,
        r.high AS raw_high,
        r.low AS raw_low,
        r.close AS raw_close,
        COALESCE(i.industry_name, 'UNKNOWN') AS industry,
        d.bar_source,
        d.indicator_source
      FROM stock_daily d
      LEFT JOIN stock_daily_raw r
        ON d.code = r.code
       AND d.date = r.date
      LEFT JOIN industry_membership i
        ON d.code = i.code
       AND d.date >= i.valid_from
       AND (i.valid_to IS NULL OR d.date < i.valid_to)
      WHERE d.exchange IN ('SH', 'SZ')
        AND d.date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY d.code, d.date
        ORDER BY TRY_CAST(i.industry_level AS INTEGER) DESC NULLS LAST,
                 i.valid_from DESC NULLS LAST
      ) = 1
      ORDER BY d.code, d.date
    """
    with _connect(catalog_path) as connection:
        return connection.execute(
            query,
            [str(pd.Timestamp(start).date()), str(pd.Timestamp(end).date())],
        ).df()


def load_execution_bars(
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    *,
    catalog_path: str | Path = CATALOG_PATH,
) -> pd.DataFrame:
    """Load unadjusted bars and dated exchange limits for fill simulation."""
    query = """
      SELECT
        code,
        date,
        open,
        high,
        low,
        close,
        amount,
        trade_status,
        limit_up,
        limit_down,
        is_limit_up,
        is_limit_down,
        cash_dividend_per_share,
        share_distribution_ratio,
        rights_issue_ratio,
        rights_issue_price,
        corporate_action_adjusted,
        corporate_action_formula_status
      FROM stock_daily_raw
      WHERE exchange IN ('SH', 'SZ')
        AND date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
      ORDER BY date, code
    """
    with _connect(catalog_path) as connection:
        return connection.execute(
            query,
            [str(pd.Timestamp(start).date()), str(pd.Timestamp(end).date())],
        ).df()


def load_index_bars(
    code: str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    *,
    catalog_path: str | Path = CATALOG_PATH,
) -> pd.DataFrame:
    """Load one benchmark through the catalog's logical index table."""
    query = """
      SELECT date, open, high, low, close, volume, amount, pct_change
      FROM index_daily_bars
      WHERE index_code = ?
        AND date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
      ORDER BY date
    """
    with _connect(catalog_path) as connection:
        return connection.execute(
            query,
            [
                str(code).replace("sh.", "").replace("sz.", "").zfill(6),
                str(pd.Timestamp(start).date()),
                str(pd.Timestamp(end).date()),
            ],
        ).df()


def load_security_master(
    *,
    catalog_path: str | Path = CATALOG_PATH,
) -> pd.DataFrame:
    """Load the catalog's stable security identity fields."""
    with _connect(catalog_path) as connection:
        return connection.execute(
            "SELECT code, name, exchange, list_date, delist_date FROM security_master"
        ).df()
