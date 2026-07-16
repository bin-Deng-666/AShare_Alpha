from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from data.scripts import research_layer


@pytest.mark.parametrize(
    ("raw_code", "expected"),
    [("801010.SI", "801010"), ("sh.000300", "000300"), ("000905", "000905")],
)
def test_index_code_normalization_is_shared_by_master_and_daily_views(
    raw_code, expected
) -> None:
    con = duckdb.connect()
    normalized = con.execute(
        "SELECT " + research_layer._normalize_index_code_sql("raw_code")
        + " FROM (SELECT ?::VARCHAR AS raw_code)",
        [raw_code],
    ).fetchone()[0]

    assert normalized == expected


def test_catalog_index_view_unifies_broad_and_industry_bars(
    tmp_path, monkeypatch
) -> None:
    broad_dir = tmp_path / "reference" / "index_bars"
    industry_dir = tmp_path / "reference" / "industry_indices" / "daily_bars"
    broad_dir.mkdir(parents=True)
    industry_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02"]),
            "code": ["sh.000300"],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "preclose": [99.0],
            "volume": [1_000.0],
            "amount": [10_000.0],
            "pctChg": [2.0202],
            "source": ["baostock"],
        }
    ).to_parquet(broad_dir / "000300.parquet", index=False)
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "index_code": ["801010", "801010"],
            "industry_name": ["农林牧渔", "农林牧渔"],
            "open": [20.0, 21.0],
            "high": [20.5, 22.5],
            "low": [19.5, 20.5],
            "close": [20.0, 22.0],
            "volume": [100.0, 120.0],
            "amount": [2_000.0, 2_500.0],
            "source": ["sw_via_akshare", "sw_via_akshare"],
        }
    ).to_parquet(industry_dir / "801010.parquet", index=False)
    monkeypatch.setattr(research_layer, "PARQUET", tmp_path)

    con = duckdb.connect()
    con.execute(
        "CREATE VIEW index_daily_bars AS "
        + research_layer._catalog_index_view_sql()
    )
    frame = con.execute(
        "SELECT * FROM index_daily_bars ORDER BY index_type, index_code, date"
    ).fetchdf()

    assert frame.columns.tolist() == [
        "index_code",
        "date",
        "index_name",
        "index_type",
        "open",
        "high",
        "low",
        "close",
        "preclose",
        "volume",
        "amount",
        "pct_change",
        "source",
    ]
    broad = frame.loc[frame["index_type"].eq("broad_based")].iloc[0]
    industry = frame.loc[
        frame["index_type"].eq("industry") & frame["date"].eq(pd.Timestamp("2024-01-03"))
    ].iloc[0]
    assert (broad["index_code"], broad["preclose"]) == ("000300", 99.0)
    assert (industry["index_name"], industry["preclose"]) == ("农林牧渔", 20.0)
    assert industry["pct_change"] == pytest.approx(10.0)
    assert industry["source"].endswith(":derived_preclose_pct_change")


def test_catalog_unique_check_rejects_duplicate_logical_keys() -> None:
    con = duckdb.connect()
    con.execute(
        "CREATE VIEW duplicated AS "
        "SELECT * FROM (VALUES ('000001', DATE '2024-01-02'), "
        "('000001', DATE '2024-01-02')) AS rows(code, date)"
    )

    with pytest.raises(RuntimeError, match="duplicated.*code, date"):
        research_layer._assert_unique(con, "duplicated", ("code", "date"))
