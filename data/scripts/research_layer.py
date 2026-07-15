#!/usr/bin/env python3
"""Research-layer builders: availability polish, warehouse, DuckDB catalog."""
from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

try:
    import duckdb
except ImportError:  # pragma: no cover
    duckdb = None

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "parquet"
WAREHOUSE = ROOT / "warehouse"
CATALOG = ROOT / "catalog" / "quant.duckdb"
STATE = ROOT / "state"


def atomic_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(temp, index=False)
    temp.replace(path)


def atomic_json(data: Dict, path: Path) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temp.replace(path)


def report_date_from_announcement_title(title: str) -> Optional[pd.Timestamp]:
    """Parse a periodic-report end date from a CNInfo announcement title."""
    text = re.sub(r"\s+", "", str(title))
    year_match = re.search(r"((?:19|20)\d{2})年", text)
    if not year_match:
        return None
    year = int(year_match.group(1))
    if re.search(r"(?:半年度报告|半年报|中期报告|中报)", text):
        return pd.Timestamp(year=year, month=6, day=30)
    if re.search(r"(?:第一季度|一季度|第1季度|一季)报告|一季报", text):
        return pd.Timestamp(year=year, month=3, day=31)
    if re.search(r"(?:第三季度|三季度|第3季度|三季)报告|三季报", text):
        return pd.Timestamp(year=year, month=9, day=30)
    if re.search(r"(?:第四季度|四季度|第4季度)报告|四季报", text):
        return pd.Timestamp(year=year, month=12, day=31)
    if re.search(r"(?:年度报告|年报)", text) and "半年度" not in text and "中期" not in text:
        return pd.Timestamp(year=year, month=12, day=31)
    return None


def statutory_deadline(report_date: pd.Timestamp) -> Optional[pd.Timestamp]:
    """China periodic-report statutory filing deadlines (calendar day)."""
    report_date = pd.Timestamp(report_date).normalize()
    month = int(report_date.month)
    year = int(report_date.year)
    if month == 12:
        return pd.Timestamp(year=year + 1, month=4, day=30)
    if month == 6:
        return pd.Timestamp(year=year, month=8, day=31)
    if month == 3:
        return pd.Timestamp(year=year, month=4, day=30)
    if month == 9:
        return pd.Timestamp(year=year, month=10, day=31)
    return None


def next_trading_day(
    trading_days: Sequence[pd.Timestamp],
    anchor: pd.Timestamp,
) -> Optional[pd.Timestamp]:
    from bisect import bisect_right

    if not trading_days or pd.isna(anchor):
        return None
    position = bisect_right(trading_days, pd.Timestamp(anchor).to_datetime64())
    if position >= len(trading_days):
        return None
    return pd.Timestamp(trading_days[position])


