"""Shared bounded month scheduling and keyed publication for disclosure sources."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import pandas as pd

from ..parquet_metadata import latest_date
from ..storage import atomic_parquet


def refresh_months(path: Path, fetch_month, keys, config, *, workers=4):
    target = pd.Timestamp(config['end_date']).normalize()
    last = latest_date(path)
    lower = pd.Timestamp(config['start_date']).normalize()
    start = max(lower, last - pd.Timedelta(days=7)) if last is not None else lower
    if config.get('refresh_from'):
        start = max(lower, min(start, pd.Timestamp(config['refresh_from'])))
    if start > target:
        return {'rows': 0, 'months': 0}
    months = pd.period_range(start, target, freq='M')
    frames = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as pool:
        futures = [pool.submit(fetch_month, m.year, m.month, target) for m in months]
        for future in as_completed(futures):
            part = future.result()  # Any failed month prevents a partial publication.
            if not part.empty:
                frames.append(part)
    existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    if not frames:
        if existing.empty:
            raise RuntimeError(f'{path.stem}: no data returned for initial load')
        return {'rows': len(existing), 'months': len(months)}
    new = pd.concat(frames, ignore_index=True)
    new['date'] = pd.to_datetime(new['date']).dt.normalize()
    new = new[new['date'].between(lower, target)]
    combined = pd.concat([existing, new], ignore_index=True).drop_duplicates(keys, keep='last')
    combined = combined.sort_values(keys).reset_index(drop=True)
    atomic_parquet(combined, path)
    return {'rows': len(combined), 'months': len(months)}
