"""Build cached signals parquet with SOTA rolling factors via DuckDB."""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "cache" / "signals_2020-01-01_latest.parquet"
OUT = ROOT / "cache" / "signals_2020-01-01_sota.parquet"


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT
    if not src.exists():
        raise SystemExit(f"missing {src}")
    src_s = str(src).replace("'", "''")
    out_s = str(out).replace("'", "''")
    print(f"building {out} from {src} …")
    duckdb.sql(
        f"""
        COPY (
          SELECT
            *,
            stddev_samp(pct_change) OVER (
              PARTITION BY code ORDER BY date
              ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
            ) AS ret_vol_20,
            stddev_samp(turnover_pct) OVER (
              PARTITION BY code ORDER BY date
              ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
            ) / nullif(
              avg(turnover_pct) OVER (
                PARTITION BY code ORDER BY date
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
              ), 0
            ) AS turn_cv_20,
            max(pct_change) OVER (
              PARTITION BY code ORDER BY date
              ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
            ) AS max_ret_20,
            abs(ret_20) / nullif(
              sum(abs(pct_change)) OVER (
                PARTITION BY code ORDER BY date
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
              ), 0
            ) AS path_eff_20
          FROM read_parquet('{src_s}')
        ) TO '{out_s}' (FORMAT PARQUET)
        """
    )
    n = duckdb.sql(f"SELECT count(*) FROM read_parquet('{out_s}')").fetchone()[0]
    print(f"done rows={n:,} -> {out}")


if __name__ == "__main__":
    main()
