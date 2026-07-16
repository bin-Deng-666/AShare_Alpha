#!/usr/bin/env python3
"""Research-layer builders: availability polish, warehouse, DuckDB catalog."""
from __future__ import annotations

import datetime as dt
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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


@dataclass(frozen=True)
class WarehouseLayerSpec:
    """Physical-to-warehouse policy for one dated research layer."""

    name: str
    pattern: str
    columns: Tuple[str, ...]
    has_exchange: bool
    required: bool = True


def warehouse_layer_specs(parquet_root: Path | None = None) -> Tuple[WarehouseLayerSpec, ...]:
    """Return the shared full/incremental warehouse layer contract."""
    root = Path(parquet_root) if parquet_root is not None else PARQUET
    return (
        WarehouseLayerSpec(
            "daily_bars_qfq",
            str(root / "daily_bars" / "adjustment=qfq" / "*.parquet"),
            ("code", "date", "exchange", "open", "high", "low", "close", "volume", "amount", "source"),
            True,
        ),
        WarehouseLayerSpec(
            "daily_bars_raw",
            str(root / "daily_bars" / "adjustment=raw" / "*.parquet"),
            ("code", "date", "exchange", "open", "high", "low", "close", "volume", "amount", "source"),
            True,
        ),
        WarehouseLayerSpec(
            "daily_indicators",
            str(root / "daily_indicators" / "*.parquet"),
            ("code", "date", "turn", "tradestatus", "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM", "isST", "source"),
            False,
        ),
        WarehouseLayerSpec(
            "shares_daily",
            str(root / "shares_daily" / "*.parquet"),
            ("code", "date", "total_shares", "float_shares", "total_market_cap", "float_market_cap", "source"),
            False,
            required=False,
        ),
        WarehouseLayerSpec(
            "limit_prices",
            str(root / "limit_prices" / "*.parquet"),
            (
                "code", "date", "prev_close", "reference_price",
                "reference_price_source", "limit_validation_status",
                "cash_dividend_per_share", "share_distribution_ratio",
                "rights_issue_ratio", "rights_issue_price",
                "corporate_action_adjusted", "corporate_action_formula_status",
                "close",
                "limit_up", "limit_down", "limit_pct", "is_limit_up",
                "is_limit_down", "source",
            ),
            False,
        ),
    )


def compact_warehouse_layer(
    connection,
    spec: WarehouseLayerSpec,
    warehouse_root: Path,
    *,
    year: int | None = None,
) -> int:
    """Rewrite one warehouse layer for full history or one calendar year."""
    target = Path(warehouse_root) / spec.name
    if year is None:
        if target.exists():
            shutil.rmtree(target)
    else:
        partition = target / f"year={int(year)}"
        if partition.exists():
            shutil.rmtree(partition)
    target.mkdir(parents=True, exist_ok=True)

    if not list(Path(spec.pattern).parent.glob("*.parquet")):
        if spec.required:
            raise FileNotFoundError(
                f"required physical layer has no parquet files: {spec.pattern}"
            )
        return len(list(target.rglob("*.parquet")))

    year_predicate = ""
    if year is not None:
        year_predicate = f"WHERE year(CAST(date AS DATE)) = {int(year)}"
        source_rows = connection.execute(
            f"""
            SELECT count(*)
            FROM read_parquet('{spec.pattern}', union_by_name=true)
            {year_predicate}
            """
        ).fetchone()[0]
        if not source_rows:
            if spec.required:
                raise RuntimeError(
                    f"required layer {spec.name} has no rows for year={year}"
                )
            return len(list(target.rglob("*.parquet")))

    exchange_expr = (
        "CAST(exchange AS VARCHAR)"
        if spec.has_exchange
        else """
            CASE
              WHEN CAST(code AS VARCHAR) LIKE '92%'
                OR CAST(code AS VARCHAR) LIKE '8%'
                OR CAST(code AS VARCHAR) LIKE '4%' THEN 'BJ'
              WHEN CAST(code AS VARCHAR) LIKE '6%' THEN 'SH'
              ELSE 'SZ'
            END
        """
    )
    connection.execute(
        f"""
        COPY (
          SELECT
            {", ".join(spec.columns)},
            year(CAST(date AS DATE)) AS year,
            {exchange_expr} AS exchange_part
          FROM read_parquet('{spec.pattern}', union_by_name=true)
          {year_predicate}
        )
        TO '{target.as_posix()}'
        (FORMAT PARQUET, PARTITION_BY (year, exchange_part), OVERWRITE_OR_IGNORE)
        """
    )
    return len(list(target.rglob("*.parquet")))


