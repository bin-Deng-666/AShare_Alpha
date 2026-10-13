"""research — catalog: data acquisition and normalization."""
from __future__ import annotations
import datetime as dt
import logging
from pathlib import Path
from typing import Dict, List, Tuple
import duckdb
from data.ashare_data.paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE, CATALOG_PATH as CATALOG

from data.ashare_data.storage import atomic_json

def _catalog_daily_view_sql(adjustment: str) -> str:
    """Return the stable logical daily-table query for one price adjustment."""
    bars_dir = PARQUET / "daily_bars" / f"adjustment={adjustment}"
    indicators_dir = PARQUET / "daily_indicators"
    if not list(bars_dir.glob("*.parquet")):
        raise FileNotFoundError(f"daily bars missing under {bars_dir}")
    if not list(indicators_dir.glob("*.parquet")):
        raise FileNotFoundError(f"daily indicators missing under {indicators_dir}")

    bars_glob = (bars_dir / "*.parquet").as_posix()
    indicators_glob = (indicators_dir / "*.parquet").as_posix()
    joins = [
        f"""
        LEFT JOIN read_parquet('{indicators_glob}', union_by_name=true) i
          ON CAST(b.code AS VARCHAR) = CAST(i.code AS VARCHAR)
         AND CAST(b.date AS DATE) = CAST(i.date AS DATE)
        """
    ]

    shares_dir = PARQUET / "shares_daily"
    if list(shares_dir.glob("*.parquet")):
        shares_glob = (shares_dir / "*.parquet").as_posix()
        joins.append(
            f"""
            LEFT JOIN read_parquet('{shares_glob}', union_by_name=true) s
              ON CAST(b.code AS VARCHAR) = CAST(s.code AS VARCHAR)
             AND CAST(b.date AS DATE) = CAST(s.date AS DATE)
            """
        )
        shares_columns = """
          CAST(s.total_shares AS BIGINT) AS total_shares,
          CAST(s.float_shares AS BIGINT) AS float_shares,
          CAST(s.total_market_cap AS DOUBLE) AS total_market_cap,
          CAST(s.float_market_cap AS DOUBLE) AS float_market_cap,
        """
    else:
        shares_columns = """
          NULL::BIGINT AS total_shares,
          NULL::BIGINT AS float_shares,
          NULL::DOUBLE AS total_market_cap,
          NULL::DOUBLE AS float_market_cap,
        """

    limits_dir = PARQUET / "limit_prices"
    if list(limits_dir.glob("*.parquet")):
        limits_glob = (limits_dir / "*.parquet").as_posix()
        joins.append(
            f"""
            LEFT JOIN read_parquet('{limits_glob}', union_by_name=true) l
              ON CAST(b.code AS VARCHAR) = CAST(l.code AS VARCHAR)
             AND CAST(b.date AS DATE) = CAST(l.date AS DATE)
            """
        )
        limit_columns = """
          CAST(l.limit_up AS DOUBLE) AS limit_up,
          CAST(l.limit_down AS DOUBLE) AS limit_down,
          CAST(l.limit_pct AS DOUBLE) AS limit_pct,
          CAST(l.is_limit_up AS BOOLEAN) AS is_limit_up,
          CAST(l.is_limit_down AS BOOLEAN) AS is_limit_down,
          CAST(l.reference_price AS DOUBLE) AS limit_reference_price,
          CAST(l.reference_price_source AS VARCHAR) AS limit_reference_source,
          CAST(l.limit_validation_status AS VARCHAR) AS limit_validation_status,
          CAST(l.cash_dividend_per_share AS DOUBLE) AS cash_dividend_per_share,
          CAST(l.share_distribution_ratio AS DOUBLE) AS share_distribution_ratio,
          CAST(l.rights_issue_ratio AS DOUBLE) AS rights_issue_ratio,
          CAST(l.rights_issue_price AS DOUBLE) AS rights_issue_price,
          CAST(l.corporate_action_adjusted AS BOOLEAN) AS corporate_action_adjusted,
          CAST(l.corporate_action_formula_status AS VARCHAR)
              AS corporate_action_formula_status,
        """
    else:
        limit_columns = """
          NULL::DOUBLE AS limit_up,
          NULL::DOUBLE AS limit_down,
          NULL::DOUBLE AS limit_pct,
          NULL::BOOLEAN AS is_limit_up,
          NULL::BOOLEAN AS is_limit_down,
          NULL::DOUBLE AS limit_reference_price,
          NULL::VARCHAR AS limit_reference_source,
          NULL::VARCHAR AS limit_validation_status,
          NULL::DOUBLE AS cash_dividend_per_share,
          NULL::DOUBLE AS share_distribution_ratio,
          NULL::DOUBLE AS rights_issue_ratio,
          NULL::DOUBLE AS rights_issue_price,
          NULL::BOOLEAN AS corporate_action_adjusted,
          NULL::VARCHAR AS corporate_action_formula_status,
        """

    return f"""
      SELECT
        LPAD(CAST(b.code AS VARCHAR), 6, '0') AS code,
        CAST(b.date AS DATE) AS date,
        CAST(b.exchange AS VARCHAR) AS exchange,
        CAST(b.open AS DOUBLE) AS open,
        CAST(b.high AS DOUBLE) AS high,
        CAST(b.low AS DOUBLE) AS low,
        CAST(b.close AS DOUBLE) AS close,
        CAST(b.volume AS DOUBLE) AS volume,
        CAST(b.amount AS DOUBLE) AS amount,
        CAST(i.turn AS DOUBLE) AS turnover_pct,
        CAST(i.tradestatus AS BOOLEAN) AS trade_status,
        CAST(i.pctChg AS DOUBLE) AS pct_change,
        CAST(i.peTTM AS DOUBLE) AS pe_ttm,
        CAST(i.pbMRQ AS DOUBLE) AS pb_mrq,
        CAST(i.psTTM AS DOUBLE) AS ps_ttm,
        CAST(i.pcfNcfTTM AS DOUBLE) AS pcf_ncf_ttm,
        CAST(i.isST AS BOOLEAN) AS is_st,
        {shares_columns}
        {limit_columns}
        CAST(b.source AS VARCHAR) AS bar_source,
        CAST(i.source AS VARCHAR) AS indicator_source
      FROM read_parquet('{bars_glob}', union_by_name=true) b
      {' '.join(joins)}
    """


