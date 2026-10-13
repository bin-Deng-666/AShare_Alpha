"""sources — reference: data acquisition and normalization."""
from __future__ import annotations
import datetime as dt
import logging
from typing import Dict, List, Sequence
import pandas as pd
from data.ashare_data.paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE

try:
    import baostock as bs
except ImportError:
    bs = None

from data.ashare_data.storage import atomic_json
from data.ashare_data.storage import atomic_parquet
from data.ashare_data.sources.indicators import baostock_frame

def build_reference_data(config: Dict) -> None:
    """Build trading calendar, benchmark bars, industry and index snapshots."""
    if bs is None:
        raise RuntimeError("baostock is required; install requirements.txt")
    start_date = pd.Timestamp(config["start_date"])
    end_date = pd.Timestamp(config["end_date"] or dt.date.today())
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    try:
        calendar_path = PARQUET / 'reference' / 'trade_calendar.parquet'
        calendar = pd.read_parquet(calendar_path) if calendar_path.exists() else pd.DataFrame()
        calendar_current = (not calendar.empty
                            and pd.to_datetime(calendar['calendar_date']).min() <= start_date
                            and pd.to_datetime(calendar['calendar_date']).max() >= end_date)
        if not calendar_current:
            calendar = baostock_frame(
                bs.query_trade_dates(start_date=start_date.strftime('%Y-%m-%d'),
                                     end_date=end_date.strftime('%Y-%m-%d'))
            )
            calendar['calendar_date'] = pd.to_datetime(calendar['calendar_date'])
            calendar['is_trading_day'] = pd.to_numeric(calendar['is_trading_day']).astype('Int8')
            atomic_parquet(calendar, calendar_path)

        index_fields = (
            "date,code,open,high,low,close,preclose,volume,amount,pctChg"
        )
        from ..incremental_files import _last_date, _merge_by_date
        for code in ("000300", "000905", "000852"):
            index_path = PARQUET / 'reference' / 'index_bars' / f'{code}.parquet'
            last = _last_date(index_path)
            query_start = max(start_date, last - pd.Timedelta(days=5)) if last is not None else start_date
            if config.get('refresh_from'):
                query_start = max(start_date, min(query_start, pd.Timestamp(config['refresh_from'])))
            frame = baostock_frame(
                bs.query_history_k_data_plus(
                    f"sh.{code}",
                    index_fields,
                    start_date=query_start.strftime("%Y-%m-%d"),
                    end_date=end_date.strftime("%Y-%m-%d"),
                    frequency="d",
                    adjustflag="3",
                )
            )
            if frame.empty:
                continue
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            for column in (
                "open", "high", "low", "close", "preclose",
                "volume", "amount", "pctChg",
            ):
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
            frame["source"] = "baostock"
            existing = pd.read_parquet(index_path) if index_path.exists() else pd.DataFrame()
            atomic_parquet(_merge_by_date(existing, frame), index_path)

        snapshot_dates = [
            f"{year}-12-31" for year in range(start_date.year, end_date.year)
        ] + [end_date.strftime("%Y-%m-%d")]
        industry_path = PARQUET / 'reference' / 'industry_membership.parquet'
        index_path = PARQUET / 'reference' / 'index_constituents.parquet'
        old_industry = pd.read_parquet(industry_path) if industry_path.exists() else pd.DataFrame()
        old_members = pd.read_parquet(index_path) if index_path.exists() else pd.DataFrame()
        industry_dates = set(pd.to_datetime(old_industry['snapshot_date']).dt.strftime('%Y-%m-%d')) if not old_industry.empty else set()
        member_dates = set(zip(old_members['index_name'], pd.to_datetime(old_members['snapshot_date']).dt.strftime('%Y-%m-%d'))) if not old_members.empty else set()
        industry_frames = [old_industry] if not old_industry.empty else []
        constituent_frames = [old_members] if not old_members.empty else []
        for snapshot_date in snapshot_dates:
            industry = baostock_frame(bs.query_stock_industry(date=snapshot_date)) if snapshot_date not in industry_dates else pd.DataFrame()
            if not industry.empty:
                industry["snapshot_date"] = pd.Timestamp(snapshot_date)
                industry_frames.append(industry)
            for index_name, query in (
                ("hs300", bs.query_hs300_stocks),
                ("zz500", bs.query_zz500_stocks),
            ):
                if (index_name, snapshot_date) in member_dates:
                    continue
                members = baostock_frame(query(snapshot_date))
                if not members.empty:
                    members["snapshot_date"] = pd.Timestamp(snapshot_date)
                    members["index_name"] = index_name
                    constituent_frames.append(members)
        if industry_frames:
            atomic_parquet(
                pd.concat(industry_frames, ignore_index=True),
                PARQUET / "reference" / "industry_membership.parquet",
            )
        if constituent_frames:
            atomic_parquet(
                pd.concat(constituent_frames, ignore_index=True),
                PARQUET / "reference" / "index_constituents.parquet",
            )
    finally:
        bs.logout()


def _merge_adjacent_intervals(
    rows: List[Dict],
    identity_columns: Sequence[str],
) -> pd.DataFrame:
    """Merge consecutive snapshot-derived intervals with identical identity."""
    if not rows:
        return pd.DataFrame()
    ordered = sorted(
        rows,
        key=lambda row: (
            str(row.get("code", "")),
            *(str(row.get(column, "")) for column in identity_columns),
            pd.Timestamp(row["valid_from"]),
        ),
    )
    merged: List[Dict] = []
    for row in ordered:
        if merged:
            previous = merged[-1]
            same_identity = (
                previous["code"] == row["code"]
                and all(
                    previous[column] == row[column]
                    for column in identity_columns
                )
            )
            adjacent = (
                pd.notna(previous["valid_to"])
                and pd.Timestamp(previous["valid_to"])
                == pd.Timestamp(row["valid_from"])
            )
            if same_identity and adjacent:
                previous["valid_to"] = row["valid_to"]
                previous["is_current_snapshot"] = row.get(
                    "is_current_snapshot", False
                )
                continue
        merged.append(dict(row))
    return pd.DataFrame(merged)


