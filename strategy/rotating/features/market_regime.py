"""Point-in-time monthly market-state features for portfolio entry control."""

from __future__ import annotations

import numpy as np
import pandas as pd


def monthly_gate_state_by_month(entry_gate: pd.DataFrame) -> pd.Series:
    """Validate a monthly gate and return one conservative state per month."""
    missing = {"effective_month", "allow_new_buys"}.difference(entry_gate.columns)
    if missing:
        raise ValueError(
            "entry gate missing columns: " + ", ".join(sorted(missing))
        )
    gate = entry_gate[["effective_month", "allow_new_buys"]].copy()
    gate["effective_month"] = pd.to_datetime(gate["effective_month"]).dt.to_period(
        "M"
    )
    if gate["effective_month"].duplicated().any():
        raise ValueError("entry gate contains duplicate effective months")
    return gate.set_index("effective_month")["allow_new_buys"].astype(bool)


def build_monthly_trend_gate(index_bars: pd.DataFrame) -> pd.DataFrame:
    """Return the fixed 10-month trend state effective in the following month.

    Each row uses only the last observed index close in the source calendar
    month. The state becomes effective on the first calendar day of the next
    month, so a month-end close can only affect later open executions.
    """
    missing = {"date", "close"}.difference(index_bars.columns)
    if missing:
        raise ValueError(
            "market regime bars missing columns: " + ", ".join(sorted(missing))
        )

    frame = index_bars[["date", "close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = (
        frame.sort_values("date")
        .drop_duplicates("date", keep="last")
        .assign(_month=lambda values: values["date"].dt.to_period("M"))
        .groupby("_month", sort=True, as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "effective_month",
                "gate_source_month_end",
                "gate_source_close",
                "gate_sma_10",
                "lookback_months",
                "allow_new_buys",
                *[
                    column
                    for lag in range(10)
                    for column in (f"lookback_{lag}_date", f"lookback_{lag}_close")
                ],
            ]
        )
    full_months = pd.period_range(
        frame["_month"].min(),
        frame["_month"].max(),
        freq="M",
    )
    frame = (
        frame.set_index("_month")
        .reindex(full_months)
        .rename_axis("_month")
        .reset_index()
    )
    frame["gate_sma_10"] = frame["close"].rolling(10, min_periods=10).mean()

    rows: list[dict[str, object]] = []
    for index, source in frame.iterrows():
        source_month = source["_month"]
        source_date = (
            pd.Timestamp(source["date"]).normalize()
            if pd.notna(source["date"])
            else pd.NaT
        )
        lookback = frame.iloc[max(0, index - 9) : index + 1].iloc[::-1]
        row: dict[str, object] = {
            "effective_month": (source_month + 1).to_timestamp(),
            "gate_source_month_end": source_date,
            "gate_source_close": (
                float(source["close"]) if pd.notna(source["close"]) else np.nan
            ),
            "gate_sma_10": (
                float(source["gate_sma_10"])
                if pd.notna(source["gate_sma_10"])
                else np.nan
            ),
            "lookback_months": int(lookback["close"].notna().sum()),
            "allow_new_buys": bool(
                pd.notna(source["gate_sma_10"])
                and float(source["close"]) > float(source["gate_sma_10"])
            ),
        }
        for lag in range(10):
            if lag < len(lookback):
                observation = lookback.iloc[lag]
                row[f"lookback_{lag}_date"] = (
                    pd.Timestamp(observation["date"]).normalize()
                    if pd.notna(observation["date"])
                    else pd.NaT
                )
                row[f"lookback_{lag}_close"] = (
                    float(observation["close"])
                    if pd.notna(observation["close"])
                    else np.nan
                )
            else:
                row[f"lookback_{lag}_date"] = pd.NaT
                row[f"lookback_{lag}_close"] = np.nan
        rows.append(row)

    return pd.DataFrame(rows)
