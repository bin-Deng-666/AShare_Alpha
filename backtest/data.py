"""Frozen run inputs and strategy data views restricted to the decision clock."""

from __future__ import annotations
import json
import fcntl
import numpy as np
from hashlib import sha256
from pathlib import Path
import pandas as pd
from .models import symbol, timestamp


def _frame(frame, *, feature=False):
    frame = frame.copy(deep=True)
    if feature and "date" not in frame and "asof" in frame:
        frame = frame.rename(columns={"asof": "date"})
    required = {"code", "date"} | ({"available_at"} if feature else set())
    if not required <= set(frame):
        raise ValueError(f"Missing input columns: {sorted(required-set(frame))}")
    frame["code"] = frame.code.map(symbol)
    frame["date"] = frame.date.map(lambda x: timestamp(x).normalize())
    if feature:
        frame["available_at"] = frame.available_at.map(timestamp)
        if frame.available_at.isna().any():
            raise ValueError("Missing feature availability")
    if frame.duplicated(["code", "date"]).any():
        raise ValueError("Duplicate security/date input keys")
    return frame.sort_values(["date", "code"]).reset_index(drop=True)


def _category_schema(frame):
    from .results import json_value

    if frame is None:
        return {}
    return {
        name: {
            "values": json_value(frame[name].cat.categories.tolist()),
            "dtype": str(frame[name].cat.categories.dtype),
            "ordered": frame[name].cat.ordered,
        }
        for name in frame
        if isinstance(frame[name].dtype, pd.CategoricalDtype)
    }


def _restore_categories(frame, schema):
    for name, spec in schema.items():
        categories = pd.Index(spec["values"], dtype=spec["dtype"])
        frame[name] = pd.Categorical(
            frame[name], categories=categories, ordered=spec["ordered"]
        )
    return frame


def _digest(frame):
    h = sha256(repr([(c, str(frame[c].dtype)) for c in frame]).encode())
    h.update(pd.util.hash_pandas_object(frame, index=False).values.tobytes())
    return h.hexdigest()


