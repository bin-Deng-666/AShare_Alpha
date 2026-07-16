from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "data" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

build_dataset = importlib.import_module("build_dataset")


def test_server_asof_sources_classify_standard_and_rights_formulas(
    monkeypatch,
) -> None:
    sharebonus = pd.DataFrame(
        {
            "code": ["000001"],
            "ex_date": [pd.Timestamp("2022-06-01")],
            "cash_dividend_per_10": [2.0],
            "bonus_share_per_10": [1.0],
            "transfer_share_per_10": [0.0],
            "bonus_transfer_per_10": [1.0],
        }
    )
    raw_rights = pd.DataFrame([[None] * 57])
    raw_rights.iloc[0, 8] = 5.0
    raw_rights.iloc[0, 9] = 2.0
    raw_rights.iloc[0, 10] = 100.0
    raw_rights.iloc[0, 18] = pd.Timestamp("2023-06-01")
    raw_rights.iloc[0, 21] = "000001"
    raw_rights.iloc[0, 50] = 120.0

    class FakeAk:
        @staticmethod
        def stock_allotment_cninfo(**kwargs):
            assert kwargs == {
                "symbol": "000001",
                "start_date": "19700101",
                "end_date": "20231231",
            }
            return raw_rights.copy()

    monkeypatch.setattr(build_dataset, "ak", FakeAk())
    standard = build_dataset._sharebonus_events(sharebonus)
    rights = build_dataset.fetch_cninfo_allotment_events(
        "000001", "2023-12-31"
    )
    result = build_dataset._combine_corporate_action_events(standard, rights)

    assert result["formula_status"].tolist() == [
        "supported_standard",
        "rights_issue_price_formula_only",
    ]
    assert result.iloc[0]["cash_dividend_per_10"] == pytest.approx(2.0)
    assert result.iloc[1]["rights_issue_per_10"] == pytest.approx(2.0)
    assert result.iloc[1]["shares_before"] == pytest.approx(1_000_000.0)


def test_eastmoney_request_applies_cutoff_before_response_rows_are_read() -> None:
    captured: dict[str, object] = {}

    class Response:
        encoding = "utf-8"

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {"success": True, "result": {"data": [], "pages": 1}}

    class Session:
        @staticmethod
        def get(url, *, params, timeout):
            captured.update({"url": url, "params": params, "timeout": timeout})
            return Response()

    class NoWait:
        @staticmethod
        def wait() -> None:
            return None

    result = build_dataset.fetch_corporate_actions(
        Session(), None, NoWait(), data_cutoff="2023-12-31"
    )

    assert result.empty
    assert captured["params"]["filter"] == (
        "(EX_DIVIDEND_DATE<='2023-12-31')"
    )


def test_preclose_validation_detects_unclassified_and_consolidation_events() -> None:
    raw = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"]
            ),
            "close": [10.0, 10.0, 5.0, 6.0],
        }
    )
    observed = pd.DataFrame(
        {
            "date": raw["date"],
            "observed_reference_price": [0.0, 10.0, 20.0, 4.0],
        }
    )

    events, diagnostics = build_dataset._apply_reference_price_validation(
        "000001",
        raw,
        build_dataset._empty_corporate_action_events(),
        observed,
    )

    assert events["formula_status"].tolist() == [
        "unsupported_share_consolidation",
        "unsupported_unclassified_share_change",
    ]
    assert events["observed_reference_price"].tolist() == [20.0, 4.0]
    assert diagnostics["detected_share_consolidations"] == 1
    assert diagnostics["detected_unclassified_share_changes"] == 1
    assert diagnostics["missing_reference_rows"] == 0
    assert diagnostics["reference_gap_invalidations"] == 0


