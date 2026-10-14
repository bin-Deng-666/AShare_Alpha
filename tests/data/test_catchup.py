"""Long-gap catch-up, historical repairs and revision merge regressions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from ashare_alpha.data.storage import atomic_parquet
from ashare_alpha.data.assets import incremental
from ashare_alpha.data.sources import announcements, financial_statements, prices
from ashare_alpha.data.sources.fetch_windows import fetch_start, financial_period_count
from ashare_alpha.data.pipeline.receipts import task_state_path
from ashare_alpha.data import incremental_files


class CatchupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_fetch_windows_cover_long_gap_and_bootstrap(self):
        config = {"start_date": "2010-01-01", "end_date": "2026-07-01"}
        self.assertEqual(fetch_start(config, "2023-01-31"), "2023-01-24")
        self.assertEqual(fetch_start(config), "2010-01-01")
        self.assertEqual(fetch_start(config, "2027-01-01"), "2010-01-01")
        self.assertEqual(
            fetch_start({**config, "refresh_from": "2018-03-01"}, "2026-06-01"),
            "2018-03-01",
        )
        self.assertGreater(financial_period_count(config, "2020-01-01"), 24)
        self.assertGreaterEqual(financial_period_count(config), 80)

    def test_announcements_request_gap_and_not_recent_180_days(self):
        observed = []

        class Session:
            headers = {}

            def post(self, url, data, timeout):
                observed.append(dict(data))
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {"announcements": [], "hasMore": False},
                )

        universe = pd.DataFrame(
            [{"code": "000001", "exchange": "SZ", "org_id": "known-id"}]
        )
        config = {
            "start_date": "2010-01-01",
            "end_date": "2026-07-01",
            "retries": 1,
            "_source_checked_through": {"000001": "2023-01-31"},
        }
        completed = []
        with (
            patch.object(announcements, "PARQUET", self.root),
            patch.object(announcements, "STATE", self.root / "state"),
            patch.object(announcements, "_listed_universe", return_value=universe),
            patch.object(announcements.requests, "Session", Session),
            patch.object(announcements.Throttle, "wait"),
        ):
            announcements.update_announcements_incremental(
                config, on_code_done=completed.append, max_workers=1
            )
        self.assertEqual(observed[0]["seDate"], "2023-01-24~2026-07-01")
        self.assertEqual(completed, ["000001"])

    def test_invalid_announcement_page_is_not_empty_success(self):
        for payload in (
            {},
            {"error": "rate limited"},
            {"announcements": [], "hasMore": True},
        ):
            response = SimpleNamespace(
                raise_for_status=lambda: None, json=lambda: payload
            )
            with self.assertRaises(RuntimeError):
                announcements.announcement_items(response)

    def test_latest_statement_observation_wins_without_losing_history(self):
        old = pd.DataFrame(
            [
                {
                    "code": "000001",
                    "report_date": pd.Timestamp("2024-12-31"),
                    "statement_type": "lrb",
                    "item_code": "profit",
                    "item": "profit",
                    "value": 100.0,
                },
                {
                    "code": "000001",
                    "report_date": pd.Timestamp("2023-12-31"),
                    "statement_type": "lrb",
                    "item_code": "profit",
                    "item": "profit",
                    "value": 80.0,
                },
            ]
        )
        new = old.iloc[:1].copy()
        new["value"] = 9.0  # shorter representation must still replace the older value
        result = financial_statements.merge_statement_revisions(old, new)
        self.assertEqual(result["value"].tolist(), [9.0, 80.0])
        pd.testing.assert_frame_equal(
            result, financial_statements.merge_statement_revisions(result, new)
        )

    def test_partial_statement_failure_keeps_prior_asset(self):
        path = self.root / "financials/000001.parquet"
        atomic_parquet(pd.DataFrame({"old": [1]}), path)
        before = path.read_bytes()
        config = {
            "start_date": "2010-01-01",
            "end_date": "2026-07-01",
            "retries": 1,
            "sina_min_interval_seconds": 0,
        }

        def fetch(session, code, kind, *args):
            if kind == "fzb":
                raise RuntimeError("source unavailable")
            return pd.DataFrame({"value": [1]})

        with (
            patch.object(financial_statements, "PARQUET", self.root),
            patch.object(financial_statements, "STATE", self.root / "state"),
            patch.object(
                financial_statements,
                "_listed_universe",
                return_value=pd.DataFrame({"code": ["000001"]}),
            ),
            patch.object(
                financial_statements, "fetch_sina_statement", side_effect=fetch
            ),
        ):
            done = []
            financial_statements.update_financials_incremental(
                config, on_code_done=done.append, max_workers=1
            )
        self.assertEqual(done, [])
        self.assertEqual(path.read_bytes(), before)

    def test_derived_resume_after_exception_and_new_day_noop(self):
        for code in ("000001", "000002"):
            atomic_parquet(pd.DataFrame({"v": [1]}), self.root / f"in/{code}.parquet")
        calls = []

        def build(config, codes, force, on_code_done):
            calls.append(codes)
            for code in codes:
                if code == "000002" and len(calls) == 1:
                    raise RuntimeError("interrupted")
                atomic_parquet(
                    pd.DataFrame({"v": [1]}), self.root / f"out/{code}.parquet"
                )
                on_code_done(code)

        with (
            patch.object(incremental, "PARQUET", self.root),
            patch.object(incremental, "STATE", self.root / "state"),
        ):

            def run(day):
                return incremental.rebuild_changed(
                    "sample",
                    build,
                    {"end_date": day},
                    ["000001", "000002"],
                    ["in/{code}.parquet"],
                    "out/{code}.parquet",
                )

            with self.assertRaises(RuntimeError):
                run("2026-07-01")
            run("2026-07-01")
            self.assertEqual(run("2026-07-02")["processed"], 0)
        self.assertEqual(calls, [["000001", "000002"], ["000002"]])

    def test_explicit_delisted_selection_and_invalid_codes(self):
        atomic_parquet(
            pd.DataFrame(
                {
                    "code": ["000001", "000002"],
                    "status": ["listed", "delisted"],
                    "exchange": ["SZ", "SZ"],
                }
            ),
            self.root / "security_master/security_master.parquet",
        )
        with patch.object(incremental_files, "PARQUET", self.root):
            self.assertEqual(
                incremental_files._listed_universe().code.tolist(), ["000001"]
            )
            self.assertEqual(
                incremental_files._listed_universe(["000002"]).code.tolist(), ["000002"]
            )
            self.assertTrue(incremental_files._listed_universe([]).empty)
            with self.assertRaises(ValueError):
                incremental_files._listed_universe(["999999"])

    def test_subset_receipts_do_not_replace_full_market(self):
        self.assertNotEqual(
            task_state_path(self.root, "prices"),
            task_state_path(self.root, "prices", ["000001"]),
        )
        self.assertEqual(
            task_state_path(self.root, "prices", ["000002", "000001"]),
            task_state_path(self.root, "prices", ["000001", "000002"]),
        )

    def test_monthly_disclosures_cover_gap_and_bound_last_day(self):
        from ashare_alpha.data.sources.monthly_disclosures import refresh_months

        path = self.root / "events.parquet"
        atomic_parquet(
            pd.DataFrame(
                {
                    "date": pd.to_datetime(["2023-12-29"]),
                    "code": ["000001"],
                    "value": [1],
                }
            ),
            path,
        )
        calls = []

        def fetch(year, month, target):
            calls.append((year, month, pd.Timestamp(target)))
            return pd.DataFrame(
                {
                    "date": [pd.Timestamp(year=year, month=month, day=1)],
                    "code": ["000001"],
                    "value": [2],
                }
            )

        result = refresh_months(
            path,
            fetch,
            ["date", "code"],
            {"start_date": "2020-01-01", "end_date": "2024-03-15"},
            workers=2,
        )
        self.assertEqual(
            sorted((y, m) for y, m, _ in calls),
            [(2023, 12), (2024, 1), (2024, 2), (2024, 3)],
        )
        self.assertTrue(all(t == pd.Timestamp("2024-03-15") for _, _, t in calls))
        self.assertEqual(result["months"], 4)
        self.assertIn(
            pd.Timestamp("2023-12-29"), pd.read_parquet(path)["date"].tolist()
        )
        before = path.read_bytes()

        def failing(*args):
            raise RuntimeError("failed month")

        with self.assertRaises(RuntimeError):
            refresh_months(
                path,
                failing,
                ["date", "code"],
                {"start_date": "2020-01-01", "end_date": "2024-04-15"},
            )
        self.assertEqual(path.read_bytes(), before)

    def test_historical_price_repair_runs_when_latest_date_is_current(self):
        output = self.root / "daily_bars/adjustment=raw/000001.parquet"
        old = pd.DataFrame(
            {
                "date": pd.to_datetime(["2020-01-02", "2026-07-01"]),
                "close": [10.0, 20.0],
            }
        )
        atomic_parquet(old, output)
        universe = pd.DataFrame(
            [
                {
                    "code": "000001",
                    "exchange": "SZ",
                    "list_date": pd.Timestamp("1990-01-01"),
                    "delist_date": pd.NaT,
                }
            ]
        )
        config = {
            "start_date": "2010-01-01",
            "end_date": "2026-07-01",
            "refresh_from": "2020-01-01",
            "bar_adjustments": ["raw"],
            "eastmoney_min_interval_seconds": 0,
        }
        new = old.iloc[:1].copy()
        new["close"] = 11.0
        with (
            patch.object(prices, "PARQUET", self.root),
            patch.object(prices, "STATE", self.root / "state"),
            patch.object(prices, "_listed_universe", return_value=universe),
            patch.object(
                prices, "_target_asof", return_value=pd.Timestamp("2026-07-01")
            ),
            patch.object(prices, "_fetch_bars_range", return_value=new) as fetch,
        ):
            touched = prices.update_bars_incremental(config, max_workers=1)
        self.assertEqual(touched, {"000001"})
        self.assertEqual(fetch.call_args.args[3], pd.Timestamp("2020-01-01"))
        self.assertEqual(pd.read_parquet(output)["close"].tolist(), [11.0, 20.0])


if __name__ == "__main__":
    unittest.main()
