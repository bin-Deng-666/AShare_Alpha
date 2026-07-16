"""Adapter: rotating daily picks."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

from utils.registry import register

STRATEGY_NAME = "rotating"
STRATEGY_ROOT = Path(__file__).resolve().parents[2] / "strategy" / "rotating"

PICK_FIELDS = [
    "code",
    "name",
    "action",
    "score",
    "industry_name",
    "board",
    "composite_score",
    "position_mult",
    "n_max",
]


def _ensure_strategy_path() -> None:
    root = str(STRATEGY_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _row_to_dict(row: pd.Series, reason: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in PICK_FIELDS:
        if k not in row.index:
            out[k] = None
            continue
        v = row[k]
        if hasattr(v, "item"):
            try:
                v = v.item()
            except (ValueError, AttributeError):
                pass
        if isinstance(v, float) and v != v:
            v = None
        if k == "code" and v is not None:
            v = str(v).zfill(6)
        out[k] = v
    out["reason"] = reason
    return out


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or len(df) == 0:
        return []
    rows = []
    for _, row in df.iterrows():
        reason = (
            row["reason"]
            if "reason" in row.index and pd.notna(row.get("reason"))
            else "rebalance"
        )
        rows.append(_row_to_dict(row, reason=str(reason)))
    return rows


def predict(
    asof: str | None = None,
    *,
    rebuild: bool = False,
    panel: str | Path | None = None,
) -> dict[str, Any]:
    _ensure_strategy_path()
    from strategy.rotating.config.params import DEFAULT_PARAMS
    from strategy.rotating.features.build_panel import MODEL_INPUT_FEATURES
    from strategy.rotating.lib.data import load_index_bars, load_security_master
    from strategy.rotating.model.walkforward import walk_forward_score
    from strategy.rotating.pipeline import (
        PANEL_ROOT,
        SCORE_ROOT,
        SELECTED_RESEARCH_SCORE_COLUMN,
        build_panel_dataset,
    )
    from utils.calendar import next_trade_date

    panel_path = Path(panel) if panel is not None else PANEL_ROOT
    if not panel_path.is_absolute():
        panel_path = STRATEGY_ROOT / panel_path
    if rebuild:
        if asof is None:
            raise ValueError("rebuild=True requires an explicit asof date")
        rebuild_start = pd.Timestamp(asof) - pd.DateOffset(years=3)
        build_panel_dataset(str(rebuild_start.date()), asof, output_root=panel_path)
    if not panel_path.exists():
        raise FileNotFoundError(
            f"rotating panel not found at {panel_path}; run "
            "python -m strategy.rotating.cli.run_pipeline build-panel --start 2014-01-01 --end YYYY-MM-DD"
        )

    if asof is None:
        dates = pd.read_parquet(panel_path, columns=["date"])["date"]
        use_date = pd.Timestamp(dates.max()).normalize()
    else:
        use_date = pd.Timestamp(asof).normalize()

    scores = pd.DataFrame()
    if panel is None and SCORE_ROOT.exists() and not rebuild:
        try:
            scores = pd.read_parquet(
                SCORE_ROOT,
                filters=[("date", "==", use_date)],
            )
        except (FileNotFoundError, OSError):
            scores = pd.DataFrame()

    if scores.empty:
        train_start = use_date - pd.DateOffset(years=3)
        columns = [
            "code",
            "date",
            "eligible",
            "target_rank",
            "label_available_date",
            "industry",
            "adv_20",
            "float_market_cap",
            "amount",
            *MODEL_INPUT_FEATURES,
        ]
        feature_panel = pd.read_parquet(
            panel_path,
            columns=columns,
            filters=[("date", ">=", train_start), ("date", "<=", use_date)],
        )
        feature_panel["date"] = pd.to_datetime(feature_panel["date"]).dt.normalize()
        quarter_start = use_date.to_period("Q").start_time
        prediction_dates = sorted(
            feature_panel.loc[
                feature_panel["eligible"].astype(bool)
                & feature_panel["date"].between(quarter_start, use_date),
                "date",
            ].unique()
        )
        scored = walk_forward_score(
            feature_panel,
            prediction_dates=prediction_dates,
            feature_columns=MODEL_INPUT_FEATURES,
        )
        metadata = feature_panel.loc[
            feature_panel["date"].eq(use_date),
            ["code", "date", "industry", "adv_20", "float_market_cap", "amount"],
        ]
        scores = scored.loc[scored["date"].eq(use_date)].merge(
            metadata,
            on=["code", "date"],
            how="left",
            validate="one_to_one",
        )
    if scores.empty:
        raise ValueError(f"no eligible rotating scores available for {use_date.date()}")

    if SELECTED_RESEARCH_SCORE_COLUMN not in scores:
        raise ValueError(
            "rotating scores missing selected model: "
            f"{SELECTED_RESEARCH_SCORE_COLUMN}"
        )
    scores = scores.sort_values(SELECTED_RESEARCH_SCORE_COLUMN, ascending=False)
    top_count = max(1, int(len(scores) * DEFAULT_PARAMS.enter_quantile + 0.999999))
    buy = scores.head(min(DEFAULT_PARAMS.names_per_sleeve, top_count)).copy()
    master = load_security_master()[["code", "name"]]
    master["code"] = master["code"].astype(str).str.zfill(6)
    buy = buy.merge(master, on="code", how="left")
    buy["action"] = "buy"
    buy["score"] = buy[SELECTED_RESEARCH_SCORE_COLUMN]
    buy["industry_name"] = buy.get("industry", "UNKNOWN")
    buy["board"] = buy["code"].map(
        lambda code: "STAR"
        if str(code).startswith(("688", "689"))
        else "ChiNext"
        if str(code).startswith(("300", "301"))
        else "Main"
    )
    buy["composite_score"] = buy[SELECTED_RESEARCH_SCORE_COLUMN]
    buy["position_mult"] = 1.0
    buy["n_max"] = DEFAULT_PARAMS.names_per_sleeve * DEFAULT_PARAMS.sleeves
    buy["reason"] = "shallow_gbrt_top_decile"

    panel_dates = pd.read_parquet(panel_path, columns=["date"])["date"].drop_duplicates()
    index_dates = load_index_bars(
        "000300",
        use_date,
        use_date + pd.Timedelta(days=15),
    )["date"]
    exec_date = next_trade_date(
        str(use_date.date()),
        calendar_dates=pd.concat([panel_dates, index_dates], ignore_index=True),
    )
    return {
        "strategy": STRATEGY_NAME,
        "asof": str(use_date.date()),
        "exec_date": exec_date,
        "exec_hint": "T+1_open",
        "panel": str(panel_path),
        "warnings": [
            "research-only signal: forward paper validation is still required",
            "reduce list and aggregate industry caps require live sleeve holdings",
        ],
        "meta": {
            "eligible_count": int(len(scores)),
            "training_rows": int(scores["training_rows"].iloc[0]),
            "train_max_label_available_date": str(
                pd.Timestamp(scores["train_max_label_available_date"].iloc[0]).date()
            ),
            "model": "shallow_gbrt",
            "admission_status": "research_only_forward_validation_required",
        },
        "buy": _df_to_records(buy),
        "satellite": [],
        "reduce": [],
    }


register(STRATEGY_NAME, predict)