def warehouse_shares_sql(
    warehouse_root: Path, *, asof: bool
) -> Tuple[str, str]:
    """Return share columns/join with current-date market-cap semantics."""
    derived_cap = "b.amount * 100.0 / NULLIF(i.turn, 0.0)"
    shares_root = Path(warehouse_root) / "shares_daily"
    if not list(shares_root.rglob("*.parquet")):
        return (
            f"""
                NULL::BIGINT AS total_shares,
                NULL::BIGINT AS float_shares,
                {derived_cap} AS total_market_cap,
                {derived_cap} AS float_market_cap,
            """,
            "",
        )

    if asof:
        total_cap = (
            "CASE WHEN CAST(b.date AS DATE) = s.date "
            f"THEN COALESCE(s.total_market_cap, s.float_market_cap, {derived_cap}) "
            f"ELSE {derived_cap} END"
        )
        float_cap = (
            "CASE WHEN CAST(b.date AS DATE) = s.date "
            f"THEN COALESCE(s.float_market_cap, {derived_cap}) "
            f"ELSE {derived_cap} END"
        )
        join_kind = "ASOF LEFT JOIN"
        date_predicate = "CAST(b.date AS DATE) >= s.date"
    else:
        total_cap = f"COALESCE(s.total_market_cap, s.float_market_cap, {derived_cap})"
        float_cap = f"COALESCE(s.float_market_cap, {derived_cap})"
        join_kind = "LEFT JOIN"
        date_predicate = "CAST(b.date AS DATE) = s.date"

    columns = f"""
        s.total_shares,
        s.float_shares,
        {total_cap} AS total_market_cap,
        {float_cap} AS float_market_cap,
    """
    join = f"""
      {join_kind} (
        SELECT code, CAST(date AS DATE) AS date,
               total_shares, float_shares, total_market_cap, float_market_cap
        FROM read_parquet('{shares_root.as_posix()}/**/*.parquet', hive_partitioning=true, union_by_name=true)
      ) s
        ON b.code = s.code AND {date_predicate}
    """
    return columns, join


def assert_warehouse_limit_coverage(
    connection, warehouse_root: Path, *, year: int | None = None
) -> None:
    """Require one dated limit row for every raw bar key in scope."""
    root = Path(warehouse_root)
    year_predicate = (
        f"AND year(CAST(b.date AS DATE)) = {int(year)}" if year is not None else ""
    )
    missing = connection.execute(
        f"""
        SELECT count(*)
        FROM read_parquet('{(root / "daily_bars_raw").as_posix()}/**/*.parquet', hive_partitioning=true, union_by_name=true) b
        LEFT JOIN read_parquet('{(root / "limit_prices").as_posix()}/**/*.parquet', hive_partitioning=true, union_by_name=true) l
          ON b.code = l.code AND CAST(b.date AS DATE) = CAST(l.date AS DATE)
        WHERE l.code IS NULL {year_predicate}
        """
    ).fetchone()[0]
    if missing:
        scope = f"year={year}" if year is not None else "full history"
        raise RuntimeError(f"limit_prices missing {missing} bar keys for {scope}")