def rebuild_financial_availability(config: Dict) -> None:
    """Rebuild availability with broader title parse + statutory fallback."""
    calendar = pd.read_parquet(PARQUET / "reference" / "trade_calendar.parquet")
    trading_days = sorted(
        pd.to_datetime(
            calendar.loc[calendar["is_trading_day"].eq(1), "calendar_date"]
        ).dt.normalize().dropna().unique()
    )

    financial_rows = []
    for path in sorted((PARQUET / "financials").glob("*.parquet")):
        dates = pd.read_parquet(path, columns=["report_date"])["report_date"]
        for report_date in pd.to_datetime(dates, errors="coerce").dropna().unique():
            financial_rows.append(
                {"code": path.stem, "report_date": pd.Timestamp(report_date)}
            )
    financial_periods = pd.DataFrame(financial_rows).drop_duplicates(
        ["code", "report_date"]
    )

    announcement_rows = []
    for path in sorted((PARQUET / "announcements").glob("*.parquet")):
        frame = pd.read_parquet(
            path, columns=["announcement_id", "announcement_date", "title"]
        )
        if frame.empty:
            continue
        frame["report_date"] = frame["title"].map(report_date_from_announcement_title)
        frame = frame.dropna(subset=["report_date", "announcement_date"])
        if frame.empty:
            continue
        frame["code"] = path.stem
        frame["announcement_date"] = pd.to_datetime(
            frame["announcement_date"], errors="coerce"
        ).dt.normalize()
        announcement_rows.append(frame)
    announcements = (
        pd.concat(announcement_rows, ignore_index=True)
        if announcement_rows
        else pd.DataFrame()
    )

    grouped = {}
    if not announcements.empty:
        for key, group in announcements.groupby(["code", "report_date"]):
            full_reports = group[
                ~group["title"].astype(str).str.contains(r"摘要|英文版", regex=True)
            ]
            if full_reports.empty:
                full_reports = group
            full_reports = full_reports.sort_values("announcement_date")
            first_date = full_reports["announcement_date"].min()
            revision_mask = full_reports["title"].astype(str).str.contains(
                r"更正|修订|更新|补充", regex=True
            )
            revision_dates = full_reports.loc[revision_mask, "announcement_date"]
            anchor_date = (
                revision_dates.max() if not revision_dates.empty else first_date
            )
            distinct_dates = full_reports["announcement_date"].nunique()
            if not revision_dates.empty:
                quality = "conservative_revision"
            elif distinct_dates == 1:
                quality = "exact"
            else:
                quality = "heuristic_multiple"
            anchor_rows = full_reports[full_reports["announcement_date"].eq(anchor_date)]
            grouped[key] = {
                "first_announcement_date": first_date,
                "latest_announcement_date": full_reports["announcement_date"].max(),
                "availability_anchor_date": anchor_date,
                "availability_quality": quality,
                "matched_announcement_count": int(len(full_reports)),
                "matched_title": " || ".join(
                    anchor_rows["title"].astype(str).drop_duplicates().tolist()
                ),
            }

    start_date = pd.Timestamp(config["start_date"])
    output_rows = []
    for row in financial_periods.itertuples(index=False):
        key = (str(row.code), pd.Timestamp(row.report_date))
        match = grouped.get(key)
        if match:
            available_date = next_trading_day(
                trading_days, pd.Timestamp(match["availability_anchor_date"])
            )
            output_rows.append(
                {
                    "code": key[0],
                    "report_date": key[1],
                    **match,
                    "available_date": available_date,
                }
            )
            continue

        if key[1] < start_date:
            quality = "out_of_scope"
            available_date = pd.NaT
            anchor = pd.NaT
            title = None
        else:
            deadline = statutory_deadline(key[1])
            if deadline is not None:
                quality = "statutory_deadline"
                anchor = deadline
                available_date = next_trading_day(trading_days, deadline)
                title = f"statutory_deadline:{deadline.date()}"
            else:
                quality = "unmatched"
                available_date = pd.NaT
                anchor = pd.NaT
                title = None
        output_rows.append(
            {
                "code": key[0],
                "report_date": key[1],
                "first_announcement_date": pd.NaT,
                "latest_announcement_date": pd.NaT,
                "availability_anchor_date": anchor,
                "availability_quality": quality,
                "matched_announcement_count": 0,
                "matched_title": title,
                "available_date": available_date,
            }
        )

    result = pd.DataFrame(output_rows).sort_values(["code", "report_date"])
    atomic_parquet(result, PARQUET / "reference" / "financial_availability.parquet")
    counts = result["availability_quality"].value_counts(dropna=False).to_dict()
    atomic_json(
        {
            "rows": len(result),
            "quality_counts": counts,
            "available_date_non_null": int(result["available_date"].notna().sum()),
            "updated_at": dt.datetime.now(),
        },
        STATE / "financial_availability_coverage.json",
    )
    logging.info("Financial availability rebuilt: %s rows; %s", len(result), counts)


def _quarter_key(ts: pd.Timestamp) -> tuple:
    ts = pd.Timestamp(ts)
    return (int(ts.year), int(ts.month))


def _incremental_income(cum: pd.Series) -> pd.Series:
    """Convert China cumulative income to quarterly increments."""
    out = {}
    by_year: Dict[int, Dict[int, float]] = {}
    for idx, value in cum.dropna().items():
        year, month = _quarter_key(pd.Timestamp(idx))
        by_year.setdefault(year, {})[month] = float(value)
    for year, months in by_year.items():
        q1 = months.get(3)
        h1 = months.get(6)
        q3 = months.get(9)
        annual = months.get(12)
        if q1 is not None:
            out[pd.Timestamp(year=year, month=3, day=31)] = q1
        if h1 is not None and q1 is not None:
            out[pd.Timestamp(year=year, month=6, day=30)] = h1 - q1
        elif h1 is not None:
            out[pd.Timestamp(year=year, month=6, day=30)] = h1
        if q3 is not None and h1 is not None:
            out[pd.Timestamp(year=year, month=9, day=30)] = q3 - h1
        elif q3 is not None and q1 is not None:
            out[pd.Timestamp(year=year, month=9, day=30)] = q3 - q1
        if annual is not None and q3 is not None:
            out[pd.Timestamp(year=year, month=12, day=31)] = annual - q3
        elif annual is not None and h1 is not None:
            out[pd.Timestamp(year=year, month=12, day=31)] = annual - h1
        elif annual is not None:
            out[pd.Timestamp(year=year, month=12, day=31)] = annual
    return pd.Series(out).sort_index()


