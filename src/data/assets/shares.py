"""assets — shares: data acquisition and normalization."""

from __future__ import annotations
import datetime as dt
import logging
from typing import Dict, Optional, Sequence
import pandas as pd
from ashare_alpha.data.paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE

from ashare_alpha.data.storage import atomic_json
from ashare_alpha.data.storage import atomic_parquet


def _post_event_total_shares(row: pd.Series) -> Optional[float]:
    """Derive outstanding shares after a bonus/transfer event."""
    total = row.get("total_shares")
    if pd.isna(total):
        return None
    total = float(total)
    bonus = (
        float(row["bonus_share_per_10"])
        if pd.notna(row.get("bonus_share_per_10"))
        else 0.0
    )
    transfer = (
        float(row["transfer_share_per_10"])
        if pd.notna(row.get("transfer_share_per_10"))
        else 0.0
    )
    factor = 1.0 + (bonus + transfer) / 10.0
    return total * factor


def _trading_dates_for_symbol(
    code: str,
    master: pd.DataFrame,
    trading_days: pd.Series,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> pd.Index:
    symbol_start = start_date
    symbol_end = end_date
    if code in master.index:
        row = master.loc[code]
        if pd.notna(row.list_date):
            symbol_start = max(symbol_start, pd.Timestamp(row.list_date))
        if pd.notna(row.delist_date):
            symbol_end = min(symbol_end, pd.Timestamp(row.delist_date))
    dates = trading_days[(trading_days >= symbol_start) & (trading_days <= symbol_end)]
    bar_path = PARQUET / "daily_bars" / "adjustment=raw" / f"{code}.parquet"
    if bar_path.exists():
        bar_dates = pd.to_datetime(
            pd.read_parquet(bar_path, columns=["date"])["date"]
        ).dt.normalize()
        return pd.Index(sorted(set(dates).intersection(set(bar_dates))))
    return pd.Index(dates)


def _shares_points_from_equity(equity: pd.DataFrame) -> Optional[pd.DataFrame]:
    frame = equity.dropna(subset=["end_date", "total_shares"]).copy()
    if frame.empty:
        return None
    frame = frame.sort_values("end_date")
    points = []
    for row in frame.itertuples(index=False):
        points.append(
            {
                "effective_date": pd.Timestamp(row.end_date),
                "total_shares": float(row.total_shares),
                "float_shares": (
                    float(row.float_shares) if pd.notna(row.float_shares) else pd.NA
                ),
            }
        )
    return (
        pd.DataFrame(points)
        .drop_duplicates("effective_date", keep="last")
        .sort_values("effective_date")
    )


def _shares_points_from_corporate_actions(
    actions: pd.DataFrame,
) -> Optional[pd.DataFrame]:
    if actions.empty or "total_shares" not in actions.columns:
        return None
    frame = actions.copy()
    frame["ex_date"] = pd.to_datetime(frame["ex_date"], errors="coerce")
    frame["notice_date"] = pd.to_datetime(frame["notice_date"], errors="coerce")
    frame["effective_date"] = frame["ex_date"].fillna(frame["notice_date"])
    frame = frame.dropna(subset=["effective_date", "total_shares"])
    if "progress" in frame.columns:
        implemented = frame[
            frame["progress"].astype(str).str.contains("实施", na=False)
        ]
        if not implemented.empty:
            frame = implemented
    if frame.empty:
        return None
    frame = frame.sort_values("effective_date")
    frame["post_total_shares"] = frame.apply(_post_event_total_shares, axis=1)
    frame = frame.dropna(subset=["post_total_shares"])
    if frame.empty:
        return None
    points = []
    for row in frame.itertuples(index=False):
        points.append(
            {
                "effective_date": pd.Timestamp(row.effective_date),
                "total_shares": float(row.post_total_shares),
                "float_shares": pd.NA,
            }
        )
    return (
        pd.DataFrame(points)
        .drop_duplicates("effective_date", keep="last")
        .sort_values("effective_date")
    )


def build_shares_daily(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
    on_code_done=None,
) -> None:
    """Build dated total/float shares and market caps.

    Use only source points effective on or before each raw date. Earlier dates
    stay present with NULL values; a later snapshot cannot seed past capital.
    """
    calendar = pd.read_parquet(PARQUET / "reference" / "trade_calendar.parquet")
    trading_days = (
        pd.to_datetime(calendar.loc[calendar["is_trading_day"].eq(1), "calendar_date"])
        .dt.normalize()
        .dropna()
        .sort_values()
    )
    start_date = pd.Timestamp(config["start_date"])
    end_date = pd.Timestamp(config["end_date"] or dt.date.today())
    trading_days = trading_days[
        (trading_days >= start_date) & (trading_days <= end_date)
    ]

    master = pd.read_parquet(
        PARQUET / "security_master" / "security_master.parquet"
    ).set_index("code")
    equity_dir = PARQUET / "equity_structure"
    action_dir = PARQUET / "corporate_actions"
    equity_codes = (
        {path.stem for path in equity_dir.glob("*.parquet")}
        if equity_dir.exists()
        else set()
    )
    action_codes = (
        {path.stem for path in action_dir.glob("*.parquet")}
        if action_dir.exists()
        else set()
    )
    target_codes = sorted(equity_codes | action_codes)
    if codes:
        wanted = set(codes)
        target_codes = [code for code in target_codes if code in wanted]
    if limit:
        target_codes = target_codes[:limit]

    output_dir = PARQUET / "shares_daily"
    output_dir.mkdir(parents=True, exist_ok=True)
    built = 0
    skipped_empty = 0
    with_float = 0
    failures = []
    for index, code in enumerate(target_codes, start=1):
        output = output_dir / f"{code}.parquet"
        if output.exists() and not force:
            continue
        try:
            points_frame = None
            source_tag = None
            equity_path = equity_dir / f"{code}.parquet"
            if equity_path.exists():
                equity = pd.read_parquet(equity_path)
                if not equity.empty:
                    points_frame = _shares_points_from_equity(equity)
                    source_tag = "equity_structure+raw_bars"
            if points_frame is None:
                action_path = action_dir / f"{code}.parquet"
                if action_path.exists():
                    actions = pd.read_parquet(action_path)
                    points_frame = _shares_points_from_corporate_actions(actions)
                    source_tag = "corporate_actions+raw_bars"
            if points_frame is None or points_frame.empty:
                skipped_empty += 1
                continue

            dates = _trading_dates_for_symbol(
                code, master, trading_days, start_date, end_date
            )
            if len(dates) == 0:
                skipped_empty += 1
                continue

            shares = pd.DataFrame({"date": dates})
            shares = pd.merge_asof(
                shares.sort_values("date"),
                points_frame.rename(columns={"effective_date": "date"}),
                on="date",
                direction="backward",
            )
            if shares.empty:
                skipped_empty += 1
                continue

            shares["code"] = code
            shares["total_market_cap"] = pd.NA
            shares["float_market_cap"] = pd.NA
            bar_path = PARQUET / "daily_bars" / "adjustment=raw" / f"{code}.parquet"
            if bar_path.exists():
                bars = pd.read_parquet(bar_path, columns=["date", "close"])
                bars["date"] = pd.to_datetime(bars["date"]).dt.normalize()
                shares = shares.merge(bars, on="date", how="left")
                shares["total_market_cap"] = shares["total_shares"] * shares["close"]
                shares["float_market_cap"] = shares["float_shares"] * shares["close"]
                shares = shares.drop(columns=["close"])
            shares["total_shares"] = shares["total_shares"].round().astype("Int64")
            shares["float_shares"] = (
                pd.to_numeric(shares["float_shares"], errors="coerce")
                .round()
                .astype("Int64")
            )
            shares["source"] = source_tag
            if shares["float_shares"].notna().any():
                with_float += 1
            atomic_parquet(
                shares[
                    [
                        "code",
                        "date",
                        "total_shares",
                        "float_shares",
                        "total_market_cap",
                        "float_market_cap",
                        "source",
                    ]
                ].sort_values("date"),
                output,
            )
            if on_code_done is not None:
                on_code_done(code)
            built += 1
            if index % 500 == 0:
                logging.info(
                    "shares_daily progress: %s/%s built=%s with_float=%s",
                    index,
                    len(target_codes),
                    built,
                    with_float,
                )
        except Exception as exc:  # noqa: BLE001
            failures.append({"code": code, "error": str(exc)})
    atomic_json(
        {
            "built": built,
            "with_float": with_float,
            "skipped_empty": skipped_empty,
            "failures": failures,
            "updated_at": dt.datetime.now(),
        },
        STATE / "shares_daily_coverage.json",
    )
    logging.info(
        "shares_daily finished: built=%s with_float=%s skipped_empty=%s failures=%s",
        built,
        with_float,
        skipped_empty,
        len(failures),
    )