def build_warehouse(config: Dict) -> None:
    """Compact hot research tables into year/exchange partitioned Parquet."""
    del config
    _ensure_duckdb()
    WAREHOUSE.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        summary = {}
        for spec in warehouse_layer_specs():
            logging.info("warehouse compacting %s", spec.name)
            file_count = compact_warehouse_layer(con, spec, WAREHOUSE)
            summary[spec.name] = {"files": file_count}
            logging.info("warehouse %s -> %s files", spec.name, file_count)

        assert_warehouse_limit_coverage(con, WAREHOUSE)

        # Materialized stock_daily research panel from warehouse parts.
        panel_dir = WAREHOUSE / "stock_daily"
        if panel_dir.exists():
            shutil.rmtree(panel_dir)
        panel_dir.mkdir(parents=True, exist_ok=True)
        logging.info("warehouse building stock_daily panel")
        shares_columns, shares_join = warehouse_shares_sql(
            WAREHOUSE, asof=False
        )
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
                {shares_columns}
                l.limit_up,
                l.limit_down,
                l.limit_pct,
                l.is_limit_up,
                l.is_limit_down,
                l.reference_price,
                l.reference_price_source,
                l.limit_validation_status,
                l.cash_dividend_per_share,
                l.share_distribution_ratio,
                l.rights_issue_ratio,
                l.rights_issue_price,
                l.corporate_action_adjusted,
                l.corporate_action_formula_status,
                year(CAST(b.date AS DATE)) AS year,
                b.exchange AS exchange_part
              FROM read_parquet('{(WAREHOUSE / "daily_bars_qfq").as_posix()}/**/*.parquet', hive_partitioning=true, union_by_name=true) b
              LEFT JOIN read_parquet('{(WAREHOUSE / "daily_indicators").as_posix()}/**/*.parquet', hive_partitioning=true, union_by_name=true) i
                ON b.code = i.code AND CAST(b.date AS DATE) = CAST(i.date AS DATE)
              {shares_join}
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
          CAST(COALESCE(s.total_market_cap, s.float_market_cap,
            b.amount * 100.0 / NULLIF(i.turn, 0.0)) AS DOUBLE) AS total_market_cap,
          CAST(COALESCE(s.float_market_cap,
            b.amount * 100.0 / NULLIF(i.turn, 0.0)) AS DOUBLE) AS float_market_cap,
        """
    else:
        shares_columns = """
          NULL::BIGINT AS total_shares,
          NULL::BIGINT AS float_shares,
          CAST(b.amount * 100.0 / NULLIF(i.turn, 0.0) AS DOUBLE) AS total_market_cap,
          CAST(b.amount * 100.0 / NULLIF(i.turn, 0.0) AS DOUBLE) AS float_market_cap,
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
          CAST(COALESCE(l.is_limit_up, FALSE) AS BOOLEAN) AS is_limit_up,
          CAST(COALESCE(l.is_limit_down, FALSE) AS BOOLEAN) AS is_limit_down,
          CAST(l.reference_price AS DOUBLE) AS limit_reference_price,
          CAST(l.reference_price_source AS VARCHAR) AS limit_reference_source,
          CAST(l.limit_validation_status AS VARCHAR) AS limit_validation_status,
          CAST(l.cash_dividend_per_share AS DOUBLE) AS cash_dividend_per_share,
          CAST(l.share_distribution_ratio AS DOUBLE) AS share_distribution_ratio,
          CAST(l.rights_issue_ratio AS DOUBLE) AS rights_issue_ratio,
          CAST(l.rights_issue_price AS DOUBLE) AS rights_issue_price,
          CAST(COALESCE(l.corporate_action_adjusted, FALSE) AS BOOLEAN)
              AS corporate_action_adjusted,
          CAST(l.corporate_action_formula_status AS VARCHAR)
              AS corporate_action_formula_status,
        """
    else:
        limit_columns = """
          NULL::DOUBLE AS limit_up,
          NULL::DOUBLE AS limit_down,
          NULL::DOUBLE AS limit_pct,
          FALSE AS is_limit_up,
          FALSE AS is_limit_down,
          NULL::DOUBLE AS limit_reference_price,
          NULL::VARCHAR AS limit_reference_source,
          NULL::VARCHAR AS limit_validation_status,
          NULL::DOUBLE AS cash_dividend_per_share,
          NULL::DOUBLE AS share_distribution_ratio,
          NULL::DOUBLE AS rights_issue_ratio,
          NULL::DOUBLE AS rights_issue_price,
          FALSE AS corporate_action_adjusted,
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
        CAST(COALESCE(i.tradestatus, 0) AS BOOLEAN) AS trade_status,
        CAST(i.pctChg AS DOUBLE) AS pct_change,
        CAST(i.peTTM AS DOUBLE) AS pe_ttm,
        CAST(i.pbMRQ AS DOUBLE) AS pb_mrq,
        CAST(i.psTTM AS DOUBLE) AS ps_ttm,
        CAST(i.pcfNcfTTM AS DOUBLE) AS pcf_ncf_ttm,
        CAST(COALESCE(i.isST, 0) AS BOOLEAN) AS is_st,
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

    industry_files = list(
        (PARQUET / "reference" / "industry_indices" / "daily_bars").glob(
            "*.parquet"
        )
    )
    if industry_files:
        industry_glob = (
            PARQUET
            / "reference"
            / "industry_indices"
            / "daily_bars"
            / "*.parquet"
        ).as_posix()
        selects.append(
            f"""
            SELECT
              index_code,
              date,
              industry_name AS index_name,
              'industry'::VARCHAR AS index_type,
              open, high, low, close,
              derived_preclose AS preclose,
              volume,
              amount,
              CASE
                WHEN derived_preclose IS NULL OR derived_preclose = 0 THEN NULL
                ELSE (close / derived_preclose - 1.0) * 100.0
              END::DOUBLE AS pct_change,
              (source || ':derived_preclose_pct_change')::VARCHAR AS source
            FROM (
              SELECT
                {_normalize_index_code_sql("index_code")} AS index_code,
                CAST(date AS DATE) AS date,
                CAST(industry_name AS VARCHAR) AS industry_name,
                CAST(open AS DOUBLE) AS open,
                CAST(high AS DOUBLE) AS high,
                CAST(low AS DOUBLE) AS low,
                CAST(close AS DOUBLE) AS close,
                LAG(CAST(close AS DOUBLE)) OVER (
                  PARTITION BY CAST(index_code AS VARCHAR) ORDER BY CAST(date AS DATE)
                ) AS derived_preclose,
                CAST(volume AS DOUBLE) AS volume,
                CAST(amount AS DOUBLE) AS amount,
                CAST(source AS VARCHAR) AS source
              FROM read_parquet('{industry_glob}', union_by_name=true)
            ) industry
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
    """Create catalog/quant.duckdb views over warehouse + reference parquet."""
    del config
    _ensure_duckdb()
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    if CATALOG.exists():
        CATALOG.unlink()
    con = duckdb.connect(str(CATALOG))
    try:
        con.execute(
            "CREATE OR REPLACE VIEW stock_daily AS "
            + _catalog_daily_view_sql("qfq")
        )
        con.execute(
            "CREATE OR REPLACE VIEW stock_daily_raw AS "
            + _catalog_daily_view_sql("raw")
        )

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

        con.execute(
            "CREATE OR REPLACE VIEW index_daily_bars AS "
            + _catalog_index_view_sql()
        )

        industry_master_path = (
            PARQUET
            / "reference"
            / "industry_indices"
            / "sw_level1_master.parquet"
        )
        if industry_master_path.exists():
            industry_master = industry_master_path.as_posix()
            con.execute(
                f"""
                CREATE OR REPLACE VIEW industry_index_master AS
                SELECT
                  {_normalize_index_code_sql("index_code")} AS index_code,
                  CAST(industry_name AS VARCHAR) AS industry_name,
                  1::TINYINT AS industry_level,
                  CAST(constituent_count AS BIGINT) AS constituent_count,
                  CAST(pe_static AS DOUBLE) AS pe_static,
                  CAST(pe_ttm AS DOUBLE) AS pe_ttm,
                  CAST(pb AS DOUBLE) AS pb,
                  CAST(dividend_yield AS DOUBLE) AS dividend_yield,
                  CAST(snapshot_date AS DATE) AS snapshot_date,
                  CAST(source AS VARCHAR) AS source
                FROM read_parquet('{industry_master}')
                """
            )
        else:
            con.execute(
                """
                CREATE OR REPLACE VIEW industry_index_master AS
                SELECT NULL::VARCHAR AS index_code,
                  NULL::VARCHAR AS industry_name, NULL::TINYINT AS industry_level,
                  NULL::BIGINT AS constituent_count, NULL::DOUBLE AS pe_static,
                  NULL::DOUBLE AS pe_ttm, NULL::DOUBLE AS pb,
                  NULL::DOUBLE AS dividend_yield, NULL::DATE AS snapshot_date,
                  NULL::VARCHAR AS source
                WHERE FALSE
                """
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
        _assert_unique(
            con, "industry_index_master", ("index_code", "snapshot_date")
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
