"""Build the point-in-time factor panel used by the rotating strategy."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from data.scripts.corporate_action_status import (
    CorporateActionFormulaStatus,
    capabilities_for,
)
from data.scripts.price_rules import round_cny_price_to_cent as _cent_price


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
INDUSTRY_SIZE_MODEL_FEATURES = (
    "ep_industry_size_residual",
    "bp_industry_size_residual",
    "roe_industry_size_residual",
)
INDUSTRY_SIZE_MODEL_INPUT_FEATURES = MODEL_INPUT_FEATURES + tuple(
    f"rank_{feature}" for feature in INDUSTRY_SIZE_MODEL_FEATURES
)
PRICE_LIMIT_MODEL_FEATURES = (
    "limit_up_close_share_5",
    "limit_up_unsealed_share_5",
    "limit_down_close_share_5",
    "limit_down_unsealed_share_5",
)
PRICE_LIMIT_MODEL_INPUT_FEATURES = (
    MODEL_INPUT_FEATURES
    + PRICE_LIMIT_MODEL_FEATURES
    + ("limit_history_missing_5",)
)
ENDPOINT_RETURN_COLUMN = "target_5"
PATH_RETURN_COLUMN = "target_path_5"
ENDPOINT_RESIDUAL_COLUMN = "target_residual"
PATH_RESIDUAL_COLUMN = "target_path_residual"
ENDPOINT_TARGET_COLUMN = "target_rank"
PATH_TARGET_COLUMN = "target_path_rank"
MODEL_TARGET_COLUMN = ENDPOINT_TARGET_COLUMN


def _add_daily_cross_section_rank(frame: pd.DataFrame, feature: str) -> None:
    """Add the frozen winsorized ``[-1, 1]`` daily rank for one feature."""
    eligible = frame["eligible"].fillna(False).astype(bool)
    eligible_values = pd.to_numeric(frame[feature], errors="coerce").where(
        eligible
    )
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


def add_industry_size_residual_features(
    source: pd.DataFrame,
    *,
    min_industry_observations: int = 5,
    copy: bool = True,
) -> pd.DataFrame:
    """Add the preregistered PIT industry/size-relative value-quality bundle."""
    required = {
        "date",
        "industry",
        "eligible",
        "log_float_market_cap",
        "ep",
        "bp",
        "roe",
    }
    missing = required.difference(source.columns)
    if missing:
        raise ValueError(
            "industry-size feature source missing columns: "
            + ", ".join(sorted(missing))
        )
    if min_industry_observations < 2:
        raise ValueError("min_industry_observations must be at least 2")

    frame = source.copy() if copy else source
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    industry = frame["industry"].astype("string").str.strip()
    normalized_industry = industry.mask(
        industry.isna() | industry.eq(""), "UNKNOWN"
    ).astype(str)
    size = pd.to_numeric(frame["log_float_market_cap"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    eligible = frame["eligible"].fillna(False).astype(bool)
    known_industry = normalized_industry.ne("UNKNOWN")
    industry_keys = [frame["date"], normalized_industry]

    ep = pd.to_numeric(frame["ep"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    bp = pd.to_numeric(frame["bp"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    raw_inputs = {
        "ep_industry_size_residual": np.log(ep.where(ep.gt(0.0))),
        "bp_industry_size_residual": np.log(bp.where(bp.gt(0.0))),
        "roe_industry_size_residual": pd.to_numeric(
            frame["roe"], errors="coerce"
        ).replace([np.inf, -np.inf], np.nan),
    }
    for output, raw_values in raw_inputs.items():
        # The frozen formula estimates 1/99 cutoffs across every eligible,
        # non-missing raw observation. Industry and size exclusions are
        # deliberately applied only after those cutoffs are fixed.
        eligible_values = raw_values.where(eligible)
        daily = eligible_values.groupby(frame["date"], sort=False)
        lower = daily.transform(lambda group: group.quantile(0.01))
        upper = daily.transform(lambda group: group.quantile(0.99))
        winsorized = eligible_values.clip(lower=lower, upper=upper)
        industry_values = winsorized.where(known_industry & size.notna())

        industry_count = industry_values.groupby(
            industry_keys, sort=False
        ).transform("count")
        valid = industry_values.notna() & industry_count.ge(
            min_industry_observations
        )
        response = industry_values.where(valid)
        valid_size = size.where(valid)

        # Frisch-Waugh-Lovell: demeaning both variables within industry and
        # fitting one common size slope is exactly the registered one-hot +
        # size OLS. Global z-scoring of size changes the coefficient scale,
        # not the fitted values or residuals.
        response_demeaned = response - response.groupby(
            industry_keys, sort=False
        ).transform("mean")
        size_demeaned = valid_size - valid_size.groupby(
            industry_keys, sort=False
        ).transform("mean")
        covariance_numerator = (response_demeaned * size_demeaned).groupby(
            frame["date"], sort=False
        ).transform("sum")
        size_denominator = size_demeaned.pow(2.0).groupby(
            frame["date"], sort=False
        ).transform("sum")
        size_slope = covariance_numerator / size_denominator.replace(0.0, np.nan)
        frame[output] = (
            response_demeaned - size_slope.fillna(0.0) * size_demeaned
        ).where(valid)
        _add_daily_cross_section_rank(frame, output)

    return frame


def add_price_limit_state_features(
    source: pd.DataFrame,
    *,
    market_dates: pd.DatetimeIndex | None = None,
    copy: bool = True,
) -> pd.DataFrame:
    """Add the preregistered five-market-session price-limit state bundle."""
    required = {
        "code",
        "date",
        "trade_status",
        "limit_up",
        "limit_down",
        "is_limit_up",
        "is_limit_down",
        "raw_high",
        "raw_low",
        "raw_close",
        "limit_validation_status",
    }
    missing = required.difference(source.columns)
    if missing:
        raise ValueError(
            "price-limit feature source missing columns: "
            + ", ".join(sorted(missing))
        )

    frame = source.copy() if copy else source
    normalized_dates = pd.to_datetime(frame["date"]).dt.normalize()
    normalized_codes = frame["code"].astype(str).str.zfill(6)
    duplicate = pd.DataFrame(
        {"code": normalized_codes, "date": normalized_dates}
    ).duplicated(["code", "date"], keep=False)
    if duplicate.any():
        raise ValueError("price-limit feature source contains duplicate code/date rows")

    if market_dates is None:
        calendar = pd.DatetimeIndex(normalized_dates.dropna().unique()).sort_values()
    else:
        calendar = pd.DatetimeIndex(pd.to_datetime(market_dates)).normalize().unique()
        calendar = calendar.sort_values()
    if calendar.empty:
        raise ValueError("price-limit feature market calendar is empty")
    session_lookup = pd.Series(np.arange(len(calendar), dtype=int), index=calendar)
    sessions = normalized_dates.map(session_lookup)
    if sessions.isna().any():
        raise ValueError("price-limit feature rows fall outside the market calendar")

    trade_raw = frame["trade_status"]
    trade_known = trade_raw.notna()
    traded = trade_raw.fillna(False).astype(bool)
    limit_up = pd.to_numeric(frame["limit_up"], errors="coerce")
    limit_down = pd.to_numeric(frame["limit_down"], errors="coerce")
    raw_high = pd.to_numeric(frame["raw_high"], errors="coerce")
    raw_low = pd.to_numeric(frame["raw_low"], errors="coerce")
    raw_close = pd.to_numeric(frame["raw_close"], errors="coerce")
    validation_status = frame["limit_validation_status"].astype("string")
    close_up_known = frame["is_limit_up"].notna()
    close_down_known = frame["is_limit_down"].notna()
    close_up = frame["is_limit_up"].fillna(False).astype(bool) & traded
    close_down = frame["is_limit_down"].fillna(False).astype(bool) & traded
    necessary_fields = (
        limit_up.notna()
        & limit_down.notna()
        & raw_high.notna()
        & raw_low.notna()
        & raw_close.notna()
        & close_up_known
        & close_down_known
        & validation_status.eq("valid").fillna(False)
    )
    observation_valid = trade_known & (~traded | necessary_fields)
    raw_high_cent = _cent_price(raw_high)
    raw_low_cent = _cent_price(raw_low)
    raw_close_cent = _cent_price(raw_close)
    limit_up_cent = _cent_price(limit_up)
    limit_down_cent = _cent_price(limit_down)
    touch_up = traded & limit_up.notna() & raw_high_cent.eq(limit_up_cent)
    touch_down = traded & limit_down.notna() & raw_low_cent.eq(limit_down_cent)
    boundary_inconsistent = observation_valid & (
        raw_high_cent.gt(limit_up_cent) | raw_low_cent.lt(limit_down_cent)
    )
    derived_close_up = traded & limit_up.notna() & raw_close_cent.eq(limit_up_cent)
    derived_close_down = (
        traded & limit_down.notna() & raw_close_cent.eq(limit_down_cent)
    )
    flag_inconsistent = observation_valid & (
        close_up.ne(derived_close_up) | close_down.ne(derived_close_down)
    )
    invalid_close_up = observation_valid & close_up & ~touch_up
    invalid_close_down = observation_valid & close_down & ~touch_down
    if (
        boundary_inconsistent.any()
        or flag_inconsistent.any()
        or invalid_close_up.any()
        or invalid_close_down.any()
    ):
        raise ValueError(
            "price-limit validation is inconsistent with raw OHLC and limit price"
        )

    working = pd.DataFrame(
        {
            "_position": np.arange(len(frame), dtype=int),
            "code": normalized_codes.to_numpy(),
            "date": normalized_dates.to_numpy(),
            "session": sessions.to_numpy(dtype=int),
            "valid": observation_valid.to_numpy(dtype=np.int8),
            "limit_up_close_share_5": close_up.to_numpy(dtype=np.int8),
            "limit_up_unsealed_share_5": (touch_up & ~close_up).to_numpy(
                dtype=np.int8
            ),
            "limit_down_close_share_5": close_down.to_numpy(dtype=np.int8),
            "limit_down_unsealed_share_5": (touch_down & ~close_down).to_numpy(
                dtype=np.int8
            ),
        }
    ).sort_values(["code", "date", "_position"])
    by_code = working.groupby("code", sort=False)
    five_rows_are_consecutive = (
        working["session"] - by_code["session"].shift(4)
    ).eq(4)
    five_rows_are_valid = by_code["valid"].transform(
        lambda values: values.rolling(5, min_periods=5).sum()
    ).eq(5)
    complete = five_rows_are_consecutive & five_rows_are_valid
    for feature in PRICE_LIMIT_MODEL_FEATURES:
        rolling_sum = by_code[feature].transform(
            lambda values: values.rolling(5, min_periods=5).sum()
        )
        working[feature] = (rolling_sum / 5.0).where(complete)
    working["limit_history_missing_5"] = (~complete).astype("int8")

    restored = working.sort_values("_position")
    for feature in (*PRICE_LIMIT_MODEL_FEATURES, "limit_history_missing_5"):
        frame[feature] = restored[feature].to_numpy()
    return frame


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
    include_industry_size_features: bool = False,
    include_price_limit_features: bool = False,
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
    raw_execution_columns = {"raw_open", "raw_high", "raw_low"}
    has_limit_observations = any(
        column in frame and frame[column].notna().any()
        for column in ("limit_up", "limit_down")
    ) or any(
        column in frame and frame[column].fillna(False).astype(bool).any()
        for column in ("is_limit_up", "is_limit_down")
    )
    missing_raw_execution = raw_execution_columns.difference(frame.columns)
    if has_limit_observations and missing_raw_execution:
        raise ValueError(
            "raw OHLC columns are required when applying raw exchange limits: "
            + ", ".join(sorted(missing_raw_execution))
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
        frame["high"] - frame["low"] + 1e-12
    )
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
        .rolling(20, min_periods=20)
        .mean()
        .pow(0.5)
    )
    frame["overnight_gap_mean_5"] = rolling_mean(frame["overnight_1"], 5, 5)
    frame["range_vol_10"] = rolling_mean(frame["range_1"], 10, 10)
    frame["close_location_mean_10"] = rolling_mean(
        frame["close_location_1"], 10, 10
    )
    trend_time = frame["history_count"].astype(float)
    trend_price = frame["close"].astype(float)
    (
        trend_time_mean,
        trend_price_mean,
        trend_covariance,
        trend_time_variance,
        trend_price_variance,
    ) = rolling_pair_moments(trend_time, trend_price, 20, 20)
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
        rolling_pair_moments(frame["ret_1"], log_amount_change, 20, 20)
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
    turnover_mean_20_complete = by_code["turnover_pct"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    frame["turnover_abnormal_20_250"] = turnover_mean_20 / turnover_mean_250 - 1.0
    frame["turnover_cv_20"] = turnover_std_20 / turnover_mean_20.replace(0.0, np.nan)
    frame["turnover_shock_5_20"] = (
        turnover_mean_5 / turnover_mean_20_complete.replace(0.0, np.nan) - 1.0
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
    if "industry" not in frame:
        frame["industry"] = "UNKNOWN"
    frame["industry"] = frame["industry"].fillna("UNKNOWN").astype(str)
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
    if include_industry_size_features:
        frame = add_industry_size_residual_features(frame, copy=False)
    if include_price_limit_features:
        frame = add_price_limit_state_features(
            frame,
            market_dates=market_dates,
            copy=False,
        )
    for feature in MODEL_FEATURES:
        _add_daily_cross_section_rank(frame, feature)
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
    # Returns use adjusted opens, while exchange limits and execution use raw
    # OHLC. The catalog exposes both bases so labels mirror ``run_portfolio``.
    raw_open = pd.to_numeric(frame.get("raw_open", frame["open"]), errors="coerce")
    raw_high = pd.to_numeric(frame.get("raw_high", frame["high"]), errors="coerce")
    raw_low = pd.to_numeric(frame.get("raw_low", frame["low"]), errors="coerce")
    limit_up = pd.to_numeric(
        frame["limit_up"]
        if "limit_up" in frame
        else pd.Series(np.nan, index=frame.index),
        errors="coerce",
    )
    limit_down = pd.to_numeric(
        frame["limit_down"]
        if "limit_down" in frame
        else pd.Series(np.nan, index=frame.index),
        errors="coerce",
    )
    raw_open_cent = _cent_price(raw_open)
    raw_high_cent = _cent_price(raw_high)
    raw_low_cent = _cent_price(raw_low)
    limit_up_cent = _cent_price(limit_up)
    limit_down_cent = _cent_price(limit_down)
    one_price = raw_high_cent.eq(raw_low_cent) & raw_open_cent.eq(raw_high_cent)
    is_limit_up = frame.get(
        "is_limit_up", pd.Series(False, index=frame.index)
    ).fillna(False).astype(bool)
    is_limit_down = frame.get(
        "is_limit_down", pd.Series(False, index=frame.index)
    ).fillna(False).astype(bool)
    open_is_limit_up = is_limit_up | raw_open_cent.eq(limit_up_cent)
    open_is_limit_down = is_limit_down | raw_open_cent.eq(limit_down_cent)
    trade_status = frame["trade_status"].fillna(False).astype(bool)
    formula_status = frame.get(
        "corporate_action_formula_status",
        pd.Series(CorporateActionFormulaStatus.NONE.value, index=frame.index),
    ).fillna(CorporateActionFormulaStatus.NONE.value).astype(str)
    supported_execution = formula_status.map(
        lambda status: capabilities_for(status).execution_supported
    )
    supported_label = formula_status.map(
        lambda status: capabilities_for(status).label_supported
    )
    frame["_can_buy_open"] = (
        trade_status
        & supported_execution
        & frame["open"].notna()
        & raw_open.notna()
        & ~(one_price & open_is_limit_up)
    )
    frame["_can_sell_open"] = (
        trade_status
        & supported_execution
        & frame["open"].notna()
        & raw_open.notna()
        & ~(one_price & open_is_limit_down)
    )
    can_buy_lookup = frame.set_index(["code", "date"])["_can_buy_open"]
    can_sell_lookup = frame.set_index(["code", "date"])["_can_sell_open"]
    invalidation_lookup = pd.Series(
        (~supported_label).to_numpy(),
        index=pd.MultiIndex.from_arrays([frame["code"], frame["date"]]),
    )
    entry_keys = pd.MultiIndex.from_arrays([frame["code"], frame["_entry_date"]])
    exit_keys = pd.MultiIndex.from_arrays([frame["code"], frame["_exit_date"]])
    frame["entry_open"] = open_lookup.reindex(entry_keys).to_numpy()
    frame["exit_open_5"] = open_lookup.reindex(exit_keys).to_numpy()
    entry_executable = (
        can_buy_lookup.reindex(entry_keys)
        .astype("boolean")
        .fillna(False)
        .to_numpy(dtype=bool)
    )
    path_opens = []
    path_executable = []
    path_requires_invalidation = [
        invalidation_lookup.reindex(entry_keys)
        .astype("boolean")
        .fillna(False)
        .to_numpy(dtype=bool)
    ]
    for offset in range(2, 7):
        path_date_map = {
            market_dates[index]: market_dates[index + offset]
            for index in range(max(0, len(market_dates) - offset))
        }
        path_dates = frame["date"].map(path_date_map)
        path_keys = pd.MultiIndex.from_arrays([frame["code"], path_dates])
        path_opens.append(open_lookup.reindex(path_keys).to_numpy(dtype=float))
        path_executable.append(
            can_sell_lookup.reindex(path_keys)
            .astype("boolean")
            .fillna(False)
            .to_numpy(dtype=bool)
        )
        path_requires_invalidation.append(
            invalidation_lookup.reindex(path_keys)
            .astype("boolean")
            .fillna(False)
            .to_numpy(dtype=bool)
        )
    path_open_frame = pd.DataFrame(np.column_stack(path_opens), index=frame.index)
    path_execution_frame = pd.DataFrame(
        np.column_stack(path_executable), index=frame.index
    )
    complete_path = (
        path_open_frame.notna().all(axis=1)
        & path_execution_frame.all(axis=1)
        & entry_executable
    )
    frame["label_path_has_unsupported_action"] = np.column_stack(
        path_requires_invalidation
    ).any(axis=1)
    frame[ENDPOINT_RETURN_COLUMN] = (
        frame["exit_open_5"] / frame["entry_open"] - 1.0
    ).where(complete_path)
    frame["path_open_mean_5"] = path_open_frame.mean(axis=1).where(complete_path)
    frame[PATH_RETURN_COLUMN] = (
        frame["path_open_mean_5"] / frame["entry_open"] - 1.0
    )
    frame["label_available_date"] = frame["_exit_date"]
    frame = frame.drop(
        columns=[
            "_entry_date",
            "_exit_date",
            "_market_session",
            "_can_buy_open",
            "_can_sell_open",
        ]
    )
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

    add_neutralized_target(
        ENDPOINT_RETURN_COLUMN, ENDPOINT_RESIDUAL_COLUMN, ENDPOINT_TARGET_COLUMN
    )
    add_neutralized_target(
        PATH_RETURN_COLUMN, PATH_RESIDUAL_COLUMN, PATH_TARGET_COLUMN
    )

    use = frame["date"].ge(pd.Timestamp(start))
    if end is not None:
        use &= frame["date"].le(pd.Timestamp(end))
    return frame.loc[use].reset_index(drop=True)
