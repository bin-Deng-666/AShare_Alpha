"""Prepare audited execution inputs for the selected single security."""

from pathlib import Path
import pandas as pd
from backtest import MarketData
from data.ashare_data.paths import CATALOG_PATH
from data.ashare_data.pipeline.status import read_status
from strategy.research.artifacts import ROOT, identity, write_json
from .params import Params

STRATEGY_ID = "demo"


def latest_observed(catalog=CATALOG_PATH):
    root = Path(catalog).resolve().parent.parent
    status = read_status(state_root=root / "state", data_root=root)
    if status["writer_active"] or not status["quality"]["current_for_latest_run"]:
        raise RuntimeError("Data is being written or its audit is stale")
    observed = status["quality"].get("bar_max_date")
    if observed is None:
        raise ValueError("No audited market observations; update data first")
    return observed


def build(
    start="2020-01-01",
    end=None,
    *,
    params=None,
    catalog=CATALOG_PATH,
    force=False,
    publish=True,
):
    params = Params.parse(params)
    end = end or latest_observed(catalog)
    data = MarketData.from_catalog(catalog, start=start, end=end, codes=[params.code])
    specification = {
        "strategy": STRATEGY_ID,
        "start": str(pd.Timestamp(start).date()),
        "end": str(pd.Timestamp(end).date()),
        "params": params.as_dict(),
        "content": data.fingerprint,
        "quality": data.metadata["quality"],
        "next_session": data.metadata.get("next_session"),
        "format_version": 1,
    }
    data.metadata.update(
        research_start=specification["start"],
        research_end=specification["end"],
        research_params=params.as_dict(),
        research_spec=specification,
    )
    path = ROOT / STRATEGY_ID / "datasets" / identity(specification)
    if path.exists():
        prior = MarketData.load_snapshot(path)
        if prior.fingerprint != data.fingerprint:
            raise ValueError("Input identity differs from saved content")
        data = prior
    else:
        data.save_snapshot(path)
    if publish:
        write_json(
            ROOT / STRATEGY_ID / "dataset.json",
            {
                "path": str(path),
                "fingerprint": data.fingerprint,
                "start": specification["start"],
                "end": specification["end"],
            },
        )
    return data, path
