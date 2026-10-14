"""Benchmark company-event reference validation using local price histories.

Ordinary-day reference prices are synthesized as previous raw close; stored
observed event prices override them. This measures computation, not source I/O.
"""

from __future__ import annotations
import argparse
import hashlib
import json
from time import perf_counter

import pandas as pd
from ashare_alpha.data.paths import PARQUET_ROOT
from ashare_alpha.data.sources.corporate_actions import (
    _apply_reference_price_validation,
)


def benchmark(limit=100):
    paths = sorted((PARQUET_ROOT / "corporate_action_events").glob("*.parquet"))[:limit]
    data = []
    for path in paths:
        raw = PARQUET_ROOT / "daily_bars/adjustment=raw" / path.name
        if not raw.exists():
            continue
        bars = (
            pd.read_parquet(raw, columns=["date", "close"])
            .sort_values("date")
            .reset_index(drop=True)
        )
        events = pd.read_parquet(path)
        refs = bars[["date"]].copy()
        refs["observed_reference_price"] = bars["close"].shift(1)
        if not events.empty:
            values = events.set_index("ex_date")["observed_reference_price"]
            aligned = refs["date"].map(values)
            refs["observed_reference_price"] = aligned.combine_first(
                refs["observed_reference_price"]
            )
        data.append((path.stem, bars, events, refs))
    tick = perf_counter()
    digest = hashlib.sha256()
    rows = 0
    for code, bars, events, refs in data:
        result, diagnostics = _apply_reference_price_validation(
            code, bars, events, refs
        )
        digest.update(
            result.to_json(
                orient="split", date_format="iso", double_precision=15
            ).encode()
        )
        digest.update(json.dumps(diagnostics, sort_keys=True).encode())
        rows += len(bars)
    return {
        "securities": len(data),
        "rows": rows,
        "seconds": perf_counter() - tick,
        "result_sha256": digest.hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.limit), indent=2))


if __name__ == "__main__":
    main()