def _normalize_index_code_sql(column: str) -> str:
    """Return one catalog expression for prefixed or suffixed index codes."""
    return (
        "REGEXP_REPLACE(REGEXP_REPLACE(CAST("
        + column
        + " AS VARCHAR), '^(sh|sz)\\.', ''), '\\..*$', '')"
    )


def _catalog_index_view_sql() -> str:
    """Return the stable broad-based plus industry index daily-bar view."""
    selects: List[str] = []
    broad_files = list((PARQUET / "reference" / "index_bars").glob("*.parquet"))
    if broad_files:
        broad_glob = (
            PARQUET / "reference" / "index_bars" / "*.parquet"
        ).as_posix()
        selects.append(
            f"""
            SELECT
              index_code,
              date,
              CASE index_code
                WHEN '000300' THEN '沪深300'
                WHEN '000905' THEN '中证500'
                WHEN '000852' THEN '中证1000'
                WHEN '000001' THEN '上证综指'
                WHEN '399001' THEN '深证成指'
                WHEN '399006' THEN '创业板指'
                ELSE index_code
              END::VARCHAR AS index_name,
              'broad_based'::VARCHAR AS index_type,
              open, high, low, close, preclose, volume, amount, pct_change, source
            FROM (
              SELECT
                {_normalize_index_code_sql("code")} AS index_code,
                CAST(date AS DATE) AS date,
                CAST(open AS DOUBLE) AS open,
                CAST(high AS DOUBLE) AS high,
                CAST(low AS DOUBLE) AS low,
                CAST(close AS DOUBLE) AS close,
                CAST(preclose AS DOUBLE) AS preclose,
                CAST(volume AS DOUBLE) AS volume,
                CAST(amount AS DOUBLE) AS amount,
                CAST(pctChg AS DOUBLE) AS pct_change,
                CAST(source AS VARCHAR) AS source
              FROM read_parquet('{broad_glob}', union_by_name=true)
            ) broad
            """
        )

    if selects:
        return " UNION ALL ".join(selects)
    return """
      SELECT NULL::VARCHAR AS index_code, NULL::DATE AS date,
        NULL::VARCHAR AS index_name, NULL::VARCHAR AS index_type,
        NULL::DOUBLE AS open, NULL::DOUBLE AS high, NULL::DOUBLE AS low,
        NULL::DOUBLE AS close, NULL::DOUBLE AS preclose,
        NULL::DOUBLE AS volume, NULL::DOUBLE AS amount,
        NULL::DOUBLE AS pct_change, NULL::VARCHAR AS source
      WHERE FALSE
    """