def test_preclose_validation_invalidates_a_missing_reference_price() -> None:
    raw = pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-01-02", "2023-01-03"]),
            "close": [10.0, 10.5],
        }
    )
    observed = pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-01-02"]),
            "observed_reference_price": [0.0],
        }
    )

    events, diagnostics = build_dataset._apply_reference_price_validation(
        "000001",
        raw,
        build_dataset._empty_corporate_action_events(),
        observed,
    )

    assert events["formula_status"].tolist() == [
        "unsupported_missing_reference_price"
    ]
    assert events["ex_date"].tolist() == [pd.Timestamp("2023-01-03")]
    assert diagnostics["missing_reference_rows"] == 1
    assert diagnostics["reference_gap_invalidations"] == 1


def test_preclose_gap_preserves_declared_action_entitlements() -> None:
    raw = pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-05-31", "2023-06-01"]),
            "close": [10.0, 9.0],
        }
    )
    observed = pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-05-31"]),
            "observed_reference_price": [0.0],
        }
    )
    declared = build_dataset._sharebonus_events(
        pd.DataFrame(
            {
                "code": ["000001"],
                "ex_date": [pd.Timestamp("2023-06-01")],
                "cash_dividend_per_10": [2.0],
                "bonus_share_per_10": [1.0],
                "transfer_share_per_10": [0.0],
                "bonus_transfer_per_10": [1.0],
            }
        )
    )

    events, diagnostics = build_dataset._apply_reference_price_validation(
        "000001", raw, declared, observed
    )

    assert events.iloc[0]["formula_status"] == (
        "unsupported_declared_action_missing_reference_price"
    )
    assert events.iloc[0]["cash_dividend_per_10"] == pytest.approx(2.0)
    assert events.iloc[0]["bonus_transfer_per_10"] == pytest.approx(1.0)
    assert diagnostics["reference_gap_invalidations"] == 1


def test_preclose_validation_rejects_a_standard_formula_mismatch() -> None:
    raw = pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-05-31", "2023-06-01"]),
            "close": [10.0, 9.5],
        }
    )
    events = build_dataset._sharebonus_events(
        pd.DataFrame(
            {
                "code": ["000001"],
                "ex_date": [pd.Timestamp("2023-06-01")],
                "cash_dividend_per_10": [2.0],
                "bonus_transfer_per_10": [0.0],
            }
        )
    )
    observed = pd.DataFrame(
        {
            "date": raw["date"],
            "observed_reference_price": [0.0, 9.7],
        }
    )

    validated, diagnostics = build_dataset._apply_reference_price_validation(
        "000001", raw, events, observed
    )

    assert validated.iloc[0]["formula_status"] == (
        "unsupported_reference_mismatch"
    )
    assert validated.iloc[0]["observed_reference_price"] == pytest.approx(9.7)
    assert diagnostics["reference_mismatches"] == 1


def test_limit_reference_uses_ex_right_cash_and_share_distribution() -> None:
    bars = pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-05-31", "2023-06-01"]),
            "prev_close": [9.5, 10.0],
        }
    )
    actions = pd.DataFrame(
        {
            "ex_date": [pd.Timestamp("2023-06-01")],
            "cash_dividend_per_10": [2.0],
            "bonus_share_per_10": [1.0],
            "transfer_share_per_10": [1.0],
            "bonus_transfer_per_10": [2.0],
            "publish_date": [pd.Timestamp("2023-05-25")],
        }
    )

    reference, adjusted = build_dataset._limit_reference_from_corporate_actions(
        bars, actions
    )

    assert reference.iloc[0] == pytest.approx(9.5)
    assert reference.iloc[1] == pytest.approx((10.0 - 0.2) / 1.2)
    assert adjusted.tolist() == [False, True]


def test_limit_reference_uses_latest_record_once_per_ex_date() -> None:
    bars = pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-06-01"]),
            "prev_close": [10.0],
        }
    )
    actions = pd.DataFrame(
        {
            "ex_date": [pd.Timestamp("2023-06-01")] * 2,
            "cash_dividend_per_10": [1.0, 2.0],
            "bonus_share_per_10": [0.0, 0.0],
            "transfer_share_per_10": [0.0, 0.0],
            "bonus_transfer_per_10": [0.0, 0.0],
            "publish_date": pd.to_datetime(["2023-05-20", "2023-05-25"]),
        }
    )

    reference, adjusted = build_dataset._limit_reference_from_corporate_actions(
        bars, actions
    )

    assert reference.iloc[0] == pytest.approx(9.8)
    assert adjusted.iloc[0]


