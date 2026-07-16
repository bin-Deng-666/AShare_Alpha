from __future__ import annotations

import importlib
import sys
import warnings
from pathlib import Path

import duckdb
import pandas as pd
import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "data" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

daily_update = importlib.import_module("update")
update_research_layer = importlib.import_module("research_layer")


def _write_required_warehouse_inputs(
    parquet_root: Path,
    *,
    bar_date: pd.Timestamp,
    limit_date: pd.Timestamp | None = None,
) -> None:
    bars = pd.DataFrame(
        {
            "code": ["600519"],
            "date": [bar_date],
            "exchange": ["SH"],
            "open": [1400.0],
            "high": [1420.0],
            "low": [1390.0],
            "close": [1410.0],
            "volume": [10_000.0],
            "amount": [1_000_000_000.0],
            "source": ["fixture"],
        }
    )
    for adjustment in ("qfq", "raw"):
        root = parquet_root / "daily_bars" / f"adjustment={adjustment}"
        root.mkdir(parents=True)
        bars.to_parquet(root / "600519.parquet", index=False)

    indicator_root = parquet_root / "daily_indicators"
    indicator_root.mkdir(parents=True)
    pd.DataFrame(
        {
            "code": ["600519"],
            "date": [bar_date],
            "turn": [0.5],
            "tradestatus": [1],
            "pctChg": [1.0],
            "peTTM": [20.0],
            "pbMRQ": [5.0],
            "psTTM": [10.0],
            "pcfNcfTTM": [15.0],
            "isST": [0],
            "source": ["fixture"],
        }
    ).to_parquet(indicator_root / "600519.parquet", index=False)

    limit_root = parquet_root / "limit_prices"
    limit_root.mkdir(parents=True)
    pd.DataFrame(
        {
            "code": ["600519"],
            "date": [limit_date or bar_date],
            "prev_close": [1400.0],
            "close": [1410.0],
            "limit_up": [1540.0],
            "limit_down": [1260.0],
            "limit_pct": [0.1],
            "is_limit_up": [False],
            "is_limit_down": [False],
            "source": ["fixture"],
        }
    ).to_parquet(limit_root / "600519.parquet", index=False)


