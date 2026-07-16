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

BASELINE_MODEL_FEATURES = (
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
ENGINEERED_MODEL_FEATURES = (
    "overnight_gap_mean_5",
    "close_location_mean_10",
    "trend_r2_20",
    "trend_residual_20",
    "downside_vol_20",
    "range_vol_10",
    "price_volume_corr_20",
    "turnover_shock_5_20",
)
MODEL_FEATURES = BASELINE_MODEL_FEATURES + ENGINEERED_MODEL_FEATURES
CONTROL_FEATURES = (
    "ep_missing",
    "bp_missing",
    "roe_missing",
    "board_star",
    "board_chinext",
)
BASELINE_MODEL_INPUT_FEATURES = tuple(
    f"rank_{feature}" for feature in BASELINE_MODEL_FEATURES
) + CONTROL_FEATURES
CANDIDATE_MODEL_INPUT_FEATURES = tuple(
    f"rank_{feature}" for feature in MODEL_FEATURES
) + CONTROL_FEATURES
MODEL_INPUT_FEATURES = CANDIDATE_MODEL_INPUT_FEATURES


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

    def rolling_mean(
        values: pd.Series, window: int, min_periods: int
    ) -> pd.Series:
        return values.groupby(frame["code"], sort=False).transform(
            lambda group: group.rolling(window, min_periods=min_periods).mean()
        )

    def rolling_pair_moments(
        left: pd.Series, right: pd.Series, window: int, min_periods: int
    ) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
        left_mean = rolling_mean(left, window, min_periods)
        right_mean = rolling_mean(right, window, min_periods)
        covariance = rolling_mean(left * right, window, min_periods) - (
            left_mean * right_mean
        )
        left_variance = (
            rolling_mean(left.pow(2.0), window, min_periods)
            - left_mean.pow(2.0)
        )
        right_variance = rolling_mean(
            right.pow(2.0), window, min_periods
        ) - right_mean.pow(2.0)
        return left_mean, right_mean, covariance, left_variance, right_variance

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
    previous_close = by_code["close"].shift(1)
    frame["overnight_1"] = frame["open"] / previous_close - 1.0
    frame["range_1"] = (frame["high"] - frame["low"]) / previous_close
    frame["close_location_1"] = (frame["close"] - frame["low"]) / (
        frame["high"] - frame["low"]
    ).replace(0.0, np.nan)
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
    frame["downside_vol_20"] = returns_by_code.transform(
        lambda values: values.clip(upper=0.0)
        .pow(2.0)
        .rolling(20, min_periods=15)
        .mean()
        .pow(0.5)
    )
    frame["overnight_gap_mean_5"] = rolling_mean(frame["overnight_1"], 5, 5)
    frame["range_vol_10"] = rolling_mean(frame["range_1"], 10, 8)
    frame["close_location_mean_10"] = rolling_mean(
        frame["close_location_1"], 10, 8
    )
    trend_time = frame["history_count"].astype(float)
    trend_price = frame["close"].astype(float)
    (
        trend_time_mean,
        trend_price_mean,
        trend_covariance,
        trend_time_variance,
        trend_price_variance,
    ) = rolling_pair_moments(trend_time, trend_price, 20, 15)
    trend_slope = trend_covariance / trend_time_variance.replace(0.0, np.nan)
    trend_fitted = trend_price_mean + trend_slope * (trend_time - trend_time_mean)
    frame["trend_residual_20"] = (
        trend_price - trend_fitted
    ) / trend_price.replace(0.0, np.nan)
    frame["trend_r2_20"] = (
        trend_covariance.pow(2.0)
        / (
            trend_time_variance.clip(lower=0.0)
            * trend_price_variance.clip(lower=0.0)
        ).replace(0.0, np.nan)
    ).clip(0.0, 1.0)
    log_amount_change = np.log1p(frame["amount"].clip(lower=0.0)).groupby(
        frame["code"], sort=False
    ).diff()
    _, _, price_volume_covariance, return_variance, amount_change_variance = (
        rolling_pair_moments(frame["ret_1"], log_amount_change, 20, 15)
    )
    frame["price_volume_corr_20"] = price_volume_covariance / np.sqrt(
        return_variance.clip(lower=0.0)
        * amount_change_variance.clip(lower=0.0)
    ).replace(0.0, np.nan)
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
    turnover_mean_5 = by_code["turnover_pct"].transform(
        lambda values: values.rolling(5, min_periods=5).mean()
    )
    frame["turnover_abnormal_20_250"] = turnover_mean_20 / turnover_mean_250 - 1.0
    frame["turnover_cv_20"] = turnover_std_20 / turnover_mean_20.replace(0.0, np.nan)
    frame["turnover_shock_5_20"] = (
        turnover_mean_5 / turnover_mean_20.replace(0.0, np.nan) - 1.0
    )

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
    path_opens = []
    for offset in range(2, 7):
        path_date_map = {
            market_dates[index]: market_dates[index + offset]
            for index in range(max(0, len(market_dates) - offset))
        }
        path_dates = frame["date"].map(path_date_map)
        path_keys = pd.MultiIndex.from_arrays([frame["code"], path_dates])
        path_opens.append(open_lookup.reindex(path_keys).to_numpy(dtype=float))
    path_open_frame = pd.DataFrame(np.column_stack(path_opens), index=frame.index)
    complete_path = path_open_frame.notna().all(axis=1)
    frame["path_open_mean_5"] = path_open_frame.mean(axis=1).where(complete_path)
    frame["target_path_5"] = frame["path_open_mean_5"] / frame["entry_open"] - 1.0
    frame["label_available_date"] = frame["_exit_date"]
    frame = frame.drop(columns=["_entry_date", "_exit_date", "_market_session"])
    if "industry" not in frame:
        frame["industry"] = "UNKNOWN"
    frame["industry"] = frame["industry"].fillna("UNKNOWN").astype(str)
    def add_neutralized_target(
        target_column: str, residual_column: str, rank_column: str
    ) -> None:
        frame[residual_column] = np.nan
        for _, indices in frame.groupby("date", sort=False).groups.items():
            valid = (
                frame.loc[indices, "eligible"]
                & frame.loc[indices, target_column].notna()
            )
            valid_indices = indices[valid]
            if len(valid_indices) == 0:
                continue
            size = frame.loc[valid_indices, "log_float_market_cap"].astype(float)
            size_std = size.std(ddof=0)
            size_control = (
                (size - size.mean()) / size_std if size_std > 0.0 else size * 0.0
            )
            industry_controls = pd.get_dummies(
                frame.loc[valid_indices, "industry"], dtype=float
            )
            design = np.column_stack(
                [industry_controls.to_numpy(), size_control.to_numpy()]
            )
            target = frame.loc[valid_indices, target_column].to_numpy(dtype=float)
            fitted = design @ np.linalg.lstsq(design, target, rcond=None)[0]
            frame.loc[valid_indices, residual_column] = target - fitted

        daily_target = frame.groupby("date", sort=False)[residual_column]
        target_ordinal = daily_target.rank(method="average")
        target_count = daily_target.transform("count")
        frame[rank_column] = np.where(
            frame[residual_column].notna(),
            np.where(
                target_count.gt(1),
                2.0 * (target_ordinal - 1.0) / (target_count - 1.0) - 1.0,
                0.0,
            ),
            np.nan,
        )

    add_neutralized_target("target_5", "target_residual", "target_rank")
    add_neutralized_target(
        "target_path_5", "target_path_residual", "target_path_rank"
    )

    use = frame["date"].ge(pd.Timestamp(start))
    if end is not None:
        use &= frame["date"].le(pd.Timestamp(end))
    return frame.loc[use].reset_index(drop=True)