def test_limit_price_rounding_uses_cent_rounded_reference_and_half_up_ties() -> None:
    reference = pd.Series([10.725, 9.646])

    basis = build_dataset._round_positive_price_to_cent(reference)
    upper = build_dataset._round_positive_price_to_cent(basis * 1.2)

    assert basis.tolist() == pytest.approx([10.73, 9.65])
    assert upper.tolist() == pytest.approx([12.88, 11.58])


def test_dated_parquet_reader_pushes_the_physical_cutoff(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_read_parquet(path, **kwargs):
        captured.update(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(build_dataset.pd, "read_parquet", fake_read_parquet)

    build_dataset._read_parquet_through_date(
        tmp_path / "bars.parquet",
        date_column="date",
        data_cutoff="2023-12-31",
        columns=["date", "close"],
    )

    assert captured["columns"] == ["date", "close"]
    assert captured["filters"][0][:2] == ("date", "<=")
    assert pd.Timestamp(captured["filters"][0][2]) == pd.Timestamp("2023-12-31")


def test_corporate_action_qfq_excludes_events_after_the_physical_cutoff() -> None:
    raw = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2023-12-29", "2024-01-02", "2024-01-03"]
            ),
            "open": [10.0, 9.0, 9.1],
            "high": [10.0, 9.2, 9.2],
            "low": [10.0, 8.9, 9.0],
            "close": [10.0, 9.0, 9.1],
        }
    )
    actions = pd.DataFrame(
        {
            "ex_date": [pd.Timestamp("2024-01-02")],
            "cash_dividend_per_10": [10.0],
            "bonus_transfer_per_10": [0.0],
        }
    )

    result = build_dataset._qfq_from_corporate_actions(
        raw,
        actions,
        data_cutoff="2023-12-31",
    )

    assert result["date"].max() == pd.Timestamp("2023-12-29")
    assert result["adjustment_factor"].tolist() == [1.0]


def test_unsupported_action_is_retained_but_not_silently_adjusted() -> None:
    bars = pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-05-31", "2023-06-01"]),
            "prev_close": [9.5, 10.0],
        }
    )
    actions = pd.DataFrame(
        {
            "ex_date": [pd.Timestamp("2023-06-01")],
            "cash_dividend_per_10": [0.0],
            "bonus_transfer_per_10": [0.0],
            "rights_issue_per_10": [3.0],
            "rights_issue_price": [5.0],
            "formula_status": ["unsupported_rights_issue"],
        }
    )

    terms = build_dataset._corporate_action_terms_for_bars(bars, actions)
    reference, adjusted = build_dataset._limit_reference_from_corporate_actions(
        bars, actions
    )

    assert terms.iloc[1]["corporate_action_formula_status"] == (
        "unsupported_rights_issue"
    )
    assert adjusted.iloc[1]
    assert reference.iloc[1] == pytest.approx((10.0 + 5.0 * 0.3) / 1.3)


def test_limit_reference_rejects_an_impossible_post_ex_report_period() -> None:
    bars = pd.DataFrame(
        {
            "date": pd.to_datetime(["2013-06-14"]),
            "prev_close": [12.11],
        }
    )
    actions = pd.DataFrame(
        {
            "ex_date": [pd.Timestamp("2013-06-14")] * 2,
            "report_date": pd.to_datetime(["2012-12-31", "2013-06-30"]),
            "cash_dividend_per_10": [0.21, 0.41],
            "bonus_share_per_10": [float("nan")] * 2,
            "transfer_share_per_10": [float("nan")] * 2,
            "bonus_transfer_per_10": [float("nan")] * 2,
            "publish_date": pd.to_datetime(["2013-03-30", "2013-08-26"]),
        }
    )

    reference, adjusted = build_dataset._limit_reference_from_corporate_actions(
        bars, actions
    )

    assert reference.iloc[0] == pytest.approx(12.11 - 0.021)
    assert adjusted.iloc[0]


