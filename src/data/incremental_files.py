"""incremental files: data acquisition and normalization."""

from __future__ import annotations
from pathlib import Path
from typing import Optional, Sequence
import pandas as pd
from ashare_alpha.data.paths import PARQUET_ROOT as PARQUET


def _listed_universe(codes: Optional[Sequence[str]] = None) -> pd.DataFrame:
    master = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    master["code"] = master["code"].astype(str).str.zfill(6)
    if codes is not None:
        wanted = {str(code).zfill(6) for code in codes}
        missing = wanted - set(master["code"])
        if missing:
            raise ValueError(f"Unknown securities: {sorted(missing)}")
        # Explicit historical repairs may include delisted securities.
        universe = master[master["code"].isin(wanted)].copy()
    else:
        universe = master[master["status"].eq("listed")].copy()
    return universe.sort_values(["exchange", "code"]).reset_index(drop=True)


def _merge_by_date(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return new.copy()
    if new.empty:
        return existing.copy()
    columns = existing.columns.union(new.columns, sort=False)
    frame = pd.concat(
        [
            existing.dropna(axis="columns", how="all"),
            new.dropna(axis="columns", how="all"),
        ],
        ignore_index=True,
    ).reindex(columns=columns)
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    return (
        frame.drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _last_date(path: Path) -> Optional[pd.Timestamp]:
    from .parquet_metadata import latest_date

    return latest_date(path)
