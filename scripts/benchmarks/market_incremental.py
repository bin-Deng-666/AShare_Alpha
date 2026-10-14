"""Measure real market-window checks using current files in an isolated directory.

The files already contain the latest completed update. This measures overlap
verification and any detected revisions, not a reconstruction of historical data.
Canonical assets and real source receipts are never modified.
"""

from __future__ import annotations
import argparse
import inspect
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from ashare_alpha.data.sources import market_events, corporate_actions, equity_structure
from ashare_alpha.data.paths import PARQUET_ROOT, STATE_ROOT
from ashare_alpha.data.runtime import load_config
from ashare_alpha.data.storage import atomic_json, writer_lock
from ashare_alpha.data.pipeline.receipts import fingerprint


def measure(name, target, checked):
    layer = market_events.LAYERS[name][0]
    fetch = (
        corporate_actions.fetch_corporate_actions
        if name == "distributions"
        else equity_structure.fetch_equity_structure
    )
    config = {**load_config(), "end_date": target}
    with TemporaryDirectory() as directory:
        root = Path(directory)
        parquet = root / "parquet"
        state = root / "state"
        (parquet / "security_master").mkdir(parents=True)
        (parquet / "security_master/security_master.parquet").symlink_to(
            PARQUET_ROOT / "security_master/security_master.parquet"
        )
        output = parquet / layer
        output.mkdir()
        for path in (PARQUET_ROOT / layer).glob("*.parquet"):
            (output / path.name).symlink_to(path)
        with (
            patch.object(market_events, "PARQUET_ROOT", parquet),
            patch.object(market_events, "STATE_ROOT", state),
        ):
            atomic_json(
                {
                    "status": "success",
                    "checked_through": checked,
                    "full_checked_through": checked,
                    "source_version": hashlib.sha256(
                        inspect.getsource(fetch).encode()
                    ).hexdigest(),
                    "outputs": {
                        p.stem: fingerprint([p]) for p in output.glob("*.parquet")
                    },
                    "progress": {},
                },
                market_events.market_receipt_path(name),
            )
            tick = perf_counter()
            result = market_events.refresh_market_events(name, fetch, config, workers=4)
            elapsed = perf_counter() - tick
    return {
        "asset": name,
        "seconds": round(elapsed, 3),
        **result,
        "data_basis": "current completed files; simulated earlier source receipt",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="2026-10-10")
    parser.add_argument("--checked", default="2026-10-09")
    parser.add_argument(
        "--asset", choices=["distributions", "equity_structure", "both"], default="both"
    )
    args = parser.parse_args()
    names = (
        ["distributions", "equity_structure"] if args.asset == "both" else [args.asset]
    )
    with writer_lock(STATE_ROOT / "data_update.lock"):
        for name in names:
            print(
                json.dumps(
                    measure(name, args.target, args.checked),
                    ensure_ascii=False,
                    indent=2,
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