def _assert_unique(con, view_name: str, key_columns: Tuple[str, ...]) -> None:
    """Fail catalog construction when a logical primary key is not unique."""
    keys = ", ".join(key_columns)
    null_predicate = " OR ".join(f"{column} IS NULL" for column in key_columns)
    if con.execute(
        f"SELECT COUNT(*) FROM {view_name} WHERE {null_predicate}"
    ).fetchone()[0]:
        raise RuntimeError(f"{view_name} contains NULL logical key: {keys}")
    duplicate = con.execute(
        f"SELECT {keys}, COUNT(*) AS n FROM {view_name} "
        f"GROUP BY {keys} HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    if duplicate is not None:
        raise RuntimeError(f"{view_name} has duplicate logical key: {keys}: {duplicate}")




def build_catalog(config: Dict) -> None:
    """Publish query views over canonical Parquet, without duplicated storage."""
    del config
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    import os
    import uuid
    pending = CATALOG.with_name(f'.{CATALOG.name}.{uuid.uuid4().hex}.tmp')
    con = duckdb.connect(str(pending))
    published = False
    try:
        con.execute(
            "CREATE OR REPLACE VIEW stock_daily AS "
            + _catalog_daily_view_sql("qfq")
        )
        con.execute(
            "CREATE OR REPLACE VIEW stock_daily_raw AS "
            + _catalog_daily_view_sql("raw")
        )


        master = (PARQUET / "security_master" / "security_master.parquet").as_posix()
        con.execute(
            f"""
            CREATE OR REPLACE VIEW security_master AS
            SELECT
              CAST(code AS VARCHAR) AS code,
              CAST(name AS VARCHAR) AS name,
              CAST(exchange AS VARCHAR) AS exchange,
              CAST(status AS VARCHAR) AS status,
              CAST(list_date AS DATE) AS list_date,
              CAST(delist_date AS DATE) AS delist_date,
              CAST(org_id AS VARCHAR) AS org_id,
              CAST(cninfo_name AS VARCHAR) AS cninfo_name,
              CAST(source AS VARCHAR) AS source
            FROM read_parquet('{master}')
            """
        )

        cal = (PARQUET / "reference" / "trade_calendar.parquet").as_posix()
        con.execute(
            f"""
            CREATE OR REPLACE VIEW trade_calendar AS
            SELECT
              CAST(calendar_date AS DATE) AS calendar_date,
              CAST(is_trading_day AS BOOLEAN) AS is_trading_day
            FROM read_parquet('{cal}')
            """
        )

        con.execute(
            "CREATE OR REPLACE VIEW index_daily_bars AS "
            + _catalog_index_view_sql()
        )

        avail_path = PARQUET / "reference" / "financial_availability.parquet"
        if avail_path.exists():
            avail = avail_path.as_posix()
            con.execute(
                f"""
                CREATE OR REPLACE VIEW financial_availability AS
                SELECT
                  CAST(code AS VARCHAR) AS code,
                  CAST(report_date AS DATE) AS report_date,
                  CAST(first_announcement_date AS DATE) AS first_announcement_date,
                  CAST(latest_announcement_date AS DATE) AS latest_announcement_date,
                  CAST(availability_anchor_date AS DATE) AS availability_anchor_date,
                  CAST(availability_quality AS VARCHAR) AS availability_quality,
                  CAST(matched_announcement_count AS BIGINT) AS matched_announcement_count,
                  CAST(matched_title AS VARCHAR) AS matched_title,
                  CAST(available_date AS DATE) AS available_date
                FROM read_parquet('{avail}')
                """
            )
        else:
            con.execute(
                """
                CREATE OR REPLACE VIEW financial_availability AS
                SELECT NULL::VARCHAR AS code, NULL::DATE AS report_date,
                  NULL::DATE AS first_announcement_date,
                  NULL::DATE AS latest_announcement_date,
                  NULL::DATE AS availability_anchor_date,
                  NULL::VARCHAR AS availability_quality,
                  NULL::BIGINT AS matched_announcement_count,
                  NULL::VARCHAR AS matched_title, NULL::DATE AS available_date
                WHERE FALSE
                """
            )

        financial_dir = PARQUET / "financials"
        if list(financial_dir.glob("*.parquet")):
            fin_glob = financial_dir.as_posix() + "/*.parquet"
            con.execute(
                f"""
                CREATE OR REPLACE VIEW financial_statements AS
                SELECT
                  CAST(f.code AS VARCHAR) AS code,
                  CAST(f.report_date AS DATE) AS report_date,
                  CASE f.statement_type
                    WHEN 'fzb' THEN 'balance_sheet'
                    WHEN 'lrb' THEN 'income_statement'
                    WHEN 'llb' THEN 'cash_flow'
                    ELSE CAST(f.statement_type AS VARCHAR)
                  END AS statement_type,
                  CAST(f.item_code AS VARCHAR) AS item_code,
                  CAST(f.item AS VARCHAR) AS item,
                  CAST(f.item_display AS VARCHAR) AS item_display,
                  CAST(f.item_group_no AS DOUBLE) AS item_group_no,
                  CAST(f.value AS DOUBLE) AS value,
                  CAST(f.value_raw AS VARCHAR) AS value_raw,
                  CAST(f.year_on_year AS DOUBLE) AS year_on_year,
                  a.available_date,
                  a.availability_quality,
                  CAST(f.source AS VARCHAR) AS source
                FROM read_parquet('{fin_glob}', union_by_name=true) f
                LEFT JOIN financial_availability a
                  ON CAST(f.code AS VARCHAR) = a.code
                 AND CAST(f.report_date AS DATE) = a.report_date
                """
            )
        else:
            con.execute(
                """
                CREATE OR REPLACE VIEW financial_statements AS
                SELECT NULL::VARCHAR AS code, NULL::DATE AS report_date,
                  NULL::VARCHAR AS statement_type, NULL::VARCHAR AS item_code,
                  NULL::VARCHAR AS item, NULL::VARCHAR AS item_display,
                  NULL::DOUBLE AS item_group_no, NULL::DOUBLE AS value,
                  NULL::VARCHAR AS value_raw, NULL::DOUBLE AS year_on_year,
                  NULL::DATE AS available_date,
                  NULL::VARCHAR AS availability_quality,
                  NULL::VARCHAR AS source
                WHERE FALSE
                """
            )

        con.execute(
            """
            CREATE OR REPLACE VIEW financial_pit AS
            SELECT *
            FROM financial_statements
            WHERE available_date IS NOT NULL
              AND availability_quality NOT IN ('unmatched', 'out_of_scope')
            """
        )

        ind_iv = (
            PARQUET / "reference" / "industry_membership_intervals.parquet"
        ).as_posix()
        con.execute(
            f"""
            CREATE OR REPLACE VIEW industry_membership AS
            SELECT
              CAST(code AS VARCHAR) AS code,
              CAST(classification_system AS VARCHAR) AS classification_system,
              CAST(industry_level AS VARCHAR) AS industry_level,
              CAST(industry_code AS VARCHAR) AS industry_code,
              CAST(industry_name AS VARCHAR) AS industry_name,
              CAST(valid_from AS DATE) AS valid_from,
              CAST(valid_to AS DATE) AS valid_to,
              CAST(interval_quality AS VARCHAR) AS interval_quality,
              CAST(source AS VARCHAR) AS source
            FROM read_parquet('{ind_iv}')
            """
        )

        idx_iv = (
            PARQUET / "reference" / "index_constituent_intervals.parquet"
        ).as_posix()
        con.execute(
            f"""
            CREATE OR REPLACE VIEW index_membership AS
            SELECT *
            FROM read_parquet('{idx_iv}')
            """
        )

        nb_path = PARQUET / "reference" / "northbound_market_daily.parquet"
        if nb_path.exists():
            nb = nb_path.as_posix()
            con.execute(
                f"""
                CREATE OR REPLACE VIEW northbound_market_daily AS
                SELECT
                  CAST(date AS DATE) AS date,
                  CAST(north_net_yi AS DOUBLE) AS north_net_yi,
                  CAST(north_buy_yi AS DOUBLE) AS north_buy_yi,
                  CAST(north_sell_yi AS DOUBLE) AS north_sell_yi,
                  CAST(hgt_net_yi AS DOUBLE) AS hgt_net_yi,
                  CAST(sgt_net_yi AS DOUBLE) AS sgt_net_yi,
                  CAST(south_net_yi AS DOUBLE) AS south_net_yi,
                  CAST(north_hold_mcap AS DOUBLE) AS north_hold_mcap,
                  CAST(source AS VARCHAR) AS source
                FROM read_parquet('{nb}')
                """
            )

        mg_path = PARQUET / "reference" / "margin_market_daily.parquet"
        if mg_path.exists():
            mg = mg_path.as_posix()
            con.execute(
                f"""
                CREATE OR REPLACE VIEW margin_market_daily AS
                SELECT
                  CAST(date AS DATE) AS date,
                  CAST(rzye AS DOUBLE) AS rzye,
                  CAST(rzmre AS DOUBLE) AS rzmre,
                  CAST(rqye AS DOUBLE) AS rqye,
                  CAST(rqyl AS DOUBLE) AS rqyl,
                  CAST(rqmcl AS DOUBLE) AS rqmcl,
                  CAST(rzrqye AS DOUBLE) AS rzrqye,
                  CAST(market AS VARCHAR) AS market,
                  CAST(source AS VARCHAR) AS source
                FROM read_parquet('{mg}')
                """
            )

        # Corporate actions with stable action_id.
        corporate_dir = PARQUET / "corporate_actions"
        if list(corporate_dir.glob("*.parquet")):
            ca_glob = corporate_dir.as_posix() + "/*.parquet"
            con.execute(
                f"""
                CREATE OR REPLACE VIEW corporate_actions AS
                SELECT
                  CAST(code AS VARCHAR) AS code,
                  CAST(name AS VARCHAR) AS name,
                  CAST(report_date AS DATE) AS report_date,
                  CAST(plan_notice_date AS DATE) AS plan_notice_date,
                  CAST(publish_date AS DATE) AS publish_date,
                  CAST(notice_date AS DATE) AS notice_date,
                  CAST(record_date AS DATE) AS record_date,
                  CAST(ex_date AS DATE) AS ex_date,
                  CAST(cash_dividend_per_10 AS DOUBLE) AS cash_dividend_per_10,
                  CAST(bonus_share_per_10 AS DOUBLE) AS bonus_share_per_10,
                  CAST(transfer_share_per_10 AS DOUBLE) AS transfer_share_per_10,
                  CAST(bonus_transfer_per_10 AS DOUBLE) AS bonus_transfer_per_10,
                  CAST(total_shares AS BIGINT) AS total_shares,
                  CAST(progress AS VARCHAR) AS progress,
                  CAST(plan_description AS VARCHAR) AS plan_description,
                  CAST(source AS VARCHAR) AS source
                FROM read_parquet('{ca_glob}', union_by_name=true)
                """
            )
        else:
            con.execute(
                """
                CREATE OR REPLACE VIEW corporate_actions AS
                SELECT NULL::VARCHAR AS code, NULL::VARCHAR AS name,
                  NULL::DATE AS report_date, NULL::DATE AS plan_notice_date,
                  NULL::DATE AS publish_date, NULL::DATE AS notice_date,
                  NULL::DATE AS record_date, NULL::DATE AS ex_date,
                  NULL::DOUBLE AS cash_dividend_per_10,
                  NULL::DOUBLE AS bonus_share_per_10,
                  NULL::DOUBLE AS transfer_share_per_10,
                  NULL::DOUBLE AS bonus_transfer_per_10,
                  NULL::BIGINT AS total_shares, NULL::VARCHAR AS progress,
                  NULL::VARCHAR AS plan_description, NULL::VARCHAR AS source
                WHERE FALSE
                """
            )

        _assert_unique(con, "stock_daily", ("code", "date"))
        _assert_unique(con, "stock_daily_raw", ("code", "date"))
        _assert_unique(con, "index_daily_bars", ("index_code", "date"))
        for name, relative in (
            ('lhb_detail', 'reference/lhb_detail/lhb_detail.parquet'),
            ('block_trade', 'reference/block_trade/block_trade.parquet'),
            ('holder_change', 'reference/holder_change/holder_change.parquet'),
            ('cash_yield_daily', 'reference/cash_yield/gc001.parquet'),
        ):
            path = PARQUET / relative
            if path.exists():
                escaped = path.as_posix().replace("'", "''")
                con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{escaped}')")
        con.execute('CHECKPOINT')
        con.close()
        os.replace(pending, CATALOG)
        published = True

        con = duckdb.connect(str(CATALOG), read_only=True)
        views = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        # DuckDB lists views via SHOW TABLES in recent versions; also try:
        try:
            views = [r[0] for r in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()]
        except Exception:  # noqa: BLE001
            pass
        atomic_json(
            {
                "catalog": str(CATALOG),
                "views": views,
                "updated_at": dt.datetime.now(),
            },
            STATE / "catalog_coverage.json",
        )
        logging.info("catalog ready at %s views=%s", CATALOG, views)
    finally:
        con.close()
        if not published:
            pending.unlink(missing_ok=True)
            Path(str(pending) + '.wal').unlink(missing_ok=True)