def build_reference_intervals(config: Dict) -> None:
    """Convert raw annual snapshots into explicit, quality-tagged intervals."""
    del config
    industry_path = PARQUET / "reference" / "industry_membership.parquet"
    constituent_path = PARQUET / "reference" / "index_constituents.parquet"
    if not industry_path.exists() or not constituent_path.exists():
        raise RuntimeError("reference snapshots are missing; run reference first")

    industry = pd.read_parquet(industry_path)
    industry["snapshot_date"] = pd.to_datetime(
        industry["snapshot_date"], errors="coerce"
    ).dt.normalize()
    industry_dates = sorted(industry["snapshot_date"].dropna().unique())
    industry_next = {
        pd.Timestamp(date): (
            pd.Timestamp(industry_dates[index + 1])
            if index + 1 < len(industry_dates) else pd.NaT
        )
        for index, date in enumerate(industry_dates)
    }
    industry_rows: List[Dict] = []
    for row in industry.itertuples(index=False):
        snapshot_date = pd.Timestamp(row.snapshot_date)
        name = str(row.industry or "").strip()
        classification = str(row.industryClassification or "证监会行业分类").strip()
        code = str(row.code).split(".")[-1].zfill(6)
        industry_rows.append({
            "code": code,
            "classification_system": "CSRC",
            "industry_level": max(1, len([part for part in name.split("-") if part])),
            "industry_code": f"CSRC:{classification}:{name}",
            "industry_name": name,
            "valid_from": snapshot_date,
            "valid_to": industry_next[snapshot_date],
            "snapshot_date": snapshot_date,
            "is_current_snapshot": snapshot_date == pd.Timestamp(industry_dates[-1]),
            "interval_quality": "snapshot_derived",
            "source_update_date": pd.to_datetime(
                row.updateDate, errors="coerce"
            ),
            "source": "baostock",
        })
    industry_intervals = _merge_adjacent_intervals(
        industry_rows,
        ["classification_system", "industry_level", "industry_code"],
    )

    industry_intervals = industry_intervals.sort_values(
        ["classification_system", "code", "valid_from", "industry_code"]
    )
    atomic_parquet(
        industry_intervals,
        PARQUET / "reference" / "industry_membership_intervals.parquet",
    )

    constituents = pd.read_parquet(constituent_path)
    constituents["snapshot_date"] = pd.to_datetime(
        constituents["snapshot_date"], errors="coerce"
    ).dt.normalize()
    index_code_map = {"hs300": "000300", "zz500": "000905"}
    constituent_rows: List[Dict] = []
    for index_name, group in constituents.groupby("index_name"):
        snapshot_dates = sorted(group["snapshot_date"].dropna().unique())
        next_dates = {
            pd.Timestamp(date): (
                pd.Timestamp(snapshot_dates[index + 1])
                if index + 1 < len(snapshot_dates) else pd.NaT
            )
            for index, date in enumerate(snapshot_dates)
        }
        for row in group.itertuples(index=False):
            snapshot_date = pd.Timestamp(row.snapshot_date)
            constituent_rows.append({
                "index_code": index_code_map.get(str(index_name), str(index_name)),
                "code": str(row.code).split(".")[-1].zfill(6),
                "valid_from": snapshot_date,
                "valid_to": next_dates[snapshot_date],
                "snapshot_date": snapshot_date,
                "interval_quality": "snapshot_derived",
                "source_update_date": pd.to_datetime(
                    row.updateDate, errors="coerce"
                ),
                "source": "baostock",
            })
    constituent_intervals = _merge_adjacent_intervals(
        constituent_rows, ["index_code"]
    )
    constituent_intervals = constituent_intervals.sort_values(
        ["index_code", "code", "valid_from"]
    )
    atomic_parquet(
        constituent_intervals,
        PARQUET / "reference" / "index_constituent_intervals.parquet",
    )
    atomic_json(
        {
            "industry_interval_rows": len(industry_intervals),
            "index_constituent_interval_rows": len(constituent_intervals),
            "updated_at": dt.datetime.now(),
        },
        STATE / "reference_interval_coverage.json",
    )
    logging.info(
        "Reference intervals saved: %s industry, %s index constituent rows",
        len(industry_intervals), len(constituent_intervals),
    )


def refresh_trade_calendar(config: Dict) -> None:
    if bs is None:
        raise RuntimeError("baostock is required; install requirements.txt")
    start_date = pd.Timestamp(config["start_date"])
    end_date = pd.Timestamp(config["end_date"] or dt.date.today())
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    try:
        calendar = baostock_frame(
            bs.query_trade_dates(
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
            )
        )
        calendar["calendar_date"] = pd.to_datetime(calendar["calendar_date"])
        calendar["is_trading_day"] = pd.to_numeric(
            calendar["is_trading_day"], errors="coerce"
        ).astype("Int8")
        path = PARQUET / 'reference' / 'trade_calendar.parquet'
        if path.exists():
            existing = pd.read_parquet(path)
            calendar = pd.concat([existing, calendar], ignore_index=True).drop_duplicates('calendar_date', keep='last').sort_values('calendar_date').reset_index(drop=True)
        atomic_parquet(calendar, path)
    finally:
        bs.logout()
    logging.info("Trade calendar refreshed through %s", end_date.date())
