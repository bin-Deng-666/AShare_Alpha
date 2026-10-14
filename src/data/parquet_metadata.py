"""Date bounds from Parquet footers; filtered reads for historical cutoffs."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import pandas as pd
import pyarrow.parquet as pq


@lru_cache(maxsize=20000)
def _date_bounds(path: str, size: int, mtime_ns: int, column: str):
    with pq.ParquetFile(path) as file:
        metadata = file.metadata
        index = file.schema.names.index(column)
        minima, maxima = [], []
        for group in range(metadata.num_row_groups):
            stats = metadata.row_group(group).column(index).statistics
            if stats is None or not stats.has_min_max:
                dates = pd.to_datetime(
                    file.read(columns=[column]).column(column).to_pandas(),
                    errors="coerce",
                )
                return dates.min(), dates.max()
            minima.append(pd.Timestamp(stats.min))
            maxima.append(pd.Timestamp(stats.max))
        return (min(minima), max(maxima)) if minima else (pd.NaT, pd.NaT)


def date_bounds(path: Path, column: str = "date"):
    path = Path(path)
    if not path.exists():
        return pd.NaT, pd.NaT
    stat = path.stat()
    return _date_bounds(str(path.resolve()), stat.st_size, stat.st_mtime_ns, column)


def latest_date(path: Path, column: str = "date"):
    value = date_bounds(path, column)[1]
    return None if pd.isna(value) else pd.Timestamp(value).normalize()


def _read_parquet_through_date(
    path: Path, *, date_column: str, data_cutoff=None, columns=None
):
    filters = None
    if data_cutoff is not None:
        filters = [(date_column, "<=", pd.Timestamp(data_cutoff).to_pydatetime())]
    return pd.read_parquet(path, columns=columns, filters=filters)