class MarketData:
    """Copies materialized inputs; returned strategy tables are independent copies."""

    def __init__(
        self,
        bars,
        *,
        calendar=None,
        master=None,
        features=None,
        benchmark=None,
        metadata=None,
        cash_yield=None,
    ):
        if not {"open", "close"} <= set(bars):
            raise ValueError("Bars require open and close")
        self._bars = _frame(bars)
        dates = calendar if calendar is not None else self._bars.date.unique()
        self.calendar = pd.DatetimeIndex(
            [timestamp(x).normalize() for x in dates]
        ).sort_values()
        if self.calendar.has_duplicates or self.calendar.empty:
            raise ValueError("Calendar must contain unique sessions")
        if not self._bars.date.isin(self.calendar).all():
            raise ValueError("Bars occur outside the trading calendar")
        if master is None:
            master = pd.DataFrame({"code": self._bars.code.unique()})
        self._master = master.copy(deep=True)
        self._master["code"] = self._master.code.map(symbol)
        if self._master.code.duplicated().any():
            raise ValueError("Duplicate master symbols")
        if not set(self._bars.code) <= set(self._master.code):
            raise ValueError("Bars contain unregistered symbols")
        for field in ["list_date", "delist_date"]:
            if field in self._master:
                self._master[field] = self._master[field].map(
                    lambda x: timestamp(x).normalize() if pd.notna(x) else pd.NaT
                )
        self._features = {
            k: _frame(v, feature=True) for k, v in (features or {}).items()
        }
        self._feature_days = {
            name: {
                pd.Timestamp(day): indices
                for day, indices in frame.groupby("date", sort=False).indices.items()
            }
            for name, frame in self._features.items()
        }
        self._benchmark = None if benchmark is None else benchmark.copy(deep=True)
        if self._benchmark is not None:
            if not {"date", "ret"} <= set(self._benchmark):
                raise ValueError("Benchmark requires date and ret")
            self._benchmark["date"] = self._benchmark.date.map(
                lambda x: timestamp(x).normalize()
            )
            if self._benchmark.date.duplicated().any():
                raise ValueError("Duplicate benchmark dates")
            self._benchmark = self._benchmark.sort_values("date").reset_index(drop=True)
        self._cash_yield = None
        if cash_yield is not None:
            rates = cash_yield.copy(deep=True)
            if not {"date", "rate", "available_at"} <= set(rates):
                raise ValueError("Cash yield requires date, rate and available_at")
            rates["date"] = rates.date.map(lambda x: timestamp(x).normalize())
            rates["available_at"] = rates.available_at.map(timestamp)
            rates["rate"] = pd.to_numeric(rates.rate, errors="raise")
            if (
                rates.date.duplicated().any()
                or ((rates.rate < 0) | np.isinf(rates.rate)).any()
            ):
                raise ValueError("Invalid cash yield observations")
            self._cash_yield = rates.sort_values("date").reset_index(drop=True)
        self.metadata = dict(metadata or {})
        self.fingerprint = sha256(
            json.dumps(
                {
                    "bars": _digest(self._bars),
                    "master": _digest(self._master),
                    "calendar": list(map(str, self.calendar)),
                    "features": {
                        k: _digest(v) for k, v in sorted(self._features.items())
                    },
                    **(
                        {"cash_yield": _digest(self._cash_yield)}
                        if self._cash_yield is not None
                        else {}
                    ),
                    "benchmark": (
                        None if self._benchmark is None else _digest(self._benchmark)
                    ),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        self._date_ns = (
            self._bars.date.astype("datetime64[ns]").astype("int64").to_numpy()
        )
        self._code_indices = {
            code: group.index.to_numpy()
            for code, group in self._bars.groupby("code", sort=False)
        }
        self._days = {
            pd.Timestamp(d): g.set_index("code", drop=False)
            for d, g in self._bars.groupby("date", sort=False)
        }

    def cash_rate_at(self, previous_date):
        """Only an observation for the previous session, already available at its close."""
        if self._cash_yield is None:
            raise ValueError("No dated cash yield input")
        day = timestamp(previous_date).normalize()
        rates = self._cash_yield
        usable = rates.loc[
            rates.date.eq(day) & rates.available_at.le(day + pd.Timedelta(hours=15))
        ]
        if usable.empty or pd.isna(usable.rate.iloc[0]):
            return None
        return float(usable.rate.iloc[0])

    def row(self, date, code):
        day = self._days.get(timestamp(date).normalize())
        return None if day is None or code not in day.index else day.loc[code].copy()

    def universe(self, date):
        date = timestamp(date).normalize()
        frame = self._master
        mask = pd.Series(True, index=frame.index)
        if "list_date" in frame:
            mask &= frame.list_date.isna() | frame.list_date.le(date)
        if "delist_date" in frame:
            mask &= frame.delist_date.isna() | frame.delist_date.ge(date)
        return tuple(frame.loc[mask, "code"])

    def save_snapshot(self, path):
        from tempfile import TemporaryDirectory
        import os

        path = Path(path)
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix="." + path.name + "-", dir=path.parent
        ) as directory:
            root = Path(directory)
            self._bars.to_parquet(root / "bars.parquet", index=False)
            self._master.to_parquet(root / "master.parquet", index=False)
            pd.DataFrame({"date": self.calendar}).to_parquet(
                root / "calendar.parquet", index=False
            )
            if self._benchmark is not None:
                self._benchmark.to_parquet(root / "benchmark.parquet", index=False)
            if self._cash_yield is not None:
                self._cash_yield.to_parquet(root / "cash_yield.parquet", index=False)
            inputs = {}
            for i, (name, frame) in enumerate(sorted(self._features.items())):
                filename = f"feature_{i:04d}.parquet"
                frame.to_parquet(root / filename, index=False)
                inputs[name] = filename
            from .results import json_value

            (root / "manifest.json").write_text(
                json.dumps(
                    json_value(
                        {
                            "format_version": 1,
                            "fingerprint": self.fingerprint,
                            "metadata": self.metadata,
                            "category_schemas": {
                                "bars": _category_schema(self._bars),
                                "master": _category_schema(self._master),
                                "features": {
                                    name: _category_schema(frame)
                                    for name, frame in self._features.items()
                                },
                                "benchmark": _category_schema(self._benchmark),
                                "cash_yield": _category_schema(self._cash_yield),
                            },
                            "features": inputs,
                            "cash_yield": self._cash_yield is not None,
                            "benchmark": self._benchmark is not None,
                        }
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            os.rename(root, path)
        return path

    @classmethod
    def load_snapshot(cls, path):
        path = Path(path)
        manifest = json.loads((path / "manifest.json").read_text())
        if manifest.get("format_version", 1) != 1:
            raise ValueError("Unsupported input snapshot format; upgrade AShare Alpha")
        schemas = manifest.get("category_schemas", {})
        features = {}
        for name, filename in manifest["features"].items():
            if Path(filename).name != filename:
                raise ValueError("Unsafe snapshot filename")
            features[name] = _restore_categories(
                pd.read_parquet(path / filename),
                schemas.get("features", {}).get(name, {}),
            )
        data = cls(
            _restore_categories(
                pd.read_parquet(path / "bars.parquet"), schemas.get("bars", {})
            ),
            master=_restore_categories(
                pd.read_parquet(path / "master.parquet"), schemas.get("master", {})
            ),
            calendar=pd.read_parquet(path / "calendar.parquet").date,
            features=features,
            benchmark=(
                _restore_categories(
                    pd.read_parquet(path / "benchmark.parquet"),
                    schemas.get("benchmark", {}),
                )
                if manifest["benchmark"]
                else None
            ),
            cash_yield=(
                _restore_categories(
                    pd.read_parquet(path / "cash_yield.parquet"),
                    schemas.get("cash_yield", {}),
                )
                if manifest.get("cash_yield")
                else None
            ),
            metadata=manifest["metadata"],
        )
        if data.fingerprint != manifest["fingerprint"]:
            raise ValueError("Snapshot fingerprint mismatch")
        return data

    @classmethod
    def from_catalog(
        cls,
        path,
        *,
        start,
        end,
        codes=None,
        warmup_sessions=0,
        features=None,
        benchmark_code="000300",
    ):
        """Materialize under the canonical shared publication lock; no source updates."""
        import duckdb
        from data.ashare_data.pipeline.status import read_status

        from .models import integer

        integer(warmup_sessions, "warmup_sessions")
        start, end = timestamp(start).normalize(), timestamp(end).normalize()
        if start > end:
            raise ValueError("start must not follow end")
        path = Path(path).resolve()
        root = path.parent.parent
        state = root / "state"
        status = read_status(state_root=state, data_root=root)
        if status["writer_active"] or not status.get("quality", {}).get(
            "current_for_latest_run"
        ):
            raise RuntimeError("Data is being written or its quality audit is stale")
        latest = status.get("latest_run") or {}
        if latest.get("status") != "success":
            raise RuntimeError("Latest data update did not complete successfully")
        quality = json.loads((root / "quality_report.json").read_text())
        if any(
            quality.get(key, 0)
            for key in (
                "invalid_ohlc_rows",
                "duplicate_bar_keys",
                "qfq_date_mismatch_count",
            )
        ):
            raise RuntimeError("Audited market data has structural quality errors")
        batch = latest.get("batch_id")
        lock = state / "data_update.lock"
        with lock.open("rb") as guard:
            try:
                fcntl.flock(guard, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "Data writer started during snapshot acquisition"
                ) from exc
            runs = sorted(
                (state / "runs").glob("*.json"), key=lambda p: p.stat().st_mtime_ns
            )
            if runs and json.loads(runs[-1].read_text()).get("batch_id") != batch:
                raise RuntimeError("Data batch changed during snapshot acquisition")
            with duckdb.connect(str(path), read_only=True) as con:
                start, end = timestamp(start).normalize(), timestamp(end).normalize()
                dates = (
                    con.execute(
                        "SELECT calendar_date FROM trade_calendar WHERE is_trading_day AND calendar_date<=? ORDER BY 1",
                        [end.date()],
                    )
                    .fetchdf()
                    .iloc[:, 0]
                )
                calendar = pd.DatetimeIndex(dates)
                first = int(calendar.searchsorted(start))
                if calendar.empty or first >= len(calendar):
                    raise ValueError("No sessions in requested interval")
                if first < warmup_sessions:
                    raise ValueError("Insufficient calendar warmup")
                if calendar[-1].date() < end.date():
                    coverage = con.execute(
                        "SELECT max(calendar_date) FROM trade_calendar"
                    ).fetchone()[0]
                    if coverage is None or coverage < end.date():
                        raise ValueError("Calendar does not cover requested end")
                observed = status.get("quality", {}).get("bar_max_date")
                if observed is None or calendar[-1] > timestamp(observed).normalize():
                    raise ValueError(
                        "Requested trading end exceeds audited market observations"
                    )
                lower = calendar[first - warmup_sessions]
                selected = [] if codes is None else [symbol(c) for c in codes]
                condition = (
                    ""
                    if codes is None
                    else (
                        " AND code IN (" + ",".join("?" for _ in selected) + ")"
                        if selected
                        else " AND FALSE"
                    )
                )
                fields = "code,date,exchange,open,high,low,close,amount,volume,trade_status,is_st,float_market_cap,total_market_cap,turnover_pct,limit_up,limit_down,limit_validation_status,corporate_action_adjusted,corporate_action_formula_status,cash_dividend_per_share,share_distribution_ratio,rights_issue_ratio,rights_issue_price"
                bars = con.execute(
                    f"SELECT {fields} FROM stock_daily_raw WHERE date>=? AND date<=?{condition} ORDER BY date,code",
                    [lower.date(), end.date(), *selected],
                ).fetchdf()
                master = con.execute("SELECT * FROM security_master").fetchdf()
                if codes is not None:
                    master = master.loc[master.code.isin(selected)].copy()
                if any(x not in set(master.code) for x in selected):
                    raise ValueError("Unknown requested security")
                next_session = con.execute(
                    "SELECT min(calendar_date) FROM trade_calendar WHERE is_trading_day AND calendar_date>?",
                    [calendar[-1].date()],
                ).fetchone()[0]
                bench = None
                views = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
                if benchmark_code is not None and "index_daily_bars" in views:
                    benchmark = con.execute(
                        "SELECT date,close FROM index_daily_bars WHERE index_code=? AND date<=? ORDER BY date",
                        [benchmark_code, end.date()],
                    ).fetchdf()
                    benchmark["ret"] = benchmark.close.pct_change(fill_method=None)
                    bench = benchmark.loc[benchmark.date.ge(start), ["date", "ret"]]
        return cls(
            bars,
            calendar=calendar[calendar >= lower],
            master=master,
            features=features,
            benchmark=bench,
            metadata={
                "catalog": str(path),
                "batch_id": batch,
                "quality": status["quality"],
                "next_session": str(next_session) if next_session else None,
                "benchmark": benchmark_code,
                "benchmark_basis": "close_to_close",
                "limitations": [
                    "financials_latest_revision",
                    "volume_units_not_verified",
                    "corporate_settlement_dates_incomplete",
                ],
            },
        )


class DataView:
    def __init__(self, market, asof, requirements):
        self.__market = market
        self.__asof = timestamp(asof)
        self.__requirements = requirements

    @property
    def asof(self):
        return self.__asof

    def _cutoff(self):
        close = self.asof.normalize() + pd.Timedelta(hours=15)
        return (
            self.asof.normalize()
            if self.asof >= close
            else self.asof.normalize() - pd.Timedelta(nanoseconds=1)
        )

    @property
    def universe(self):
        return self.__market.universe(self.asof)

    def history(self, code=None, *, fields=None, lookback=None):
        allowed = set(self.__requirements.fields) | {"code", "date"}
        fields = list(fields or self.__requirements.fields)
        if not set(fields) <= allowed:
            raise ValueError("Undeclared history fields")
        market = self.__market
        end = int(np.searchsorted(market._date_ns, self._cutoff().value, side="right"))
        if lookback is not None:
            from .models import integer

            integer(lookback, "lookback", minimum=1)
        if code is not None:
            indices = market._code_indices.get(symbol(code), np.array([], dtype=int))
            stop = int(np.searchsorted(indices, end))
            selected = (
                indices[max(0, stop - lookback) : stop]
                if lookback is not None
                else indices[:stop]
            )
            result = market._bars.iloc[selected]
        elif lookback is not None:
            parts = []
            for indices in market._code_indices.values():
                stop = int(np.searchsorted(indices, end))
                parts.append(indices[max(0, stop - lookback) : stop])
            selected = (
                np.sort(np.concatenate(parts)) if parts else np.array([], dtype=int)
            )
            result = market._bars.iloc[selected]
        else:
            result = market._bars.iloc[:end]
        return result[
            [
                "code",
                "date",
                *dict.fromkeys(c for c in fields if c not in ("code", "date")),
            ]
        ].copy(deep=True)

    def cross_section(self, table="bars"):
        if table == "bars":
            day = self.__market._days.get(self.asof.normalize())
            fields = [
                "code",
                "date",
                *dict.fromkeys(
                    c for c in self.__requirements.fields if c not in ("code", "date")
                ),
            ]
            if day is None or self._cutoff() < self.asof.normalize():
                return self.__market._bars.iloc[:0][fields].copy()
            return day[fields].reset_index(drop=True).copy(deep=True)
        if table not in self.__requirements.features:
            raise ValueError("Undeclared feature input")
        frame = self.__market._features[table]
        indices = self.__market._feature_days[table].get(
            self.asof.normalize(), np.array([], dtype=int)
        )
        result = frame.iloc[indices]
        mask = result.available_at.le(self.asof)
        # Model artifacts may additionally declare when training/labels became usable.
        for column in ["model_available_at", "labels_available_at"]:
            if column in result:
                available = pd.to_datetime(result[column], errors="coerce")
                mask &= available.notna() & available.le(self.asof)
        return result.loc[mask].copy(deep=True)
