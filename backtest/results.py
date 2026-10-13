"""Stable run artifacts published as a single new result directory."""

from __future__ import annotations
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from collections.abc import Mapping
from tempfile import TemporaryDirectory
import json, math, os
import numpy as np
import pandas as pd


def json_value(value):
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    if is_dataclass(value):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    if isinstance(value, pd.DataFrame):
        return json_value(value.to_dict("records"))
    if value is pd.NA:
        return None
    raise TypeError(f"Unsupported reproducibility metadata: {type(value).__name__}")


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(freeze(v) for v in value)
    return value


@dataclass(frozen=True)
class ResultView:
    status: str
    metadata: Mapping
    metrics: Mapping
    daily: pd.DataFrame
    trades: pd.DataFrame


@dataclass
class BacktestResult:
    status: str
    metadata: dict
    metrics: dict
    daily: pd.DataFrame
    positions: pd.DataFrame
    trades: pd.DataFrame
    orders: pd.DataFrame
    attempts: pd.DataFrame
    ledger: pd.DataFrame
    actions: pd.DataFrame
    decisions: pd.DataFrame
    diagnostics: dict
    error: str | None = None

    def view(self):
        metrics = {
            k: v for k, v in self.metrics.items() if not isinstance(v, pd.DataFrame)
        }
        return ResultView(
            self.status,
            freeze(json_value(self.metadata)),
            freeze(json_value(metrics)),
            self.daily.copy(deep=True),
            self.trades.copy(deep=True),
        )

    def save(self, path, *, inputs=None):
        path = Path(path)
        if path.exists():
            raise FileExistsError(f"Result directory already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix="." + path.name + "-", dir=path.parent
        ) as directory:
            root = Path(directory)
            payload = {
                "format_version": 1,
                "status": self.status,
                "error": self.error,
                "metadata": self.metadata,
                "metrics": {
                    k: v
                    for k, v in self.metrics.items()
                    if not isinstance(v, pd.DataFrame)
                },
                "diagnostics": self.diagnostics,
            }
            (root / "summary.json").write_text(
                json.dumps(
                    json_value(payload), ensure_ascii=False, indent=2, allow_nan=False
                )
            )
            for name in [
                "daily",
                "positions",
                "trades",
                "orders",
                "attempts",
                "ledger",
                "actions",
                "decisions",
            ]:
                frame = getattr(self, name).copy(deep=True)
                for column in frame.select_dtypes("object"):
                    if any(
                        isinstance(v, (dict, list, tuple, Mapping))
                        for v in frame[column]
                        if v is not None
                    ):
                        frame[column] = frame[column].map(
                            lambda v: (
                                json.dumps(json_value(v), ensure_ascii=False)
                                if isinstance(v, (dict, list, tuple, Mapping))
                                else v
                            )
                        )
                frame.to_parquet(root / f"{name}.parquet", index=False)
            if inputs is not None:
                if inputs.fingerprint != self.metadata["data_fingerprint"]:
                    raise ValueError("Result and input fingerprints differ")
                inputs.save_snapshot(root / "inputs")
            os.rename(root, path)
        return path


class BacktestError(RuntimeError):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result
