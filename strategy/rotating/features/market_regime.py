"""Point-in-time monthly market-state features for portfolio entry control."""

from __future__ import annotations

import numpy as np
import pandas as pd


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
    frame["gate_sma_10"] = frame["close"].rolling(10, min_periods=10).mean()

    rows: list[dict[str, object]] = []
    for index, source in frame.iterrows():
        source_date = pd.Timestamp(source["date"]).normalize()
        lookback = frame.iloc[max(0, index - 9) : index + 1].iloc[::-1]
        row: dict[str, object] = {
            "effective_month": (source_date.to_period("M") + 1).to_timestamp(),
            "gate_source_month_end": source_date,
            "gate_source_close": float(source["close"]),
            "gate_sma_10": (
                float(source["gate_sma_10"])
                if pd.notna(source["gate_sma_10"])
                else np.nan
            ),
            "lookback_months": int(len(lookback)),
            "allow_new_buys": bool(
                pd.notna(source["gate_sma_10"])
                and float(source["close"]) > float(source["gate_sma_10"])
            ),
        }
        for lag in range(10):
            if lag < len(lookback):
                observation = lookback.iloc[lag]
                row[f"lookback_{lag}_date"] = pd.Timestamp(
                    observation["date"]
                ).normalize()
                row[f"lookback_{lag}_close"] = float(observation["close"])
            else:
                row[f"lookback_{lag}_date"] = pd.NaT
                row[f"lookback_{lag}_close"] = np.nan
        rows.append(row)

    return pd.DataFrame(rows)
