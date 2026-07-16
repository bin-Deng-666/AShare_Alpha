"""Leakage-safe rolling factor, Elastic Net, and shallow GBRT scores."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import ElasticNet

from strategy.rotating.features.build_panel import MODEL_INPUT_FEATURES


FACTOR_DIRECTIONS = {
    "rank_reversal_20": 1.0,
    "rank_mom_120_20": 1.0,
    "rank_vol_20": -1.0,
    "rank_vol_60": -1.0,
    "rank_maxret_20": -1.0,
    "rank_turnover_abnormal_20_250": -1.0,
    "rank_turnover_cv_20": -1.0,
    "rank_amount_cv_20": -1.0,
    "rank_ep": 1.0,
    "rank_bp": 1.0,
    "rank_roe": 1.0,
}


def walk_forward_score(
    panel: pd.DataFrame,
    *,
    prediction_dates: Iterable[pd.Timestamp | str] | None = None,
    feature_columns: Sequence[str] | None = None,
    target_column: str = "target_rank",
    min_train_rows: int = 2_000,
    train_years: int = 3,
    random_state: int = 17,
) -> pd.DataFrame:
    """Fit only fully realized labels and score each requested cross-section."""
    frame = panel.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame["label_available_date"] = pd.to_datetime(
        frame["label_available_date"]
    ).dt.normalize()
    features = list(feature_columns or MODEL_INPUT_FEATURES)
    required = {
        "code",
        "date",
        "eligible",
        target_column,
        "label_available_date",
        *features,
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"walk-forward panel missing columns: {', '.join(sorted(missing))}")

    if prediction_dates is None:
        dates = sorted(frame.loc[frame["eligible"].astype(bool), "date"].unique())
    else:
        dates = sorted(pd.Timestamp(value).normalize() for value in prediction_dates)

    outputs = []
    quarterly_models = {}
    for prediction_date in dates:
        predict = frame.loc[
            frame["eligible"].astype(bool) & frame["date"].eq(prediction_date)
        ].copy()
        if predict.empty:
            continue
        quarter = prediction_date.to_period("Q")
        if quarter not in quarterly_models:
            train_start = prediction_date - pd.DateOffset(years=train_years)
            train = frame.loc[
                frame["eligible"].astype(bool)
                & frame["date"].lt(prediction_date)
                & frame["date"].ge(train_start)
                & frame["label_available_date"].le(prediction_date)
                & frame[target_column].notna()
            ].copy()
            if len(train) < min_train_rows:
                raise ValueError(
                    f"prediction date {prediction_date.date()} has {len(train)} training rows; "
                    f"requires {min_train_rows}"
                )

            x_train = train[features].replace([np.inf, -np.inf], np.nan).fillna(0.0)
            y_train = train[target_column].astype(float)
            elastic = ElasticNet(
                alpha=0.0005,
                l1_ratio=0.1,
                max_iter=5_000,
                random_state=random_state,
            ).fit(x_train, y_train)
            leaf_rows = max(2, min(300, len(train) // 20))
            gbrt = HistGradientBoostingRegressor(
                learning_rate=0.05,
                max_iter=120,
                max_leaf_nodes=15,
                max_depth=3,
                min_samples_leaf=leaf_rows,
                l2_regularization=2.0,
                early_stopping=False,
                random_state=random_state,
            ).fit(x_train, y_train)
            quarterly_models[quarter] = (
                elastic,
                gbrt,
                len(train),
                train["label_available_date"].max(),
            )

        elastic, gbrt, training_rows, max_label_date = quarterly_models[quarter]
        x_predict = predict[features].replace([np.inf, -np.inf], np.nan).fillna(0.0)

        factor_features = [feature for feature in features if feature in FACTOR_DIRECTIONS]
        if not factor_features:
            factor_features = [
                feature for feature in features if not feature.endswith("_missing")
            ]
        weighted = pd.DataFrame(
            {
                feature: x_predict[feature] * FACTOR_DIRECTIONS.get(feature, 1.0)
                for feature in factor_features
            },
            index=predict.index,
        )
        predict["factor_score"] = weighted.mean(axis=1)
        predict["elastic_score"] = elastic.predict(x_predict)
        predict["gbrt_score"] = gbrt.predict(x_predict)
        predict["ensemble_score"] = predict[
            ["factor_score", "elastic_score", "gbrt_score"]
        ].mean(axis=1)
        predict["training_rows"] = training_rows
        predict["train_max_label_available_date"] = max_label_date
        predict["model_target_column"] = target_column
        outputs.append(
            predict[
                [
                    "code",
                    "date",
                    "factor_score",
                    "elastic_score",
                    "gbrt_score",
                    "ensemble_score",
                    "training_rows",
                    "train_max_label_available_date",
                    "model_target_column",
                ]
            ]
        )

    if not outputs:
        return pd.DataFrame(
            columns=[
                "code",
                "date",
                "factor_score",
                "elastic_score",
                "gbrt_score",
                "ensemble_score",
                "training_rows",
                "train_max_label_available_date",
                "model_target_column",
            ]
        )
    return pd.concat(outputs, ignore_index=True)
