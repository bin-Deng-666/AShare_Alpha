from __future__ import annotations

import datetime as dt

import pandas as pd

from strategy.rotating.model.walkforward import walk_forward_score


def test_walk_forward_score_ignores_labels_that_are_not_available_on_prediction_day() -> None:
    dates = pd.bdate_range("2024-01-02", periods=7)
    rows = []
    for date_index, date in enumerate(dates[:6]):
        for code_index, code in enumerate(("000001", "000002", "000003")):
            rows.append(
                {
                    "code": code,
                    "date": date,
                    "eligible": True,
                    "rank_ret_5": (code_index - 1) * 0.5 + date_index * 0.01,
                    "target_rank": (code_index - 1) * 0.4,
                    "label_available_date": (
                        dates[min(date_index + 1, 6)] if date_index < 4 else dates[6]
                    ),
                }
            )
    panel = pd.DataFrame(rows)
    poisoned = panel.copy()
    poisoned.loc[poisoned["date"].eq(dates[4]), "target_rank"] = [1000.0, -1000.0, 500.0]

    expected = walk_forward_score(
        panel,
        prediction_dates=[dates[5]],
        feature_columns=("rank_ret_5",),
        target_column="target_rank",
        min_train_rows=6,
        train_years=5,
        random_state=7,
    )
    actual = walk_forward_score(
        poisoned,
        prediction_dates=[dates[5]],
        feature_columns=("rank_ret_5",),
        target_column="target_rank",
        min_train_rows=6,
        train_years=5,
        random_state=7,
    )

    score_columns = ["factor_score", "elastic_score", "gbrt_score", "ensemble_score"]
    assert expected.sort_values("code")[score_columns].equals(
        actual.sort_values("code")[score_columns]
    )


def test_walk_forward_score_freezes_models_between_quarterly_retrains() -> None:
    codes = ("000001", "000002", "000003")
    rows = []
    for date, available in (
        (pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-10")),
        (pd.Timestamp("2024-02-01"), pd.Timestamp("2024-02-09")),
        (pd.Timestamp("2024-03-01"), pd.Timestamp("2024-03-11")),
        (pd.Timestamp("2024-04-15"), pd.Timestamp("2024-04-20")),
    ):
        for index, code in enumerate(codes):
            rows.append(
                {
                    "code": code,
                    "date": date,
                    "eligible": True,
                    "rank_ret_5": [-1.0, 0.0, 1.0][index],
                    "target_rank": [-0.5, 0.0, 0.5][index],
                    "label_available_date": available,
                }
            )
    for date in (pd.Timestamp("2024-04-10"), pd.Timestamp("2024-05-10")):
        for index, code in enumerate(codes):
            rows.append(
                {
                    "code": code,
                    "date": date,
                    "eligible": True,
                    "rank_ret_5": [-0.8, 0.1, 0.9][index],
                    "target_rank": None,
                    "label_available_date": None,
                }
            )
    panel = pd.DataFrame(rows)
    poisoned = panel.copy()
    poisoned.loc[poisoned["date"].eq(pd.Timestamp("2024-04-15")), "target_rank"] = [
        1000.0,
        -1000.0,
        500.0,
    ]

    expected = walk_forward_score(
        panel,
        prediction_dates=["2024-04-10", "2024-05-10"],
        feature_columns=("rank_ret_5",),
        target_column="target_rank",
        min_train_rows=6,
        train_years=5,
        random_state=11,
    )
    actual = walk_forward_score(
        poisoned,
        prediction_dates=["2024-04-10", "2024-05-10"],
        feature_columns=("rank_ret_5",),
        target_column="target_rank",
        min_train_rows=6,
        train_years=5,
        random_state=11,
    )

    score_columns = ["factor_score", "elastic_score", "gbrt_score", "ensemble_score"]
    expected_may = expected.loc[expected["date"].eq(pd.Timestamp("2024-05-10"))]
    actual_may = actual.loc[actual["date"].eq(pd.Timestamp("2024-05-10"))]
    assert expected_may.sort_values("code")[score_columns].equals(
        actual_may.sort_values("code")[score_columns]
    )


def test_walk_forward_score_keeps_control_features_out_of_fixed_factor_baseline() -> None:
    rows = []
    codes = ("000001", "000002", "000003")
    for date_string in ("2024-01-02", "2024-02-01", "2024-03-01"):
        date = pd.Timestamp(date_string)
        for index, code in enumerate(codes):
            rows.append(
                {
                    "code": code,
                    "date": date,
                    "eligible": True,
                    "rank_vol_20": [-1.0, 0.0, 1.0][index],
                    "rank_ep": [1.0, 0.0, -1.0][index],
                    "rank_ret_5": [2.0, -2.0, 1.0][index],
                    "target_rank": [-0.5, 0.0, 0.5][index],
                    "label_available_date": date + dt.timedelta(days=7),
                }
            )
    prediction_date = pd.Timestamp("2024-04-01")
    for index, code in enumerate(codes):
        rows.append(
            {
                "code": code,
                "date": prediction_date,
                "eligible": True,
                "rank_vol_20": [-1.0, 0.0, 1.0][index],
                "rank_ep": [1.0, 0.0, -1.0][index],
                "rank_ret_5": [2.0, -2.0, 1.0][index],
                "target_rank": None,
                "label_available_date": None,
            }
        )

    scores = walk_forward_score(
        pd.DataFrame(rows),
        prediction_dates=[prediction_date],
        feature_columns=("rank_vol_20", "rank_ep", "rank_ret_5"),
        target_column="target_rank",
        min_train_rows=6,
        train_years=5,
    ).sort_values("code")

    assert scores["factor_score"].tolist() == [1.0, 0.0, -1.0]


def test_walk_forward_score_can_fit_a_forward_interval_path_target() -> None:
    rows = []
    codes = ("000001", "000002", "000003")
    training_dates = pd.bdate_range("2024-01-02", periods=4)
    for date in training_dates:
        for index, code in enumerate(codes):
            rows.append(
                {
                    "code": code,
                    "date": date,
                    "eligible": True,
                    "rank_ret_5": [-1.0, 0.0, 1.0][index],
                    "target_rank": [-1.0, 0.0, 1.0][index],
                    "target_path_rank": [1.0, 0.0, -1.0][index],
                    "label_available_date": pd.Timestamp("2024-01-10"),
                }
            )
    prediction_date = pd.Timestamp("2024-02-01")
    for index, code in enumerate(codes):
        rows.append(
            {
                "code": code,
                "date": prediction_date,
                "eligible": True,
                "rank_ret_5": [-1.0, 0.0, 1.0][index],
                "target_rank": None,
                "target_path_rank": None,
                "label_available_date": None,
            }
        )
    panel = pd.DataFrame(rows)

    endpoint = walk_forward_score(
        panel,
        prediction_dates=[prediction_date],
        feature_columns=("rank_ret_5",),
        target_column="target_rank",
        min_train_rows=6,
        train_years=5,
    ).sort_values("code")
    path = walk_forward_score(
        panel,
        prediction_dates=[prediction_date],
        feature_columns=("rank_ret_5",),
        target_column="target_path_rank",
        min_train_rows=6,
        train_years=5,
    ).sort_values("code")

    assert endpoint["elastic_score"].is_monotonic_increasing
    assert path["elastic_score"].is_monotonic_decreasing