def enrich_bj_indicators_with_valuation(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
) -> None:
    """Fill peTTM/pbMRQ for Beijing stocks using financials + market cap."""
    del config
    universe = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    universe = universe[universe["exchange"].eq("BJ")].copy()
    if codes:
        universe = universe[universe["code"].isin(set(codes))]
    if limit:
        universe = universe.head(limit)

    availability = pd.read_parquet(
        PARQUET / "reference" / "financial_availability.parquet",
        columns=["code", "report_date", "available_date", "availability_quality"],
    )
    availability = availability[
        availability["available_date"].notna()
        & ~availability["availability_quality"].isin(["unmatched", "out_of_scope"])
    ].copy()
    availability["report_date"] = pd.to_datetime(availability["report_date"]).dt.normalize()
    availability["available_date"] = pd.to_datetime(
        availability["available_date"]
    ).dt.normalize()

    updated = 0
    pe_filled = 0
    pb_filled = 0
    failures = []
    for index, row in enumerate(universe.itertuples(index=False), start=1):
        code = str(row.code)
        ind_path = PARQUET / "daily_indicators" / f"{code}.parquet"
        fin_path = PARQUET / "financials" / f"{code}.parquet"
        shares_path = PARQUET / "shares_daily" / f"{code}.parquet"
        if not ind_path.exists() or not fin_path.exists() or not shares_path.exists():
            continue
        try:
            indicators = pd.read_parquet(ind_path)
            indicators["date"] = pd.to_datetime(indicators["date"]).dt.normalize()
            financials = pd.read_parquet(fin_path)
            financials["report_date"] = pd.to_datetime(
                financials["report_date"]
            ).dt.normalize()

            income = financials[
                financials["statement_type"].eq("lrb")
                & financials["item_code"].isin(["PARENETP", "NETPROFIT"])
            ]
            parent = income[income["item_code"].eq("PARENETP")]
            use_income = parent if not parent.empty else income
            if use_income.empty:
                continue
            cum = use_income.groupby("report_date")["value"].last().sort_index()
            increments = _incremental_income(cum)

            equity = financials[
                financials["statement_type"].eq("fzb")
                & financials["item_code"].isin(["PARESHARRIGH", "RIGHAGGR"])
            ]
            parent_eq = equity[equity["item_code"].eq("PARESHARRIGH")]
            use_equity = parent_eq if not parent_eq.empty else equity
            book = use_equity.groupby("report_date")["value"].last().sort_index()

            avail = availability[availability["code"].eq(code)][
                ["report_date", "available_date"]
            ]
            if avail.empty:
                income_asof = pd.DataFrame(
                    {
                        "report_date": increments.index,
                        "q_profit": increments.values,
                        "available_date": increments.index + pd.Timedelta(days=45),
                    }
                )
                book_asof = pd.DataFrame(
                    {
                        "report_date": book.index,
                        "book_equity": book.values,
                        "available_date": book.index + pd.Timedelta(days=45),
                    }
                )
            else:
                income_asof = (
                    increments.rename("q_profit")
                    .rename_axis("report_date")
                    .reset_index()
                    .merge(avail, on="report_date", how="inner")
                )
                book_asof = (
                    book.rename("book_equity")
                    .rename_axis("report_date")
                    .reset_index()
                    .merge(avail, on="report_date", how="inner")
                )

            shares = pd.read_parquet(
                shares_path, columns=["date", "total_market_cap"]
            )
            shares["date"] = pd.to_datetime(shares["date"]).dt.normalize()
            bars = indicators.merge(shares, on="date", how="left")

            pe_vals: List[Optional[float]] = []
            pb_vals: List[Optional[float]] = []
            income_asof = income_asof.dropna(
                subset=["available_date", "report_date", "q_profit"]
            ).sort_values("available_date")
            book_asof = book_asof.dropna(
                subset=["available_date", "book_equity"]
            ).sort_values("available_date")

            for rec in bars.itertuples(index=False):
                asof = pd.Timestamp(rec.date)
                pe = None
                pb = None
                known_inc = income_asof[income_asof["available_date"] <= asof]
                if len(known_inc) >= 4 and pd.notna(rec.total_market_cap):
                    ttm = float(known_inc.tail(4)["q_profit"].sum())
                    if ttm > 0:
                        pe = float(rec.total_market_cap) / ttm
                known_book = book_asof[book_asof["available_date"] <= asof]
                if not known_book.empty and pd.notna(rec.total_market_cap):
                    eq = float(known_book.iloc[-1]["book_equity"])
                    if eq > 0:
                        pb = float(rec.total_market_cap) / eq
                pe_vals.append(pe)
                pb_vals.append(pb)

            indicators["peTTM"] = pe_vals
            indicators["pbMRQ"] = pb_vals
            indicators["source"] = "derived_bars_shares_bj+financials"
            atomic_parquet(indicators, ind_path)
            updated += 1
            pe_filled += int(pd.Series(pe_vals).notna().any())
            pb_filled += int(pd.Series(pb_vals).notna().any())
            if index % 50 == 0:
                logging.info("BJ valuation progress %s/%s", index, len(universe))
        except Exception as exc:  # noqa: BLE001
            failures.append({"code": code, "error": str(exc)})

    atomic_json(
        {
            "updated": updated,
            "with_pe": pe_filled,
            "with_pb": pb_filled,
            "failures": failures,
            "updated_at": dt.datetime.now(),
        },
        STATE / "bj_valuation_coverage.json",
    )
    logging.info(
        "BJ valuation finished: updated=%s with_pe=%s with_pb=%s failures=%s",
        updated, pe_filled, pb_filled, len(failures),
    )