def test_merge_by_date_ignores_all_null_columns_when_resolving_dtypes() -> None:
    existing = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-15"]),
            "turn": pd.Series([None], dtype="object"),
        }
    )
    new = pd.DataFrame(
        {"date": pd.to_datetime(["2026-07-16"]), "turn": [0.5]}
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        merged = daily_update._merge_by_date(existing, new)

    assert not [item for item in caught if issubclass(item.category, FutureWarning)]
    assert merged["turn"].dtype == "float64"
    assert merged["turn"].iloc[-1] == 0.5


def test_indicator_freshness_requires_every_listed_symbol_at_the_target_date(
    tmp_path, monkeypatch
) -> None:
    indicator_root = tmp_path / "daily_indicators"
    indicator_root.mkdir()
    for code, last_date in (
        ("000001", "2026-07-16"),
        ("600519", "2026-07-16"),
        ("300750", "2026-07-16"),
        ("000002", "2026-05-22"),
    ):
        pd.DataFrame(
            {"code": [code], "date": pd.to_datetime([last_date]), "turn": [1.0]}
        ).to_parquet(indicator_root / f"{code}.parquet", index=False)
    monkeypatch.setattr(daily_update, "PARQUET", tmp_path)
    monkeypatch.setattr(
        daily_update,
        "_listed_universe",
        lambda: pd.DataFrame(
            {
                "code": ["000001", "600519", "300750", "000002"],
                "exchange": ["SZ", "SH", "SZ", "SZ"],
            }
        ),
    )

    assert not daily_update._indicators_already_at(pd.Timestamp("2026-07-16"))

    pd.DataFrame(
        {
            "code": ["000002"],
            "date": pd.to_datetime(["2026-07-16"]),
            "turn": [1.0],
        }
    ).to_parquet(indicator_root / "000002.parquet", index=False)

    assert daily_update._indicators_already_at(pd.Timestamp("2026-07-16"))


def test_stock_daily_asof_reads_the_strategy_catalog(tmp_path, monkeypatch) -> None:
    catalog = tmp_path / "quant.duckdb"
    connection = duckdb.connect(str(catalog))
    try:
        connection.execute("CREATE TABLE stock_daily (date DATE)")
        connection.execute(
            "INSERT INTO stock_daily VALUES (DATE '2026-07-15'), (DATE '2026-07-16')"
        )
    finally:
        connection.close()
    monkeypatch.setattr(update_research_layer, "CATALOG", catalog)

    assert daily_update._stock_daily_asof() == pd.Timestamp("2026-07-16")


def test_current_shortcut_refuses_incomplete_limit_coverage(monkeypatch) -> None:
    monkeypatch.setattr(daily_update, "_bars_already_at", lambda _asof: True)
    monkeypatch.setattr(daily_update, "_indicators_already_at", lambda _asof: True)
    monkeypatch.setattr(
        daily_update,
        "_warehouse_limits_cover_year",
        lambda _asof: False,
    )
    monkeypatch.setattr(
        daily_update,
        "_stock_daily_asof",
        lambda: pytest.fail("must not inspect stock_daily before limits are complete"),
    )

    result = daily_update._finish_if_daily_layers_current(
        {},
        pd.Timestamp("2026-07-16"),
        after_calendar_refresh=False,
    )

    assert result is None


def test_limit_coverage_gate_requires_raw_warehouse_through_asof(
    tmp_path, monkeypatch
) -> None:
    warehouse_root = tmp_path / "warehouse"
    for layer in ("daily_bars_raw", "limit_prices"):
        root = warehouse_root / layer / "year=2026"
        root.mkdir(parents=True)
        pd.DataFrame(
            {
                "code": ["600519"],
                "date": pd.to_datetime(["2026-07-15"]),
            }
        ).to_parquet(root / "part.parquet", index=False)
    monkeypatch.setattr(update_research_layer, "WAREHOUSE", warehouse_root)

    assert not daily_update._warehouse_limits_cover_year(
        pd.Timestamp("2026-07-16")
    )


def test_current_year_warehouse_derives_market_cap_when_shares_layer_is_absent(
    tmp_path, monkeypatch
) -> None:
    parquet_root = tmp_path / "parquet"
    warehouse_root = tmp_path / "warehouse"
    date = pd.Timestamp("2026-07-16")
    _write_required_warehouse_inputs(parquet_root, bar_date=date)

    stale_shares = warehouse_root / "shares_daily" / "year=2025"
    stale_shares.mkdir(parents=True)
    pd.DataFrame(
        {
            "code": ["600519"],
            "date": [pd.Timestamp("2025-12-31")],
            "total_shares": [100_000_000],
            "float_shares": [50_000_000],
            "total_market_cap": [10.0],
            "float_market_cap": [5.0],
        }
    ).to_parquet(stale_shares / "part.parquet", index=False)

    monkeypatch.setattr(daily_update, "PARQUET", parquet_root)
    monkeypatch.setattr(update_research_layer, "WAREHOUSE", warehouse_root)

    daily_update.update_warehouse_current_year({"end_date": "2026-07-16"})

    panel = pd.read_parquet(warehouse_root / "stock_daily")
    row = panel.iloc[0]
    assert row["float_shares"] == 50_000_000
    assert row["float_market_cap"] == 200_000_000_000.0
    assert row["total_market_cap"] == 200_000_000_000.0


def test_current_year_warehouse_rejects_stale_limit_layer(
    tmp_path, monkeypatch
) -> None:
    parquet_root = tmp_path / "parquet"
    warehouse_root = tmp_path / "warehouse"
    _write_required_warehouse_inputs(
        parquet_root,
        bar_date=pd.Timestamp("2026-07-16"),
        limit_date=pd.Timestamp("2025-12-31"),
    )
    monkeypatch.setattr(daily_update, "PARQUET", parquet_root)
    monkeypatch.setattr(update_research_layer, "WAREHOUSE", warehouse_root)

    with pytest.raises(RuntimeError, match="limit_prices"):
        daily_update.update_warehouse_current_year({"end_date": "2026-07-16"})


def test_full_warehouse_supports_absent_optional_shares(
    tmp_path, monkeypatch
) -> None:
    parquet_root = tmp_path / "parquet"
    warehouse_root = tmp_path / "warehouse"
    date = pd.Timestamp("2026-07-16")
    _write_required_warehouse_inputs(parquet_root, bar_date=date)
    monkeypatch.setattr(update_research_layer, "PARQUET", parquet_root)
    monkeypatch.setattr(update_research_layer, "WAREHOUSE", warehouse_root)
    monkeypatch.setattr(update_research_layer, "STATE", tmp_path / "state")

    update_research_layer.build_warehouse({"end_date": "2026-07-16"})

    row = pd.read_parquet(warehouse_root / "stock_daily").iloc[0]
    assert pd.isna(row["float_shares"])
    assert row["float_market_cap"] == 200_000_000_000.0
