"""Build v2.6 enriched signals: industry crowding + limit-up + earnings blackout."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
CATALOG = REPO / "data" / "catalog" / "quant.duckdb"
SRC_DEFAULT = ROOT / "cache" / "signals_2020-01-01_sota.parquet"
OUT_DEFAULT = ROOT / "cache" / "signals_2020-01-01_v26.parquet"


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC_DEFAULT
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT_DEFAULT
    if not src.exists():
        # fall back to base panel
        alt = ROOT / "cache" / "signals_2020-01-01_latest.parquet"
        if not alt.exists():
            raise SystemExit(f"missing {src}")
        src = alt
    if not CATALOG.exists():
        raise SystemExit(f"missing catalog {CATALOG}")

    src_s = str(src).replace("'", "''")
    out_s = str(out).replace("'", "''")
    cat_s = str(CATALOG).replace("'", "''")
    print(f"building {out} from {src} …")

    con = duckdb.connect()
    con.execute(f"ATTACH '{cat_s}' AS quant (READ_ONLY)")
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE ind_daily AS
        SELECT
          d.date,
          i.industry_code,
          sum(d.amount) AS ind_amount
        FROM quant.stock_daily d
        JOIN quant.industry_membership i
          ON d.code = i.code
         AND i.classification_system = 'CSRC'
         AND i.industry_level = '1'
         AND d.date >= i.valid_from
         AND (i.valid_to IS NULL OR d.date <= i.valid_to)
        WHERE d.date >= DATE '2019-01-01'
        GROUP BY 1, 2
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE ind_share AS
        SELECT
          i.date,
          i.industry_code,
          i.ind_amount,
          i.ind_amount / nullif(sum(i.ind_amount) OVER (PARTITION BY i.date), 0) AS ind_share
        FROM ind_daily i
        """
    )
    # historical percentile of industry share within each industry (120d window approx via z→cdf-ish rank)
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE ind_crowd AS
        SELECT
          date,
          industry_code,
          ind_share,
          avg(ind_share) OVER (
            PARTITION BY industry_code ORDER BY date
            ROWS BETWEEN 119 PRECEDING AND CURRENT ROW
          ) AS share_ma120,
          stddev_samp(ind_share) OVER (
            PARTITION BY industry_code ORDER BY date
            ROWS BETWEEN 119 PRECEDING AND CURRENT ROW
          ) AS share_sd120,
          -- empirical CDF proxy: share vs rolling mean/std mapped to [0,1]
          least(1.0, greatest(0.0,
            0.5 + 0.5 * (
              (ind_share - avg(ind_share) OVER (
                PARTITION BY industry_code ORDER BY date
                ROWS BETWEEN 119 PRECEDING AND CURRENT ROW
              )) / nullif(stddev_samp(ind_share) OVER (
                PARTITION BY industry_code ORDER BY date
                ROWS BETWEEN 119 PRECEDING AND CURRENT ROW
              ), 0)
            ) / 2.0
          )) AS ind_crowd_pct_approx
        FROM ind_share
        """
    )
    # Better empirical percentile via self-join window count (bounded)
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE ind_crowd2 AS
        SELECT
          a.date,
          a.industry_code,
          a.ind_share,
          avg(CASE WHEN b.ind_share <= a.ind_share THEN 1.0 ELSE 0.0 END) AS ind_crowd_pct
        FROM ind_share a
        JOIN ind_share b
          ON a.industry_code = b.industry_code
         AND b.date BETWEEN a.date - INTERVAL 180 DAY AND a.date
        GROUP BY 1, 2, 3
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE limit_roll AS
        SELECT
          code,
          date,
          sum(CASE WHEN is_limit_up THEN 1 ELSE 0 END) OVER (
            PARTITION BY code ORDER BY date
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
          ) AS limit_up_5
        FROM quant.stock_daily
        WHERE date >= DATE '2019-01-01'
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE earn_days AS
        SELECT DISTINCT code, available_date AS earn_date
        FROM quant.financial_availability
        WHERE available_date IS NOT NULL
          AND available_date >= DATE '2019-01-01'
        """
    )

    con.execute(
        f"""
        COPY (
          SELECT
            s.* EXCLUDE (crowding_score, crowding_bucket),
            coalesce(l.limit_up_5, 0) AS limit_up_5,
            coalesce(c.ind_crowd_pct, 0.5) AS ind_crowd_pct,
            CASE
              WHEN coalesce(c.ind_crowd_pct, 0.5) >= 0.85 THEN 'high'
              WHEN coalesce(c.ind_crowd_pct, 0.5) >= 0.70 THEN 'mid'
              ELSE 'low'
            END AS ind_crowd_bucket,
            EXISTS (
              SELECT 1 FROM earn_days e
              WHERE e.code = s.code
                AND e.earn_date BETWEEN s.date - INTERVAL 1 DAY AND s.date + INTERVAL 1 DAY
            ) AS earnings_blackout,
            least(
              1.0,
              coalesce(s.crowding_score, 0)
                + least(0.25, coalesce(l.limit_up_5, 0) * 0.08)
            ) AS crowding_score,
            CASE
              WHEN least(
                1.0,
                coalesce(s.crowding_score, 0)
                  + least(0.25, coalesce(l.limit_up_5, 0) * 0.08)
              ) >= 0.90 THEN 'extreme'
              WHEN least(
                1.0,
                coalesce(s.crowding_score, 0)
                  + least(0.25, coalesce(l.limit_up_5, 0) * 0.08)
              ) >= 0.70 THEN 'high'
              WHEN least(
                1.0,
                coalesce(s.crowding_score, 0)
                  + least(0.25, coalesce(l.limit_up_5, 0) * 0.08)
              ) >= 0.40 THEN 'mid'
              ELSE 'low'
            END AS crowding_bucket
          FROM read_parquet('{src_s}') s
          LEFT JOIN limit_roll l ON s.code = l.code AND s.date = l.date
          LEFT JOIN ind_crowd2 c
            ON s.industry_code = c.industry_code AND s.date = c.date
        ) TO '{out_s}' (FORMAT PARQUET)
        """
    )
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out_s}')").fetchone()[0]
    sample = con.execute(
        f"""
        SELECT
          avg(ind_crowd_pct) avg_ic,
          avg(CASE WHEN ind_crowd_bucket='high' THEN 1.0 ELSE 0.0 END) high_share,
          avg(CASE WHEN earnings_blackout THEN 1.0 ELSE 0.0 END) earn_share,
          avg(limit_up_5) avg_lu
        FROM read_parquet('{out_s}')
        WHERE date >= DATE '2020-01-01'
        """
    ).fetchone()
    print(f"done rows={n:,} -> {out}")
    print(
        f"  ind_crowd avg={sample[0]:.3f} high_share={sample[1]:.3f} "
        f"earn_blackout={sample[2]:.4f} avg_limit_up_5={sample[3]:.3f}"
    )
    con.close()


if __name__ == "__main__":
    main()
