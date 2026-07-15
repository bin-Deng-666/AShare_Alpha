"""Build v2.7 panel: exact earnings/event blackout + HS300/ZZ500 membership tiers.

Starts from v2.6 cache (limit-up crowding already baked in).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
CATALOG = REPO / "a_share_quant_data" / "catalog" / "quant.duckdb"
SRC_DEFAULT = ROOT / "cache" / "signals_2020-01-01_v26.parquet"
OUT_DEFAULT = ROOT / "cache" / "signals_2020-01-01_v27.parquet"


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC_DEFAULT
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT_DEFAULT
    if not src.exists():
        alt = ROOT / "cache" / "signals_2020-01-01_sota.parquet"
        if not alt.exists():
            raise SystemExit(f"missing {src}")
        src = alt
        print(f"warn: v26 missing, using {src}")
    if not CATALOG.exists():
        raise SystemExit(f"missing catalog {CATALOG}")

    src_s = str(src).replace("'", "''")
    out_s = str(out).replace("'", "''")
    cat_s = str(CATALOG).replace("'", "''")
    print(f"building {out} from {src} …")

    con = duckdb.connect()
    con.execute(f"ATTACH '{cat_s}' AS quant (READ_ONLY)")

    # Exact earnings announce dates only (avoid statutory_deadline false blackouts)
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE earn_exact AS
        SELECT DISTINCT code, available_date AS earn_date
        FROM quant.financial_availability
        WHERE available_date IS NOT NULL
          AND available_date >= DATE '2019-01-01'
          AND availability_quality = 'exact'
        """
    )
    # Corporate-action notice days (高送转/分红预案) as soft event calendar
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE corp_notice AS
        SELECT DISTINCT
          code,
          coalesce(notice_date, plan_notice_date, publish_date) AS notice_date
        FROM quant.corporate_actions
        WHERE coalesce(notice_date, plan_notice_date, publish_date) IS NOT NULL
          AND coalesce(notice_date, plan_notice_date, publish_date) >= DATE '2019-01-01'
          AND (
            coalesce(bonus_share_per_10, 0) > 0
            OR coalesce(transfer_share_per_10, 0) > 0
            OR coalesce(cash_dividend_per_10, 0) >= 5.0
          )
        """
    )
    # PIT index membership (snapshot_derived intervals)
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE idx_hs AS
        SELECT code, valid_from::DATE AS valid_from,
               coalesce(valid_to::DATE, DATE '2099-12-31') AS valid_to
        FROM quant.index_membership
        WHERE index_code = '000300'
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE idx_zz AS
        SELECT code, valid_from::DATE AS valid_from,
               coalesce(valid_to::DATE, DATE '2099-12-31') AS valid_to
        FROM quant.index_membership
        WHERE index_code = '000905'
        """
    )

    con.execute(
        f"""
        COPY (
          SELECT
            s.* EXCLUDE (earnings_blackout),
            EXISTS (
              SELECT 1 FROM earn_exact e
              WHERE e.code = s.code
                AND e.earn_date BETWEEN s.date - INTERVAL 1 DAY AND s.date + INTERVAL 1 DAY
            ) AS earnings_blackout,
            (
              EXISTS (
                SELECT 1 FROM earn_exact e
                WHERE e.code = s.code
                  AND e.earn_date BETWEEN s.date - INTERVAL 1 DAY AND s.date + INTERVAL 1 DAY
              )
              OR EXISTS (
                SELECT 1 FROM corp_notice c
                WHERE c.code = s.code
                  AND c.notice_date BETWEEN s.date - INTERVAL 1 DAY AND s.date + INTERVAL 1 DAY
              )
            ) AS event_blackout,
            EXISTS (
              SELECT 1 FROM idx_hs h
              WHERE h.code = s.code
                AND s.date >= h.valid_from AND s.date <= h.valid_to
            ) AS is_hs300,
            EXISTS (
              SELECT 1 FROM idx_zz z
              WHERE z.code = s.code
                AND s.date >= z.valid_from AND s.date <= z.valid_to
            ) AS is_zz500,
            CASE
              WHEN EXISTS (
                SELECT 1 FROM idx_hs h
                WHERE h.code = s.code AND s.date >= h.valid_from AND s.date <= h.valid_to
              ) THEN 'hs300'
              WHEN EXISTS (
                SELECT 1 FROM idx_zz z
                WHERE z.code = s.code AND s.date >= z.valid_from AND s.date <= z.valid_to
              ) THEN 'zz500'
              ELSE 'other'
            END AS index_tier
          FROM read_parquet('{src_s}') s
        ) TO '{out_s}' (FORMAT PARQUET)
        """
    )
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out_s}')").fetchone()[0]
    sample = con.execute(
        f"""
        SELECT
          avg(CASE WHEN earnings_blackout THEN 1.0 ELSE 0.0 END) earn_share,
          avg(CASE WHEN event_blackout THEN 1.0 ELSE 0.0 END) event_share,
          avg(CASE WHEN is_hs300 THEN 1.0 ELSE 0.0 END) hs_share,
          avg(CASE WHEN is_zz500 THEN 1.0 ELSE 0.0 END) zz_share,
          avg(CASE WHEN action='open_A' AND is_hs300 THEN 1.0 ELSE 0.0 END)
            FILTER (WHERE action='open_A') hs_among_open
        FROM read_parquet('{out_s}')
        WHERE date >= DATE '2020-01-01'
        """
    ).fetchone()
    print(f"done rows={n:,} -> {out}")
    print(
        f"  earn_bo={sample[0]:.4f} event_bo={sample[1]:.4f} "
        f"hs300={sample[2]:.3f} zz500={sample[3]:.3f} hs_among_openA={sample[4]:.3f}"
    )
    con.close()


if __name__ == "__main__":
    main()
