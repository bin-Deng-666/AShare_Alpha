"""research — financial availability: data acquisition and normalization."""

from __future__ import annotations
import datetime as dt
import logging
import re
from bisect import bisect_right
from typing import Dict, Optional, Sequence
import pandas as pd
from ashare_alpha.data.paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE

from ashare_alpha.data.storage import atomic_json
from ashare_alpha.data.storage import atomic_parquet


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
    if (
        re.search(r"(?:年度报告|年报)", text)
        and "半年度" not in text
        and "中期" not in text
    ):
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
        pd.to_datetime(calendar.loc[calendar["is_trading_day"].eq(1), "calendar_date"])
        .dt.normalize()
        .dropna()
        .unique()
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
            revision_mask = (
                full_reports["title"]
                .astype(str)
                .str.contains(r"更正|修订|更新|补充", regex=True)
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
            anchor_rows = full_reports[
                full_reports["announcement_date"].eq(anchor_date)
            ]
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