def _ensure_duckdb():
    if duckdb is None:
        raise RuntimeError("duckdb is required; pip install duckdb")


def build_warehouse(config: Dict) -> None:
    """Compact hot research tables into year/exchange partitioned Parquet."""
    del config
    _ensure_duckdb()
    WAREHOUSE.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        jobs = [
            (
                "daily_bars_qfq",
                str(PARQUET / "daily_bars" / "adjustment=qfq" / "*.parquet"),
                ["code", "date", "exchange", "open", "high", "low", "close", "volume", "amount", "source"],
                True,
            ),
            (
                "daily_bars_raw",
                str(PARQUET / "daily_bars" / "adjustment=raw" / "*.parquet"),
                ["code", "date", "exchange", "open", "high", "low", "close", "volume", "amount", "source"],
                True,
            ),
            (
                "daily_indicators",
                str(PARQUET / "daily_indicators" / "*.parquet"),
                ["code", "date", "turn", "tradestatus", "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM", "isST", "source"],
                False,
            ),
            (
                "shares_daily",
                str(PARQUET / "shares_daily" / "*.parquet"),
                ["code", "date", "total_shares", "float_shares", "total_market_cap", "float_market_cap", "source"],
                False,
            ),
            (
                "limit_prices",
                str(PARQUET / "limit_prices" / "*.parquet"),
                ["code", "date", "prev_close", "close", "limit_up", "limit_down", "limit_pct", "is_limit_up", "is_limit_down", "source"],
                False,
            ),
        ]
        summary = {}
        for name, pattern, cols, has_exchange in jobs:
            target = WAREHOUSE / name
            if target.exists():
                import shutil

                shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=True)
            col_sql = ", ".join(cols)
            if has_exchange:
                exchange_expr = "CAST(exchange AS VARCHAR)"
            else:
                exchange_expr = """
                    CASE
                      WHEN CAST(code AS VARCHAR) LIKE '92%'
                        OR CAST(code AS VARCHAR) LIKE '8%'
                        OR CAST(code AS VARCHAR) LIKE '4%' THEN 'BJ'
                      WHEN CAST(code AS VARCHAR) LIKE '6%' THEN 'SH'
                      ELSE 'SZ'
                    END
                """
            logging.info("warehouse compacting %s", name)
            con.execute(
                f"""
                COPY (
                  SELECT
                    {col_sql},
                    year(CAST(date AS DATE)) AS year,
                    {exchange_expr} AS exchange_part
                  FROM read_parquet('{pattern}', union_by_name=true)
                )
                TO '{target.as_posix()}'
                (FORMAT PARQUET, PARTITION_BY (year, exchange_part), OVERWRITE_OR_IGNORE)
                """
            )
            file_count = len(list(target.rglob("*.parquet")))
            summary[name] = {"files": file_count}
            logging.info("warehouse %s -> %s files", name, file_count)

        # Materialized stock_daily research panel from warehouse parts.
        panel_dir = WAREHOUSE / "stock_daily"
        if panel_dir.exists():
            import shutil

            shutil.rmtree(panel_dir)
        panel_dir.mkdir(parents=True, exist_ok=True)
        logging.info("warehouse building stock_daily panel")
        con.execute(
            f"""
            COPY (
              SELECT
                b.code,
                CAST(b.date AS DATE) AS date,
                b.exchange,
                b.open, b.high, b.low, b.close, b.volume, b.amount,
                i.turn AS turnover_pct,
                CAST(i.tradestatus AS BOOLEAN) AS trade_status,
                i.pctChg AS pct_change,
                i.peTTM AS pe_ttm,
                i.pbMRQ AS pb_mrq,
                i.psTTM AS ps_ttm,
                i.pcfNcfTTM AS pcf_ncf_ttm,
                CAST(i.isST AS BOOLEAN) AS is_st,
                s.total_shares,
                s.float_shares,
                s.total_market_cap,
                s.float_market_cap,
                l.limit_up,
                l.limit_down,
                l.limit_pct,
                l.is_limit_up,
                l.is_limit_down,
                year(CAST(b.date AS DATE)) AS year,
                b.exchange AS exchange_part
              FROM read_parquet('{(WAREHOUSE / "daily_bars_qfq").as_posix()}/**/*.parquet', hive_partitioning=true, union_by_name=true) b
              LEFT JOIN read_parquet('{(WAREHOUSE / "daily_indicators").as_posix()}/**/*.parquet', hive_partitioning=true, union_by_name=true) i
                ON b.code = i.code AND CAST(b.date AS DATE) = CAST(i.date AS DATE)
              LEFT JOIN read_parquet('{(WAREHOUSE / "shares_daily").as_posix()}/**/*.parquet', hive_partitioning=true, union_by_name=true) s
                ON b.code = s.code AND CAST(b.date AS DATE) = CAST(s.date AS DATE)
              LEFT JOIN read_parquet('{(WAREHOUSE / "limit_prices").as_posix()}/**/*.parquet', hive_partitioning=true, union_by_name=true) l
                ON b.code = l.code AND CAST(b.date AS DATE) = CAST(l.date AS DATE)
            )
            TO '{panel_dir.as_posix()}'
            (FORMAT PARQUET, PARTITION_BY (year, exchange_part), OVERWRITE_OR_IGNORE)
            """
        )
        summary["stock_daily"] = {
            "files": len(list(panel_dir.rglob("*.parquet")))
        }
        atomic_json(
            {"tables": summary, "updated_at": dt.datetime.now()},
            STATE / "warehouse_coverage.json",
        )
        logging.info("warehouse finished: %s", summary)
    finally:
        con.close()


