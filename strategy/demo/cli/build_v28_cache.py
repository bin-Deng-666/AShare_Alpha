"""Enrichment stage → live **v1.0** panel (northbound + margin soft-flow on prior stage).

Script name is historical (old panel stage id). Public artifact:
`cache/signals_2020-01-01_v1.0.parquet`.

PIT: flow columns are as-of signal date (EOD); portfolio executes next open.
Northbound NET is NaN after ~2024-08-19 (upstream); flags stay False when null.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
CATALOG = REPO / "a_share_quant_data" / "catalog" / "quant.duckdb"
SRC_DEFAULT = ROOT / "cache" / "signals_2020-01-01_v27.parquet"
OUT_DEFAULT = ROOT / "cache" / "signals_2020-01-01_v1.0.parquet"


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC_DEFAULT
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT_DEFAULT
    if not src.exists():
        raise SystemExit(f"missing {src}")
    if not CATALOG.exists():
        raise SystemExit(f"missing catalog {CATALOG}")

    src_s = str(src).replace("'", "''")
    out_s = str(out).replace("'", "''")
    cat_s = str(CATALOG).replace("'", "''")
    print(f"building {out} from {src} …")

    con = duckdb.connect()
    con.execute(f"ATTACH '{cat_s}' AS quant (READ_ONLY)")

    # Market flow feature table (one row per date)
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE flow_feat AS
        WITH nb AS (
          SELECT
            date::DATE AS date,
            north_net_yi,
            hgt_net_yi,
            sgt_net_yi,
            south_net_yi
          FROM quant.northbound_market_daily
        ),
        mg AS (
          SELECT
            date::DATE AS date,
            rzye,
            rzmre,
            rzrqye
          FROM quant.margin_market_daily
        ),
        joined AS (
          SELECT
            coalesce(nb.date, mg.date) AS date,
            nb.north_net_yi,
            nb.hgt_net_yi,
            nb.sgt_net_yi,
            nb.south_net_yi,
            mg.rzye AS margin_rzye,
            mg.rzmre AS margin_rzmre,
            mg.rzrqye AS margin_rzrqye
          FROM nb
          FULL OUTER JOIN mg ON nb.date = mg.date
        ),
        base AS (
          SELECT
            date,
            north_net_yi,
            hgt_net_yi,
            sgt_net_yi,
            south_net_yi,
            margin_rzye,
            margin_rzmre,
            margin_rzrqye,
            sum(north_net_yi) OVER (
              ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
            ) AS north_net_5,
            avg(north_net_yi) OVER (
              ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
            ) AS north_mean_60,
            stddev_samp(north_net_yi) OVER (
              ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
            ) AS north_std_60,
            margin_rzye / nullif(
              lag(margin_rzye, 5) OVER (ORDER BY date), 0
            ) - 1.0 AS margin_chg_5,
            margin_rzye / nullif(
              lag(margin_rzye, 20) OVER (ORDER BY date), 0
            ) - 1.0 AS margin_chg_20
          FROM joined
        ),
        calc AS (
          SELECT
            *,
            avg(margin_chg_5) OVER (
              ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
            ) AS margin_chg5_mean_60,
            stddev_samp(margin_chg_5) OVER (
              ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
            ) AS margin_chg5_std_60
          FROM base
        )
        SELECT
          date,
          north_net_yi,
          north_net_5,
          CASE
            WHEN north_net_yi IS NULL OR north_std_60 IS NULL OR north_std_60 < 1e-9
              THEN NULL
            ELSE (north_net_yi - north_mean_60) / north_std_60
          END AS north_z_60,
          hgt_net_yi,
          sgt_net_yi,
          south_net_yi,
          margin_rzye,
          margin_rzmre,
          margin_rzrqye,
          margin_chg_5,
          margin_chg_20,
          CASE
            WHEN margin_chg_5 IS NULL OR margin_chg5_std_60 IS NULL
                 OR margin_chg5_std_60 < 1e-9
              THEN NULL
            ELSE (margin_chg_5 - margin_chg5_mean_60) / margin_chg5_std_60
          END AS margin_z_60,
          CASE
            WHEN north_net_yi IS NULL THEN FALSE
            WHEN north_std_60 IS NOT NULL AND north_std_60 >= 1e-9
                 AND (north_net_yi - north_mean_60) / north_std_60 <= -1.0
              THEN TRUE
            WHEN north_net_5 IS NOT NULL AND north_net_5 <= -50.0 THEN TRUE
            ELSE FALSE
          END AS nb_weak,
          CASE
            WHEN north_net_yi IS NULL THEN FALSE
            WHEN north_std_60 IS NOT NULL AND north_std_60 >= 1e-9
                 AND (north_net_yi - north_mean_60) / north_std_60 >= 1.0
              THEN TRUE
            WHEN north_net_5 IS NOT NULL AND north_net_5 >= 80.0 THEN TRUE
            ELSE FALSE
          END AS nb_strong,
          CASE
            WHEN margin_chg_5 IS NULL THEN FALSE
            WHEN margin_chg5_std_60 IS NOT NULL AND margin_chg5_std_60 >= 1e-9
                 AND (margin_chg_5 - margin_chg5_mean_60) / margin_chg5_std_60 >= 1.25
              THEN TRUE
            WHEN margin_chg_5 >= 0.03 THEN TRUE
            ELSE FALSE
          END AS mg_stress
        FROM calc
        """
    )

    con.execute(
        f"""
        COPY (
          SELECT
            s.*,
            f.north_net_yi,
            f.north_net_5,
            f.north_z_60,
            f.hgt_net_yi,
            f.sgt_net_yi,
            f.south_net_yi,
            f.margin_rzye,
            f.margin_rzmre,
            f.margin_rzrqye,
            f.margin_chg_5,
            f.margin_chg_20,
            f.margin_z_60,
            coalesce(f.nb_weak, FALSE) AS nb_weak,
            coalesce(f.nb_strong, FALSE) AS nb_strong,
            coalesce(f.mg_stress, FALSE) AS mg_stress
          FROM read_parquet('{src_s}') s
          LEFT JOIN flow_feat f ON s.date = f.date
        ) TO '{out_s}' (FORMAT PARQUET)
        """
    )
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out_s}')").fetchone()[0]
    sample = con.execute(
        f"""
        SELECT
          avg(CASE WHEN nb_weak THEN 1.0 ELSE 0.0 END) weak_share,
          avg(CASE WHEN nb_strong THEN 1.0 ELSE 0.0 END) strong_share,
          avg(CASE WHEN mg_stress THEN 1.0 ELSE 0.0 END) stress_share,
          avg(CASE WHEN north_net_yi IS NULL THEN 1.0 ELSE 0.0 END) nb_null_share,
          avg(CASE WHEN action='open_A' AND nb_weak THEN 1.0 ELSE 0.0 END)
            FILTER (WHERE action='open_A') open_weak,
          avg(CASE WHEN action='open_A' AND mg_stress THEN 1.0 ELSE 0.0 END)
            FILTER (WHERE action='open_A') open_stress
        FROM read_parquet('{out_s}')
        WHERE date >= DATE '2020-01-01'
        """
    ).fetchone()
    print(f"done rows={n:,} -> {out}")
    print(
        f"  nb_weak={sample[0]:.3f} nb_strong={sample[1]:.3f} mg_stress={sample[2]:.3f} "
        f"nb_null={sample[3]:.3f} openA_weak={sample[4]:.3f} openA_stress={sample[5]:.3f}"
    )
    con.close()


if __name__ == "__main__":
    main()
