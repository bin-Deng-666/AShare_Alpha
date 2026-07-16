"""Build the point-in-time factor panel used by the rotating strategy."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {
    "code",
    "date",
    "open",
    "high",
    "low",
    "close",
    "amount",
    "turnover_pct",
    "float_market_cap",
    "is_st",
    "trade_status",
}

MODEL_FEATURES = (
    "ret_5",
    "ret_20",
    "ret_60",
    "mom_20_5",
    "mom_120_20",
    "reversal_20",
    "vol_20",
    "vol_60",
    "maxret_20",
    "drawdown_60",
    "trend_quality_60",
    "turnover_abnormal_20_250",
    "turnover_cv_20",
    "amount_ratio_5_20",
    "amount_ratio_20_60",
    "amount_cv_20",
    "amihud_20",
    "adv_20",
    "ep",
    "bp",
    "roe",
    "log_float_market_cap",
)
MODEL_INPUT_FEATURES = tuple(f"rank_{feature}" for feature in MODEL_FEATURES) + (
    "ep_missing",
    "bp_missing",
    "roe_missing",
    "board_star",
    "board_chinext",
)


def build_factor_panel(
    start: str,
    end: str | None = None,
    *,
    source: pd.DataFrame | str | Path,
    min_history: int = 250,
    min_listing_sessions: int = 120,
    min_tradable_observations: int | None = None,
    min_median_amount: float = 50_000_000.0,
    min_float_market_cap: float = 2_000_000_000.0,
) -> pd.DataFrame:
    """Return signal rows after computing rolling fields on the full history."""
    if isinstance(source, pd.DataFrame):
        frame = source.copy()
    else:
        frame = pd.read_parquet(Path(source))
    missing = REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"factor source missing columns: {', '.join(sorted(missing))}")

    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["board_star"] = frame["code"].str.startswith(("688", "689")).astype("int8")
    frame["board_chinext"] = frame["code"].str.startswith(("300", "301")).astype("int8")
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame = (
        frame.sort_values(["code", "date"])
        .drop_duplicates(["code", "date"], keep="last")
        .reset_index(drop=True)
    )
    market_dates = pd.DatetimeIndex(sorted(frame["date"].unique()))
    market_session = pd.Series(range(len(market_dates)), index=market_dates)
    frame["_market_session"] = frame["date"].map(market_session).astype(int)
    by_code = frame.groupby("code", sort=False)
    frame["history_count"] = by_code.cumcount() + 1
    frame["tradable_observations_20"] = 0
    for _, indices in by_code.groups.items():
        sessions = frame.loc[indices, "_market_session"].to_numpy(dtype=int)
        tradable = (
            frame.loc[indices, "trade_status"].fillna(False).astype(bool).to_numpy(dtype=int)
        )
        cumulative = np.concatenate(([0], np.cumsum(tradable)))
        starts = np.searchsorted(sessions, sessions - 19, side="left")
        frame.loc[indices, "tradable_observations_20"] = (
            cumulative[np.arange(len(indices)) + 1] - cumulative[starts]
        )
    frame["ret_1"] = by_code["close"].pct_change(fill_method=None)
    frame["ret_5"] = frame["close"] / by_code["close"].shift(5) - 1.0
    frame["ret_20"] = frame["close"] / by_code["close"].shift(20) - 1.0
    frame["ret_60"] = frame["close"] / by_code["close"].shift(60) - 1.0
    frame["mom_20_5"] = by_code["close"].shift(5) / by_code["close"].shift(20) - 1.0
    frame["mom_120_20"] = by_code["close"].shift(20) / by_code["close"].shift(120) - 1.0
    frame["reversal_20"] = -frame["ret_20"]

    returns_by_code = frame.groupby("code", sort=False)["ret_1"]
    frame["vol_20"] = returns_by_code.transform(
        lambda values: values.rolling(20, min_periods=15).std()
    )
    frame["vol_60"] = returns_by_code.transform(
        lambda values: values.rolling(60, min_periods=40).std()
    )
    frame["maxret_20"] = returns_by_code.transform(
        lambda values: values.rolling(20, min_periods=15).max()
    )
    high_60 = by_code["close"].transform(
        lambda values: values.rolling(60, min_periods=40).max()
    )
    frame["drawdown_60"] = frame["close"] / high_60 - 1.0
    frame["trend_quality_60"] = frame["ret_60"] / (
        frame["vol_60"] * np.sqrt(60.0)
    ).clip(lower=1e-6)

    turnover_mean_20 = by_code["turnover_pct"].transform(
        lambda values: values.rolling(20, min_periods=15).mean()
    )
    turnover_mean_250 = by_code["turnover_pct"].transform(
        lambda values: values.rolling(250, min_periods=min(120, min_history)).mean()
    )
    turnover_std_20 = by_code["turnover_pct"].transform(
        lambda values: values.rolling(20, min_periods=15).std()
    )
    frame["turnover_abnormal_20_250"] = turnover_mean_20 / turnover_mean_250 - 1.0
    frame["turnover_cv_20"] = turnover_std_20 / turnover_mean_20.replace(0.0, np.nan)

    frame["amount_mean_5"] = by_code["amount"].transform(
        lambda values: values.rolling(5, min_periods=min(5, min_history)).mean()
    )
    amount_mean_20 = by_code["amount"].transform(
        lambda values: values.rolling(20, min_periods=15).mean()
    )
    amount_mean_60 = by_code["amount"].transform(
        lambda values: values.rolling(60, min_periods=40).mean()
    )
    amount_std_20 = by_code["amount"].transform(
        lambda values: values.rolling(20, min_periods=15).std()
    )
    frame["amount_ratio_5_20"] = np.log1p(frame["amount_mean_5"]) - np.log1p(
        amount_mean_20
    )
    frame["amount_ratio_20_60"] = np.log1p(amount_mean_20) - np.log1p(
        amount_mean_60
    )
    frame["amount_cv_20"] = amount_std_20 / amount_mean_20.replace(0.0, np.nan)
    frame["adv_20"] = amount_mean_20
    frame["amihud_1"] = frame["ret_1"].abs() / frame["amount"].clip(lower=1.0) * 1e9
    frame["amihud_20"] = frame.groupby("code", sort=False)["amihud_1"].transform(
        lambda values: values.rolling(20, min_periods=15).mean()
    )
    frame["amount_median_20"] = by_code["amount"].transform(
        lambda values: values.rolling(20, min_periods=min(20, min_history)).median()
    )
    pe_ttm = pd.to_numeric(
        frame["pe_ttm"] if "pe_ttm" in frame else pd.Series(np.nan, index=frame.index),
        errors="coerce",
    )
    frame["ep"] = (1.0 / pe_ttm).where(pe_ttm.gt(0.0))
    frame["ep_missing"] = frame["ep"].isna().astype("int8")
    pb_mrq = pd.to_numeric(
        frame["pb_mrq"] if "pb_mrq" in frame else pd.Series(np.nan, index=frame.index),
        errors="coerce",
    )
    frame["bp"] = (1.0 / pb_mrq).where(pb_mrq.gt(0.0))
    frame["bp_missing"] = frame["bp"].isna().astype("int8")
    frame["roe"] = pd.to_numeric(
        frame["roe"] if "roe" in frame else pd.Series(np.nan, index=frame.index),
        errors="coerce",
    )
    frame["roe_missing"] = frame["roe"].isna().astype("int8")
    frame["log_float_market_cap"] = np.log(
        frame["float_market_cap"].where(frame["float_market_cap"].gt(0.0))
    )
    required_tradable = (
        min(15, min_history)
        if min_tradable_observations is None
        else int(min_tradable_observations)
    )
    frame["eligible"] = (
        frame["trade_status"].fillna(False).astype(bool)
        & ~frame["is_st"].fillna(False).astype(bool)
        & frame["float_market_cap"].ge(min_float_market_cap)
        & frame["amount_median_20"].ge(min_median_amount)
        & frame["tradable_observations_20"].ge(required_tradable)
        & frame["history_count"].ge(min_history)
        & frame["history_count"].ge(min_listing_sessions)
    )
    for feature in MODEL_FEATURES:
        eligible_values = frame[feature].where(frame["eligible"])
        daily = eligible_values.groupby(frame["date"], sort=False)
        lower = daily.transform(lambda values: values.quantile(0.01))
        upper = daily.transform(lambda values: values.quantile(0.99))
        winsorized = eligible_values.clip(lower=lower, upper=upper)
        ordinal = winsorized.groupby(frame["date"], sort=False).rank(method="average")
        count = daily.transform("count")
        frame[f"rank_{feature}"] = np.where(
            winsorized.notna(),
            np.where(count.gt(1), 2.0 * (ordinal - 1.0) / (count - 1.0) - 1.0, 0.0),
            np.nan,
        )
    entry_date_map = {
        market_dates[index]: market_dates[index + 1]
        for index in range(max(0, len(market_dates) - 1))
    }
    exit_date_map = {
        market_dates[index]: market_dates[index + 6]
        for index in range(max(0, len(market_dates) - 6))
    }
    frame["_entry_date"] = frame["date"].map(entry_date_map)
    frame["_exit_date"] = frame["date"].map(exit_date_map)
    open_lookup = frame.set_index(["code", "date"])["open"]
    entry_keys = pd.MultiIndex.from_arrays([frame["code"], frame["_entry_date"]])
    exit_keys = pd.MultiIndex.from_arrays([frame["code"], frame["_exit_date"]])
    frame["entry_open"] = open_lookup.reindex(entry_keys).to_numpy()
    frame["exit_open_5"] = open_lookup.reindex(exit_keys).to_numpy()
    frame["target_5"] = frame["exit_open_5"] / frame["entry_open"] - 1.0
    frame["label_available_date"] = frame["_exit_date"]
    frame = frame.drop(columns=["_entry_date", "_exit_date", "_market_session"])
    if "industry" not in frame:
        frame["industry"] = "UNKNOWN"
    frame["industry"] = frame["industry"].fillna("UNKNOWN").astype(str)
    frame["target_residual"] = np.nan
    for _, indices in frame.groupby("date", sort=False).groups.items():
        valid = frame.loc[indices, "eligible"] & frame.loc[indices, "target_5"].notna()
        valid_indices = indices[valid]
        if len(valid_indices) == 0:
            continue
        size = frame.loc[valid_indices, "log_float_market_cap"].astype(float)
        size_std = size.std(ddof=0)
        size_control = (size - size.mean()) / size_std if size_std > 0.0 else size * 0.0
        industry_controls = pd.get_dummies(
            frame.loc[valid_indices, "industry"], dtype=float
        )
        design = np.column_stack([industry_controls.to_numpy(), size_control.to_numpy()])
        target = frame.loc[valid_indices, "target_5"].to_numpy(dtype=float)
        fitted = design @ np.linalg.lstsq(design, target, rcond=None)[0]
        frame.loc[valid_indices, "target_residual"] = target - fitted

    daily_target = frame.groupby("date", sort=False)["target_residual"]
    target_ordinal = daily_target.rank(method="average")
    target_count = daily_target.transform("count")
    frame["target_rank"] = np.where(
        frame["target_residual"].notna(),
        np.where(
            target_count.gt(1),
            2.0 * (target_ordinal - 1.0) / (target_count - 1.0) - 1.0,
            0.0,
        ),
        np.nan,
    )

    use = frame["date"].ge(pd.Timestamp(start))
    if end is not None:
        use &= frame["date"].le(pd.Timestamp(end))
    return frame.loc[use].reset_index(drop=True)
