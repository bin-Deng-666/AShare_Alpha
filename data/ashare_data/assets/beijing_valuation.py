"""assets — beijing valuation: data acquisition and normalization."""
from __future__ import annotations
import datetime as dt
import logging
from typing import Dict, List, Optional, Sequence
import pandas as pd
from data.ashare_data.paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE

from data.ashare_data.storage import atomic_json
from data.ashare_data.storage import atomic_parquet

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