def build_catalog(config: Dict) -> None:
    """Create catalog/quant.duckdb views over warehouse + reference parquet."""
    del config
    _ensure_duckdb()
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    if CATALOG.exists():
        CATALOG.unlink()
    con = duckdb.connect(str(CATALOG))
    try:
        # Prefer warehouse panel when present.
        stock_daily_glob = (WAREHOUSE / "stock_daily").as_posix() + "/**/*.parquet"
        if list((WAREHOUSE / "stock_daily").rglob("*.parquet")):
            con.execute(
                f"""
                CREATE OR REPLACE VIEW stock_daily AS
                SELECT * EXCLUDE (year, exchange_part)
                FROM read_parquet('{stock_daily_glob}', hive_partitioning=true, union_by_name=true)
                """
            )
        else:
            logging.warning("stock_daily warehouse missing; skip stock_daily view")

        def parquet_view(name: str, path: Path, select_sql: str) -> None:
            if not path.exists() and "*" not in path.name:
                # glob parent
                pass
            con.execute(f"CREATE OR REPLACE VIEW {name} AS {select_sql}")

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

        avail = (PARQUET / "reference" / "financial_availability.parquet").as_posix()
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
              matched_announcement_count,
              CAST(matched_title AS VARCHAR) AS matched_title,
              CAST(available_date AS DATE) AS available_date
            FROM read_parquet('{avail}')
            """
        )

        fin_glob = (PARQUET / "financials").as_posix() + "/*.parquet"
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
        ca_glob = (PARQUET / "corporate_actions").as_posix() + "/*.parquet"
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
