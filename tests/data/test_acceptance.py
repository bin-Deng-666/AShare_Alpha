"""Acceptance regressions found during the first live catch-up run."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from ashare_alpha.data.sources import securities, prices
from ashare_alpha.data.assets import adjustment_limits
from ashare_alpha.data.pipeline import runner
from ashare_alpha.data.storage import atomic_parquet


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        pool = patch.object(
            runner,
            "ProcessPoolExecutor",
            side_effect=lambda max_workers, **kw: ThreadPoolExecutor(
                max_workers=max_workers
            ),
        )
        pool.start()
        self.addCleanup(pool.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_current_chinext_code_prefix_is_supported(self):
        self.assertTrue(securities.is_a_share("302132"))
        self.assertEqual(securities.exchange_for("302132"), "SZ")
        self.assertEqual(
            adjustment_limits._limit_pct_for_row(
                "302132", False, pd.Timestamp("2026-10-09")
            ),
            0.20,
        )
        self.assertFalse(securities.is_a_share("399001"))
        self.assertFalse(securities.is_a_share("159001"))

    def test_baostock_recovers_expiry_before_and_during_row_read(self):
        from ashare_alpha.data.sources import indicators

        expired = SimpleNamespace(
            error_code="10001001", error_msg="用户未登录", fields=["date", "preclose"]
        )
        partial = Mock(
            error_code="0", error_msg="用户未登录", fields=["date", "preclose"]
        )

        def lose_session():
            partial.error_code = "10001001"
            return False

        partial.next.side_effect = lose_session
        complete = Mock(error_code="0", fields=["date", "preclose"])
        complete.next.side_effect = [True, False]
        complete.get_row_data.return_value = ["2026-10-09", "10"]
        backend = Mock()
        backend.login.return_value = SimpleNamespace(error_code="0")
        query = Mock(side_effect=[expired, partial, complete])
        with patch.object(indicators, "bs", backend):
            frame = indicators.query_baostock_frame(query)
        self.assertEqual(query.call_count, 3)
        self.assertEqual(backend.login.call_count, 2)
        self.assertEqual(
            frame.to_dict("records"), [{"date": "2026-10-09", "preclose": "10"}]
        )

    def test_current_indicators_repair_an_older_raw_date_gap(self):
        from ashare_alpha.data.sources import indicators
        import baostock as bs

        dates = pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"])
        raw = pd.DataFrame({"code": ["000001"] * 3, "date": dates})
        atomic_parquet(raw, self.root / "daily_bars/adjustment=raw/000001.parquet")
        atomic_parquet(raw.iloc[1:], self.root / "daily_indicators/000001.parquet")
        universe = pd.DataFrame({"code": ["000001"]})
        config = {"start_date": "2026-07-01"}
        fields = "date,code,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST,preclose"
        response = Mock(error_code="0", fields=fields.split(","))
        response.next.side_effect = [True, True, True, False]
        response.get_row_data.side_effect = [
            ["2026-07-01", "sz.000001", "1", "1", "0", "10", "1", "1", "1", "0", "0"],
            ["2026-07-02", "sz.000001", "1", "1", "0", "10", "1", "1", "1", "0", "10"],
            ["2026-07-03", "sz.000001", "1", "1", "0", "10", "1", "1", "1", "0", "10"],
        ]
        import threading

        with (
            patch.object(indicators, "PARQUET", self.root),
            patch.object(
                bs, "query_history_k_data_plus", return_value=response
            ) as query,
        ):
            gaps = indicators.indicator_history_gaps(universe, config, dates[-1])
            self.assertEqual(gaps, {"000001": "2026-07-01"})
            result = indicators._process_indicator_stock(
                SimpleNamespace(
                    code="000001", exchange="SZ", list_date=dates[0], delist_date=pd.NaT
                ),
                dates[-1],
                {**config, "_indicator_history_gaps": gaps},
                5,
                fields,
                threading.Lock(),
                [0],
                1,
            )
            self.assertTrue(result[1])
            self.assertEqual(query.call_args.kwargs["start_date"], "2026-07-01")
            self.assertEqual(
                indicators.indicator_history_gaps(universe, config, dates[-1]), {}
            )
        self.assertEqual(
            pd.read_parquet(
                self.root / "daily_indicators/000001.parquet"
            ).date.tolist(),
            dates.tolist(),
        )

    def test_baostock_network_failure_reconnects_and_discards_partial_response(self):
        from ashare_alpha.data.sources import indicators
        import baostock.common.context as context

        broken = Mock(
            error_code="0", error_msg="网络接收错误", fields=["date", "preclose"]
        )
        count = [0]

        def next_row():
            count[0] += 1
            if count[0] == 1:
                return True
            broken.error_code = "10002007"
            return False

        broken.next.side_effect = next_row
        broken.get_row_data.return_value = ["2026-10-08", "999"]
        complete = Mock(error_code="0", fields=["date", "preclose"])
        complete.next.side_effect = [True, False]
        complete.get_row_data.return_value = ["2026-10-09", "10"]
        query = Mock(side_effect=[broken, complete])
        connection = Mock()
        backend = Mock()
        backend.login.return_value = SimpleNamespace(error_code="0")
        with (
            patch.object(context, "default_socket", connection, create=True),
            patch.object(indicators, "bs", backend),
        ):
            result = indicators.query_baostock_frame(query)
            self.assertIsNone(context.default_socket)
        connection.close.assert_called_once()
        backend.login.assert_called_once()
        self.assertEqual(
            result.to_dict("records"), [{"date": "2026-10-09", "preclose": "10"}]
        )

    def test_baostock_does_not_relogin_for_invalid_query(self):
        from ashare_alpha.data.sources import indicators

        query = Mock(
            return_value=SimpleNamespace(
                error_code="200000", error_msg="invalid field", fields=[]
            )
        )
        backend = Mock()
        with patch.object(indicators, "bs", backend):
            with self.assertRaisesRegex(RuntimeError, "invalid field"):
                indicators.query_baostock_frame(query)
        query.assert_called_once()
        backend.login.assert_not_called()

    def test_current_universe_keeps_officially_listed_suspended_security(self):
        quotes = pd.DataFrame({"code": ["600000"], "current_name": ["浦发银行"]})
        official = pd.DataFrame(
            {
                "code": ["920305"],
                "current_name": ["广道数字"],
                "list_date": pd.to_datetime(["2021-11-15"]),
            }
        )
        prior = pd.DataFrame(
            {
                "code": ["600000"],
                "list_date": pd.to_datetime(["1999-11-10"]),
                "status": ["listed"],
            }
        )
        atomic_parquet(prior, self.root / "security_master/security_master.parquet")
        with (
            patch.object(securities, "PARQUET", self.root),
            patch.object(securities, "fetch_market_list", return_value=quotes),
            patch.object(securities, "fetch_exchange_listings", return_value=official),
        ):
            frame = securities.fetch_current_universe()
        self.assertEqual(set(frame.code), {"600000", "920305"})
        self.assertFalse(frame.list_date.isna().any())

    def test_cdr_omitted_by_general_quote_node_is_independently_verified(self):
        prior = pd.DataFrame(
            {
                "code": ["689009"],
                "list_date": pd.to_datetime(["2020-10-29"]),
                "status": ["inactive"],
            }
        )
        atomic_parquet(prior, self.root / "security_master/security_master.parquet")
        fields = [""] * 40
        fields[1] = "九号公司-WD"
        fields[2] = "689009"
        fields[6] = "100"
        fields[30] = "20261009150000"
        response = Mock()
        response.text = "~".join(fields)
        quotes = pd.DataFrame({"code": ["600000"], "current_name": ["浦发银行"]})
        official = pd.DataFrame(
            {
                "code": ["600000"],
                "list_date": pd.to_datetime(["1999-11-10"]),
                "current_name": ["浦发银行"],
            }
        )
        with (
            patch.object(securities, "PARQUET", self.root),
            patch.object(securities, "fetch_market_list", return_value=quotes),
            patch.object(securities, "fetch_exchange_listings", return_value=official),
            patch.object(securities.requests, "get", return_value=response) as quote,
        ):
            frame = securities.fetch_current_universe()
        self.assertEqual(set(frame.code), {"600000", "689009"})
        quote.assert_called_once()
        fields[6] = "0"
        response.text = "~".join(fields)
        with (
            patch.object(securities, "PARQUET", self.root),
            patch.object(securities, "fetch_market_list", return_value=quotes),
            patch.object(securities, "fetch_exchange_listings", return_value=official),
            patch.object(securities.requests, "get", return_value=response),
        ):
            self.assertEqual(
                set(securities.fetch_current_universe().code), {"600000", "689009"}
            )
        fields[30] = ""
        response.text = "~".join(fields)
        with (
            patch.object(securities, "PARQUET", self.root),
            patch.object(securities, "fetch_market_list", return_value=quotes),
            patch.object(securities, "fetch_exchange_listings", return_value=official),
            patch.object(securities.requests, "get", return_value=response),
        ):
            with self.assertRaisesRegex(RuntimeError, "verify current CDR"):
                securities.fetch_current_universe()

    def test_universe_refresh_preserves_inactive_history_and_cached_identifiers(self):
        prior = pd.DataFrame(
            {
                "code": ["600000", "920999", "000001"],
                "name": ["浦发", "历史证券", "退市证券"],
                "list_date": pd.to_datetime(["1999-11-10"] * 3),
                "delist_date": pd.to_datetime([None, None, "2020-01-01"]),
                "exchange": ["SH", "BJ", "SZ"],
                "status": ["listed", "inactive", "delisted"],
                "org_id": ["real-sh", "real-bj", "real-sz"],
                "cninfo_name": ["浦发", "历史证券", "退市证券"],
                "source": ["old"] * 3,
            }
        )
        atomic_parquet(prior, self.root / "security_master/security_master.parquet")
        current = pd.DataFrame(
            {
                "code": ["600000"],
                "current_name": ["浦发银行"],
                "exchange": ["SH"],
                "list_date": pd.to_datetime(["1999-11-10"]),
            }
        )
        with (
            patch.object(securities, "PARQUET", self.root),
            patch.object(securities, "STATE", self.root / "state"),
            patch.object(securities, "fetch_current_universe", return_value=current),
            patch.object(
                securities, "fetch_delisted_universe", return_value=pd.DataFrame()
            ),
            patch.object(
                securities, "fetch_cninfo_map", side_effect=RuntimeError("unavailable")
            ),
            patch.object(securities, "retry", side_effect=lambda fn, *args, **kw: fn()),
        ):
            frame = securities.build_universe({"include_delisted": True})
        self.assertEqual(set(frame.code), set(prior.code))
        self.assertEqual(frame.set_index("code").loc["600000", "org_id"], "real-sh")
        self.assertEqual(frame.set_index("code").loc["920999", "status"], "inactive")

    def test_market_pagination_rejects_count_and_key_changes(self):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.return_value.json.return_value = "1001"
        broken = [False]

        def response(*args, **kwargs):
            page = kwargs["params"]["page"]
            start = (page - 1) * 100
            rows = [
                {"code": str(600000 + i), "name": str(i)}
                for i in range(start, min(start + 100, 1001))
            ]
            if broken[0] and page == 11:
                rows[0]["code"] = "600000"
            result = Mock()
            result.json.return_value = rows
            return result

        with (
            patch.object(securities.requests, "Session", return_value=session),
            patch.object(securities.requests, "get", side_effect=response),
        ):
            self.assertEqual(len(securities.fetch_market_list()), 1001)
            broken[0] = True
            with self.assertRaisesRegex(RuntimeError, "count/keys"):
                securities.fetch_market_list()

    def test_missing_validated_events_never_fall_back_to_distribution_plans(self):
        dates = pd.to_datetime(["2020-01-02", "2020-01-03"])
        raw = pd.DataFrame(
            {
                "code": ["000001"] * 2,
                "date": dates,
                "open": [10.0, 10.0],
                "high": [10.0, 10.0],
                "low": [10.0, 10.0],
                "close": [10.0, 10.0],
            }
        )
        atomic_parquet(raw, self.root / "daily_bars/adjustment=raw/000001.parquet")
        atomic_parquet(
            pd.DataFrame(
                {
                    "code": ["000001"],
                    "ex_date": [dates[1]],
                    "cash_dividend_per_10": [10.0],
                }
            ),
            self.root / "corporate_actions/000001.parquet",
        )
        with (
            patch.object(adjustment_limits, "PARQUET", self.root),
            patch.object(adjustment_limits, "STATE", self.root / "state"),
        ):
            for builder, report, layer in [
                (
                    adjustment_limits.build_qfq_from_corporate_actions,
                    "corporate_action_qfq_coverage.json",
                    "daily_bars/adjustment=qfq",
                ),
                (
                    adjustment_limits.build_limit_prices,
                    "limit_prices_coverage.json",
                    "limit_prices",
                ),
            ]:
                done = []
                builder({}, codes=["000001"], force=True, on_code_done=done.append)
                self.assertEqual(done, [])
                self.assertEqual(
                    len(
                        json.loads((self.root / "state" / report).read_text())[
                            "failures"
                        ]
                    ),
                    1,
                )
                self.assertFalse((self.root / layer / "000001.parquet").exists())

    def test_unknown_st_has_no_assumed_limit_and_missing_reference_is_distinct(self):
        dates = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
        raw = pd.DataFrame(
            {
                "code": ["000001"] * 3,
                "date": dates,
                "high": [10.0] * 3,
                "low": [10.0] * 3,
                "close": [10.0] * 3,
            }
        )
        atomic_parquet(raw, self.root / "daily_bars/adjustment=raw/000001.parquet")
        atomic_parquet(
            pd.DataFrame({"date": [dates[0], dates[2]], "isST": [0, 0]}),
            self.root / "daily_indicators/000001.parquet",
        )
        atomic_parquet(
            pd.DataFrame({"ex_date": pd.Series(dtype="datetime64[ns]")}),
            self.root / "corporate_action_events/000001.parquet",
        )
        with (
            patch.object(adjustment_limits, "PARQUET", self.root),
            patch.object(adjustment_limits, "STATE", self.root / "state"),
        ):
            adjustment_limits.build_limit_prices({}, codes=["000001"], force=True)
        result = pd.read_parquet(self.root / "limit_prices/000001.parquet")
        self.assertEqual(
            result.limit_validation_status.tolist(),
            ["missing_reference_price", "unknown_st_status", "valid"],
        )
        self.assertTrue(pd.isna(result.iloc[1].limit_pct))
        self.assertTrue(pd.isna(result.iloc[1].is_limit_up))
        self.assertAlmostEqual(result.iloc[2].limit_pct, 0.1)
        self.assertIsNone(
            adjustment_limits._limit_pct_for_row("000001", pd.NA, dates[0])
        )
        self.assertEqual(
            adjustment_limits._limit_pct_for_row(
                "302132", pd.NA, pd.Timestamp("2026-10-09")
            ),
            0.2,
        )
        self.assertEqual(
            adjustment_limits._limit_pct_for_row("920238", pd.NA, dates[0]), 0.3
        )

    def test_beijing_first_session_and_declared_dividend_limits(self):
        dates = pd.to_datetime(["2026-07-24", "2026-07-27"])
        atomic_parquet(
            pd.DataFrame({"code": ["920238"], "list_date": [dates[0]]}),
            self.root / "security_master/security_master.parquet",
        )
        atomic_parquet(
            pd.DataFrame({"calendar_date": dates, "is_trading_day": [1, 1]}),
            self.root / "reference/trade_calendar.parquet",
        )
        atomic_parquet(
            pd.DataFrame(
                {
                    "date": dates,
                    "high": [12.0, 11.0],
                    "low": [8.0, 8.0],
                    "close": [10.0, 9.0],
                }
            ),
            self.root / "daily_bars/adjustment=raw/920238.parquet",
        )
        atomic_parquet(
            pd.DataFrame(
                {
                    "code": ["920238"],
                    "ex_date": [dates[1]],
                    "cash_dividend_per_10": [10.0],
                    "bonus_transfer_per_10": [0.0],
                }
            ),
            self.root / "corporate_actions/920238.parquet",
        )
        done = []
        with (
            patch.object(adjustment_limits, "PARQUET", self.root),
            patch.object(adjustment_limits, "STATE", self.root / "state"),
        ):
            adjustment_limits.build_limit_prices(
                {}, codes=["920238"], force=True, on_code_done=done.append
            )
        frame = pd.read_parquet(self.root / "limit_prices/920238.parquet")
        self.assertEqual(done, ["920238"])
        self.assertTrue(pd.isna(frame.iloc[0].limit_up))
        self.assertEqual(frame.iloc[1].reference_price, 9.0)
        self.assertAlmostEqual(frame.iloc[1].limit_up, 11.70)
        self.assertAlmostEqual(frame.iloc[1].limit_down, 6.30)
        self.assertEqual(
            frame.iloc[1].reference_price_source, "declared_distribution_formula"
        )

    def test_vendor_qfq_revision_refreshes_older_history(self):
        dates = pd.to_datetime(["2026-01-02", "2026-06-30", "2026-07-01"])

        def bars(values):
            return pd.DataFrame(
                {
                    "date": dates[: len(values)],
                    "open": values,
                    "high": values,
                    "low": values,
                    "close": values,
                    "code": ["920000"] * len(values),
                }
            )

        old = bars([9.0, 10.0])
        raw = bars([9.0, 10.0, 11.0])
        revised = bars([8.0, 9.0, 11.0])
        atomic_parquet(old, self.root / "daily_bars/adjustment=qfq/920000.parquet")
        atomic_parquet(old, self.root / "daily_bars/adjustment=raw/920000.parquet")
        master = pd.DataFrame(
            {
                "code": ["920000"],
                "exchange": ["BJ"],
                "list_date": pd.to_datetime(["2025-01-01"]),
                "delist_date": [pd.NaT],
            }
        )
        calls = []

        def fetch(config, code, exchange, start, end, adjustment, *rest):
            calls.append((adjustment, start))
            if adjustment == "raw":
                return raw.iloc[1:].copy()
            return (
                revised.copy()
                if start == pd.Timestamp("2026-01-01")
                else revised.iloc[1:].copy()
            )

        with (
            patch.object(prices, "PARQUET", self.root),
            patch.object(prices, "STATE", self.root / "state"),
            patch.object(prices, "_listed_universe", return_value=master),
            patch.object(prices, "_target_asof", return_value=dates[-1]),
            patch.object(prices, "_fetch_bars_range", side_effect=fetch),
        ):
            prices.update_bars_incremental(
                {
                    "start_date": "2026-01-01",
                    "bar_adjustments": ["raw", "qfq"],
                    "derive_qfq": True,
                    "eastmoney_min_interval_seconds": 0,
                },
                max_workers=1,
            )
        result = pd.read_parquet(self.root / "daily_bars/adjustment=qfq/920000.parquet")
        self.assertEqual(result.close.tolist(), [8.0, 9.0, 11.0])
        self.assertEqual([item[0] for item in calls], ["raw", "qfq", "qfq"])
        self.assertEqual(calls[-1][1], pd.Timestamp("2026-01-01"))

    def test_live_run_record_identifies_current_task_before_handler_executes(self):
        target = self.root / "output.parquet"

        def execute(*args):
            report = json.loads(next((self.root / "runs").glob("*.json")).read_text())
            self.assertEqual(report["status"], "running")
            self.assertEqual(report["current_task"], "sample")
            self.assertGreater(report["pid"], 0)
            atomic_parquet(pd.DataFrame({"v": [1]}), target)
            return {}

        with (
            patch.object(runner, "STATE_ROOT", self.root),
            patch.object(
                runner,
                "select_tasks",
                return_value=[SimpleNamespace(name="sample", dependencies=())],
            ),
            patch.object(
                runner,
                "build_plan",
                return_value=[{"task": "sample", "action": "refresh", "input": "sig"}],
            ),
            patch.object(runner, "task_paths", return_value=([], [target])),
            patch.object(runner, "execute", side_effect=execute),
        ):
            report = runner.run_update({"end_date": "2026-10-10"})
        self.assertEqual(report["status"], "success")
        self.assertNotIn("current_task", report)
        self.assertIn("finished_at", report)

    def test_core_coverage_rejects_stale_indicator_file(self):
        from ashare_alpha.data.research.coverage import validate_core_coverage

        dates = pd.to_datetime(["2026-10-08", "2026-10-09"])
        frame = pd.DataFrame({"code": ["000001"] * 2, "date": dates})
        atomic_parquet(
            pd.DataFrame({"code": ["000001"]}),
            self.root / "security_master/security_master.parquet",
        )
        for layer in [
            "daily_bars/adjustment=raw",
            "daily_bars/adjustment=qfq",
            "shares_daily",
            "limit_prices",
        ]:
            atomic_parquet(frame, self.root / layer / "000001.parquet")
        atomic_parquet(frame.iloc[:1], self.root / "daily_indicators/000001.parquet")
        with self.assertRaisesRegex(
            RuntimeError, "daily_indicators missing raw bar key"
        ):
            validate_core_coverage(self.root)
        atomic_parquet(frame, self.root / "daily_indicators/000001.parquet")
        validate_core_coverage(self.root)

    def test_successful_source_response_records_remaining_indicator_gap_without_fabricating_rows(
        self,
    ):
        from ashare_alpha.data.sources import indicators
        from ashare_alpha.data.research.coverage import confirmed_indicator_gaps
        import baostock as bs

        dates = pd.to_datetime(["2026-10-08", "2026-10-09"])
        raw = pd.DataFrame({"code": ["000001"] * 2, "date": dates})
        atomic_parquet(raw, self.root / "daily_bars/adjustment=raw/000001.parquet")
        atomic_parquet(raw.iloc[1:], self.root / "daily_indicators/000001.parquet")
        universe = pd.DataFrame(
            {
                "code": ["000001"],
                "exchange": ["SZ"],
                "list_date": [dates[0]],
                "delist_date": [pd.NaT],
            }
        )
        fields = "date,code,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST,preclose"
        response = Mock(error_code="0", fields=fields.split(","))
        response.next.side_effect = [True, False]
        response.get_row_data.return_value = [
            "2026-10-09",
            "sz.000001",
            "1",
            "1",
            "0",
            "10",
            "1",
            "1",
            "1",
            "0",
            "10",
        ]
        with (
            patch.object(indicators, "PARQUET", self.root),
            patch.object(indicators, "STATE", self.root / "state"),
            patch.object(indicators, "_listed_universe", return_value=universe),
            patch.object(indicators, "_target_asof", return_value=dates[-1]),
            patch(
                "concurrent.futures.ProcessPoolExecutor",
                side_effect=lambda max_workers, **kw: ThreadPoolExecutor(
                    max_workers=max_workers
                ),
            ),
            patch.object(bs, "login", return_value=SimpleNamespace(error_code="0")),
            patch.object(bs, "logout"),
            patch.object(bs, "query_history_k_data_plus", return_value=response),
        ):
            indicators.update_indicators_incremental(
                {"start_date": "2026-10-08", "end_date": "2026-10-10"}, max_workers=1
            )
        result = pd.read_parquet(self.root / "daily_indicators/000001.parquet")
        self.assertEqual(result.date.tolist(), [dates[1]])
        known = confirmed_indicator_gaps(self.root, self.root / "state")
        self.assertEqual(known.date.tolist(), [dates[0]])
        report = json.loads(
            (self.root / "state/daily_indicator_update.json").read_text()
        )
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["source_missing_dates"], {"000001": ["2026-10-08"]})

    def test_source_checked_indicator_holes_are_visible_and_signature_bound(self):
        from ashare_alpha.data.research.coverage import (
            validate_core_coverage,
            confirmed_indicator_gaps,
        )
        from ashare_alpha.data.pipeline.receipts import fingerprint
        from ashare_alpha.data.storage import atomic_json

        dates = pd.to_datetime(["2026-10-08", "2026-10-09"])
        frame = pd.DataFrame({"code": ["000001"] * 2, "date": dates})
        atomic_parquet(
            pd.DataFrame({"code": ["000001"]}),
            self.root / "security_master/security_master.parquet",
        )
        for layer in [
            "daily_bars/adjustment=raw",
            "daily_bars/adjustment=qfq",
            "shares_daily",
            "limit_prices",
        ]:
            atomic_parquet(frame, self.root / layer / "000001.parquet")
        raw = self.root / "daily_bars/adjustment=raw/000001.parquet"
        indicator = self.root / "daily_indicators/000001.parquet"
        atomic_parquet(frame.iloc[:1], indicator)
        state = self.root / "state"
        with self.assertRaisesRegex(
            RuntimeError, "daily_indicators missing raw bar key"
        ):
            validate_core_coverage(self.root, state)
        atomic_json(
            {
                "000001": {
                    "source": "baostock",
                    "checked_through": "2026-10-10",
                    "missing_dates": ["2026-10-09"],
                    "raw": fingerprint([raw]),
                    "indicator": fingerprint([indicator]),
                }
            },
            state / "indicator_source_gaps.json",
        )
        validate_core_coverage(self.root, state)
        self.assertEqual(
            confirmed_indicator_gaps(self.root, state).date.tolist(), [dates[1]]
        )
        atomic_parquet(frame.assign(extra=1), raw)
        self.assertTrue(confirmed_indicator_gaps(self.root, state).empty)
        with self.assertRaisesRegex(
            RuntimeError, "daily_indicators missing raw bar key"
        ):
            validate_core_coverage(self.root, state)

    def test_share_history_keeps_unknown_dates_without_future_backfill(self):
        from ashare_alpha.data.assets import shares

        dates = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
        atomic_parquet(
            pd.DataFrame(
                {"code": ["000001"], "list_date": [dates[0]], "delist_date": [pd.NaT]}
            ),
            self.root / "security_master/security_master.parquet",
        )
        atomic_parquet(
            pd.DataFrame({"calendar_date": dates, "is_trading_day": [1, 1, 1]}),
            self.root / "reference/trade_calendar.parquet",
        )
        atomic_parquet(
            pd.DataFrame({"code": ["000001"] * 3, "date": dates, "close": [10.0] * 3}),
            self.root / "daily_bars/adjustment=raw/000001.parquet",
        )
        atomic_parquet(
            pd.DataFrame(
                {
                    "code": ["000001"],
                    "end_date": [dates[2]],
                    "total_shares": [100.0],
                    "float_shares": [10.0],
                }
            ),
            self.root / "equity_structure/000001.parquet",
        )
        with (
            patch.object(shares, "PARQUET", self.root),
            patch.object(shares, "STATE", self.root / "state"),
        ):
            shares.build_shares_daily(
                {"start_date": "2020-01-01", "end_date": "2020-01-06"},
                codes=["000001"],
                force=True,
            )
        result = pd.read_parquet(self.root / "shares_daily/000001.parquet")
        self.assertEqual(result.date.tolist(), dates.tolist())
        self.assertTrue(result.iloc[:2].total_shares.isna().all())
        self.assertTrue(result.iloc[:2].float_market_cap.isna().all())
        self.assertEqual(result.iloc[2].total_market_cap, 1000.0)
        self.assertEqual(result.iloc[2].float_market_cap, 100.0)

    def test_catalog_retains_unknown_st_for_source_missing_indicator(self):
        import duckdb
        from ashare_alpha.data.research import catalog

        date = pd.Timestamp("2026-10-09")
        raw = pd.DataFrame(
            {
                "code": ["000001"],
                "date": [date],
                "exchange": ["SZ"],
                "source": ["tencent"],
                "open": [10.0],
                "high": [11.0],
                "low": [9.0],
                "close": [10.0],
                "volume": [100.0],
                "amount": [1000.0],
            }
        )
        indicator = pd.DataFrame(
            {
                "code": ["000001"],
                "date": [date - pd.Timedelta(days=1)],
                "source": ["baostock"],
                "turn": [1.0],
                "tradestatus": [1],
                "pctChg": [0.0],
                "peTTM": [10.0],
                "pbMRQ": [1.0],
                "psTTM": [1.0],
                "pcfNcfTTM": [1.0],
                "isST": [0],
            }
        )
        atomic_parquet(raw, self.root / "daily_bars/adjustment=raw/000001.parquet")
        atomic_parquet(indicator, self.root / "daily_indicators/000001.parquet")
        with patch.object(catalog, "PARQUET", self.root), duckdb.connect() as con:
            result = con.execute(catalog._catalog_daily_view_sql("raw")).fetchdf()
        self.assertTrue(pd.isna(result.iloc[0].is_st))
        self.assertTrue(pd.isna(result.iloc[0].trade_status))
        self.assertTrue(pd.isna(result.iloc[0].indicator_source))
        self.assertTrue(pd.isna(result.iloc[0].is_limit_up))
        self.assertTrue(pd.isna(result.iloc[0].total_market_cap))
        self.assertTrue(pd.isna(result.iloc[0].float_market_cap))
        # A known float capitalization is not evidence of total capitalization.
        atomic_parquet(
            pd.DataFrame(
                {
                    "code": ["000001"],
                    "date": [date],
                    "total_shares": [pd.NA],
                    "float_shares": [100.0],
                    "total_market_cap": [pd.NA],
                    "float_market_cap": [1000.0],
                }
            ),
            self.root / "shares_daily/000001.parquet",
        )
        with patch.object(catalog, "PARQUET", self.root), duckdb.connect() as con:
            result = con.execute(catalog._catalog_daily_view_sql("raw")).fetchdf()
        self.assertTrue(pd.isna(result.iloc[0].total_market_cap))
        self.assertEqual(result.iloc[0].float_market_cap, 1000.0)
        missing_limits = {
            name: None
            for name in [
                "limit_up",
                "limit_down",
                "limit_pct",
                "is_limit_up",
                "is_limit_down",
                "reference_price",
                "reference_price_source",
                "cash_dividend_per_share",
                "share_distribution_ratio",
                "rights_issue_ratio",
                "rights_issue_price",
                "corporate_action_adjusted",
                "corporate_action_formula_status",
            ]
        }
        atomic_parquet(
            pd.DataFrame(
                [
                    {
                        **missing_limits,
                        "code": "000001",
                        "date": date,
                        "limit_validation_status": "unknown_st_status",
                    }
                ]
            ),
            self.root / "limit_prices/000001.parquet",
        )
        with patch.object(catalog, "PARQUET", self.root), duckdb.connect() as con:
            result = con.execute(catalog._catalog_daily_view_sql("raw")).fetchdf()
        self.assertTrue(pd.isna(result.iloc[0].is_limit_up))
        self.assertTrue(pd.isna(result.iloc[0].is_limit_down))

    def test_status_marks_abandoned_run_and_historical_audit(self):
        from ashare_alpha.data.pipeline.status import read_status
        from ashare_alpha.data.storage import atomic_json, writer_lock

        state = self.root / "state"
        data = self.root / "data"
        atomic_json(
            {"status": "review", "issues": ["missing dates"]},
            data / "quality_report.json",
        )
        atomic_json(
            {"status": "running", "current_task": "prices"}, state / "runs/test.json"
        )
        report = read_status(state_root=state, data_root=data)
        self.assertEqual(report["latest_run"]["status"], "interrupted")
        self.assertFalse(report["quality"]["current_for_latest_run"])
        with writer_lock(state / "data_update.lock"):
            report = read_status(state_root=state, data_root=data)
            self.assertTrue(report["writer_active"])
            self.assertEqual(report["latest_run"]["status"], "running")

    def test_acceptance_requires_success_repeat_and_current_quality(self):
        from ashare_alpha.data.pipeline import acceptance
        from ashare_alpha.data.storage import atomic_json

        success = {
            "batch_id": "first",
            "status": "success",
            "seconds": 1.0,
            "results": [{"task": "prices", "status": "success"}],
        }
        skipped = {
            "batch_id": "second",
            "status": "success",
            "seconds": 0.1,
            "results": [{"task": "prices", "status": "skipped"}],
        }
        failed = {
            "batch_id": "failed",
            "status": "failed",
            "seconds": 1.0,
            "results": [{"task": "prices", "status": "failed"}],
        }

        def audit(config):
            atomic_json(
                {"status": "pass", "issues": []}, self.root / "data/quality_report.json"
            )

        with (
            patch.object(acceptance, "STATE_ROOT", self.root / "state"),
            patch.object(acceptance, "DATA_ROOT", self.root / "data"),
            patch.object(acceptance, "validate_dataset", side_effect=audit),
            patch.object(acceptance, "build_manifest"),
            patch.object(
                acceptance, "run_update", side_effect=[failed, success, skipped]
            ) as update,
        ):
            report = acceptance.run_acceptance({"end_date": "2026-10-10"})
        self.assertEqual(update.call_count, 3)
        self.assertEqual(report["status"], "pass")
        with (
            patch.object(acceptance, "STATE_ROOT", self.root / "state"),
            patch.object(acceptance, "DATA_ROOT", self.root / "data"),
            patch.object(acceptance, "validate_dataset", side_effect=audit),
            patch.object(acceptance, "build_manifest"),
            patch.object(acceptance, "run_update", side_effect=[failed, failed]),
        ):
            report = acceptance.run_acceptance({"end_date": "2026-10-10"})
        self.assertEqual(report["status"], "review")
        self.assertNotIn("repeat", report)


if __name__ == "__main__":
    unittest.main()
