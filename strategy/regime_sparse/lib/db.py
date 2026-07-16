from __future__ import annotations

import duckdb

from config.params import CATALOG_PATH, CACHE_DIR, DEFAULT_PARAMS, Params, TECH_INDUSTRY_SUBSTR


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(f"DuckDB catalog not found: {CATALOG_PATH}")
    return duckdb.connect(str(CATALOG_PATH), read_only=read_only)


def ensure_dirs() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def tech_sql(industry_col: str = "industry_name") -> str:
    parts = [f"coalesce({industry_col}, '') LIKE '%{s}%'" for s in TECH_INDUSTRY_SUBSTR]
    return "(" + " OR ".join(parts) + ")"


def register_params(con: duckdb.DuckDBPyConnection, p: Params = DEFAULT_PARAMS) -> None:
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE _params AS
        SELECT
            ?::INTEGER AS amt_lookback,
            ?::INTEGER AS ma_short,
            ?::INTEGER AS ma_long,
            ?::INTEGER AS ret_short,
            ?::INTEGER AS ret_crowd,
            ?::INTEGER AS dry_up_lookback,
            ?::INTEGER AS max_hold_days,
            ?::DOUBLE AS min_amount,
            ?::DOUBLE AS min_float_mcap,
            ?::INTEGER AS min_list_days,
            ?::BOOLEAN AS exclude_bj,
            ?::DOUBLE AS expand_vs_med,
            ?::DOUBLE AS expand_ma_ratio,
            ?::DOUBLE AS shrink_vs_med,
            ?::DOUBLE AS shrink_ma_ratio,
            ?::DOUBLE AS climax_vs_med,
            ?::DOUBLE AS thrust_amt_vs_med,
            ?::DOUBLE AS dry_amt_vs_med,
            ?::DOUBLE AS climax_amt_vs_med,
            ?::DOUBLE AS break_ret,
            ?::DOUBLE AS stall_dist_high,
            ?::INTEGER AS top_n,
            ?::DOUBLE AS cost_bps,
            ?::DOUBLE AS val_extreme_pct
        """,
        [
            p.amt_lookback,
            p.ma_short,
            p.ma_long,
            p.ret_short,
            p.ret_crowd,
            p.dry_up_lookback,
            p.max_hold_days,
            p.min_amount,
            p.min_float_mcap,
            p.min_list_days,
            p.exclude_bj,
            p.expand_vs_med,
            p.expand_ma_ratio,
            p.shrink_vs_med,
            p.shrink_ma_ratio,
            p.climax_vs_med,
            p.thrust_amt_vs_med,
            p.dry_amt_vs_med,
            p.climax_amt_vs_med,
            p.break_ret,
            p.stall_dist_high,
            p.top_n,
            p.cost_bps,
            p.val_extreme_pct,
        ],
    )