def test_corporate_action_qfq_removes_the_mechanical_distribution_jump() -> None:
    raw = pd.DataFrame(
        {
            "code": "002846",
            "date": pd.to_datetime(
                ["2021-03-29", "2021-03-30", "2021-03-31"]
            ),
            "exchange": "SZ",
            "open": [17.0, 10.6, 10.9],
            "high": [17.78, 11.0, 11.88],
            "low": [16.5, 10.46, 10.8],
            "close": [17.78, 10.8, 11.88],
            "volume": 1_000.0,
            "amount": 10_000.0,
            "source": "fixture",
        }
    )
    actions = pd.DataFrame(
        {
            "ex_date": [pd.Timestamp("2021-03-30")],
            "cash_dividend_per_10": [1.49971],
            "bonus_share_per_10": [float("nan")],
            "transfer_share_per_10": [6.0],
            "bonus_transfer_per_10": [6.0],
        }
    )

    result = build_dataset._qfq_from_corporate_actions(raw, actions)

    ex_reference = (17.78 - 1.49971 / 10.0) / 1.6
    assert result.loc[0, "close"] == pytest.approx(ex_reference)
    assert result.loc[1, "close"] == pytest.approx(10.8)
    assert result.loc[0, "adjustment_factor"] == pytest.approx(
        ex_reference / 17.78
    )
    assert result.loc[1, "adjustment_factor"] == pytest.approx(1.0)
    assert result["source"].eq("corporate_actions_adjusted+raw_bars").all()


def test_limit_price_builder_invalidates_raw_ohlc_outside_the_derived_boundary(
    tmp_path, monkeypatch
) -> None:
    parquet_root = tmp_path / "parquet"
    state_root = tmp_path / "state"
    raw_root = parquet_root / "daily_bars" / "adjustment=raw"
    raw_root.mkdir(parents=True)
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-05-31", "2023-06-01"]),
            "high": [10.0, 10.0],
            "low": [10.0, 8.0],
            "close": [10.0, 8.5],
        }
    ).to_parquet(raw_root / "000001.parquet", index=False)
    monkeypatch.setattr(build_dataset, "PARQUET", parquet_root)
    monkeypatch.setattr(build_dataset, "STATE", state_root)

    build_dataset.build_limit_prices({}, codes=["000001"], force=True)

    result = pd.read_parquet(
        parquet_root / "limit_prices" / "000001.parquet"
    )
    invalid = result.iloc[1]
    assert invalid["limit_validation_status"] == "raw_ohlc_outside_limit"
    assert pd.isna(invalid["limit_up"])
    assert pd.isna(invalid["limit_down"])
    assert not invalid["is_limit_up"]
    assert not invalid["is_limit_down"]


def test_limit_price_builder_requires_exact_cent_close_and_applies_cutoff(
    tmp_path, monkeypatch
) -> None:
    parquet_root = tmp_path / "parquet"
    state_root = tmp_path / "state"
    raw_root = parquet_root / "daily_bars" / "adjustment=raw"
    raw_root.mkdir(parents=True)
    pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2023-12-28", "2023-12-29", "2024-01-02"]
            ),
            "high": [10.0, 11.0, 12.09],
            "low": [10.0, 10.0, 11.0],
            "close": [10.0, 10.99, 12.09],
        }
    ).to_parquet(raw_root / "000001.parquet", index=False)
    monkeypatch.setattr(build_dataset, "PARQUET", parquet_root)
    monkeypatch.setattr(build_dataset, "STATE", state_root)

    build_dataset.build_limit_prices(
        {}, codes=["000001"], force=True, data_cutoff="2023-12-31"
    )

    result = pd.read_parquet(
        parquet_root / "limit_prices" / "000001.parquet"
    )
    assert result["date"].max() == pd.Timestamp("2023-12-29")
    assert result.iloc[-1]["limit_up"] == pytest.approx(11.0)
    assert not result.iloc[-1]["is_limit_up"]


def test_limit_price_builder_uses_rights_price_but_keeps_capability_status(
    tmp_path, monkeypatch
) -> None:
    parquet_root = tmp_path / "parquet"
    state_root = tmp_path / "state"
    raw_root = parquet_root / "daily_bars" / "adjustment=raw"
    event_root = parquet_root / "corporate_action_events"
    raw_root.mkdir(parents=True)
    event_root.mkdir(parents=True)
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-05-31", "2023-06-01"]),
            "high": [10.0, 9.0],
            "low": [10.0, 8.0],
            "close": [10.0, 8.5],
        }
    ).to_parquet(raw_root / "000001.parquet", index=False)
    pd.DataFrame(
        {
            "code": "000001",
            "ex_date": pd.to_datetime(["2023-06-01"]),
            "cash_dividend_per_10": 0.0,
            "bonus_transfer_per_10": 0.0,
            "rights_issue_per_10": 3.0,
            "rights_issue_price": 5.0,
            "formula_status": "rights_issue_price_formula_only",
        }
    ).to_parquet(event_root / "000001.parquet", index=False)
    monkeypatch.setattr(build_dataset, "PARQUET", parquet_root)
    monkeypatch.setattr(build_dataset, "STATE", state_root)

    build_dataset.build_limit_prices(
        {}, codes=["000001"], force=True, data_cutoff="2023-12-31"
    )

    result = pd.read_parquet(
        parquet_root / "limit_prices" / "000001.parquet"
    )
    rights_row = result.iloc[-1]
    assert rights_row["corporate_action_formula_status"] == (
        "rights_issue_price_formula_only"
    )
    assert rights_row["reference_price"] == pytest.approx((10.0 + 1.5) / 1.3)
    assert rights_row["limit_validation_status"] == "valid"
    assert rights_row["limit_up"] == pytest.approx(9.74)
    assert rights_row["limit_down"] == pytest.approx(7.97)


def test_limit_builder_retains_declared_terms_when_reference_is_missing(
    tmp_path, monkeypatch
) -> None:
    parquet_root = tmp_path / "parquet"
    state_root = tmp_path / "state"
    raw_root = parquet_root / "daily_bars" / "adjustment=raw"
    event_root = parquet_root / "corporate_action_events"
    raw_root.mkdir(parents=True)
    event_root.mkdir(parents=True)
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-05-31", "2023-06-01"]),
            "high": [10.0, 9.0],
            "low": [10.0, 8.0],
            "close": [10.0, 8.5],
        }
    ).to_parquet(raw_root / "000001.parquet", index=False)
    pd.DataFrame(
        {
            "code": "000001",
            "ex_date": pd.to_datetime(["2023-06-01"]),
            "cash_dividend_per_10": 2.0,
            "bonus_transfer_per_10": 1.0,
            "rights_issue_per_10": 0.0,
            "rights_issue_price": 0.0,
            "observed_reference_price": float("nan"),
            "formula_status": (
                "unsupported_declared_action_missing_reference_price"
            ),
        }
    ).to_parquet(event_root / "000001.parquet", index=False)
    monkeypatch.setattr(build_dataset, "PARQUET", parquet_root)
    monkeypatch.setattr(build_dataset, "STATE", state_root)

    build_dataset.build_limit_prices(
        {}, codes=["000001"], force=True, data_cutoff="2023-12-31"
    )

    action_row = pd.read_parquet(
        parquet_root / "limit_prices" / "000001.parquet"
    ).iloc[-1]
    assert not action_row["corporate_action_adjusted"]
    assert action_row["limit_validation_status"] == (
        "unsupported_corporate_action"
    )
    assert pd.isna(action_row["limit_up"])
    assert action_row["cash_dividend_per_share"] == pytest.approx(0.2)
    assert action_row["share_distribution_ratio"] == pytest.approx(0.1)
