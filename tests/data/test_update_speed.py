"""Regression tests for smaller requests, scoped versions and durable handoff."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_alpha.data.storage import atomic_parquet, read_json
from ashare_alpha.data.sources import market_events
from ashare_alpha.data.pipeline import identity, source_receipts
from ashare_alpha.data.sources import financial_statements


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        atomic_parquet(
            pd.DataFrame({"code": ["000001", "000002"]}),
            self.root / "security_master/security_master.parquet",
        )
        self.config = {
            "start_date": "2010-01-01",
            "end_date": "2026-10-10",
            "retries": 1,
            "retry_base_seconds": 0,
            "eastmoney_min_interval_seconds": 0,
            "event_overlap_days": 7,
            "event_full_refresh_days": 30,
        }
        self.original = pd.DataFrame(
            {
                "code": ["000001", "000002"],
                "end_date": pd.to_datetime(["2020-01-01"] * 2),
                "notice_date": pd.to_datetime(["2020-01-01"] * 2),
                "total_shares": [10.0, 20.0],
            }
        )
        self.current = self.original.copy()
        self.detector = self.original.iloc[:0].copy()
        self.calls = []
        self.fail = set()
        self.patches = [
            patch.object(market_events, "PARQUET_ROOT", self.root),
            patch.object(market_events, "STATE_ROOT", self.root / "state"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def fetch(self, session, code, throttle, **kw):
        self.calls.append((code, kw.get("date_field"), kw.get("refresh_from")))
        if code in self.fail:
            raise RuntimeError("provider failed")
        if kw.get("refresh_from"):
            return self.detector.copy()
        return (
            self.current.copy()
            if code is None
            else self.current[self.current.code.eq(code)].copy()
        )

    def update(self, day="2026-10-10", **kw):
        return market_events.refresh_market_events(
            "equity_structure", self.fetch, {**self.config, "end_date": day}, **kw
        )

    def test_malformed_market_success_cannot_advance_verified_receipt(self):
        from types import SimpleNamespace
        from ashare_alpha.data.sources.equity_structure import fetch_equity_structure

        initial = {
            "success": True,
            "result": {
                "count": 2,
                "pages": 1,
                "data": [
                    {
                        "SECURITY_CODE": code,
                        "END_DATE": "2020-01-01",
                        "NOTICE_DATE": "2020-01-01",
                        "TOTAL_SHARES": 10,
                    }
                    for code in ["000001", "000002"]
                ],
            },
        }
        payload = [initial]

        class Session:
            def __init__(self):
                self.headers = {}

            def get(self, *args, **kwargs):
                return SimpleNamespace(
                    raise_for_status=lambda: None, json=lambda: payload[0]
                )

        with patch.object(market_events.requests, "Session", Session):
            market_events.refresh_market_events(
                "equity_structure", fetch_equity_structure, self.config
            )
            receipt = market_events.market_receipt_path("equity_structure")
            before = receipt.read_bytes()
            old_asset = (self.root / "equity_structure/000001.parquet").read_bytes()
            for broken in [
                {"success": True, "result": {"data": []}},
                {
                    "success": True,
                    "result": {
                        "count": 1,
                        "data": [
                            {"SECURITY_CODE": "000001", "NOTICE_DATE": "2026-10-11"}
                        ],
                    },
                },
                {
                    "success": True,
                    "result": {
                        "count": 1,
                        "data": [
                            {"END_DATE": "2026-10-11", "NOTICE_DATE": "2026-10-11"}
                        ],
                    },
                },
            ]:
                with self.subTest(payload=broken):
                    payload[0] = broken
                    with self.assertRaises((RuntimeError, ValueError)):
                        market_events.refresh_market_events(
                            "equity_structure",
                            fetch_equity_structure,
                            {**self.config, "end_date": "2026-10-11"},
                        )
                    self.assertEqual(before, receipt.read_bytes())
                    self.assertEqual(
                        old_asset,
                        (self.root / "equity_structure/000001.parquet").read_bytes(),
                    )

    def test_market_internal_codes_stay_outside_registered_asset_scope(self):
        internal = self.original.iloc[:1].copy()
        internal["code"] = "A22444"
        self.current = pd.concat([self.original, internal], ignore_index=True)
        self.update()
        self.assertFalse((self.root / "equity_structure/A22444.parquet").exists())
        self.detector = internal.copy()
        self.assertEqual(self.update("2026-10-11")["processed"], 0)
        receipt = market_events.market_receipt_path("equity_structure")
        before = receipt.read_bytes()
        self.current = internal
        with self.assertRaisesRegex(RuntimeError, "no registered securities"):
            self.update("2026-11-11")
        self.assertEqual(before, receipt.read_bytes())

    def test_wrong_issuer_history_is_not_published(self):
        self.current = self.original.iloc[1:].copy()
        with self.assertRaisesRegex(RuntimeError, "issuer histories failed"):
            market_events.refresh_market_events(
                "equity_structure",
                self.wrong_issuer_fetch,
                self.config,
                codes=["000001"],
            )
        self.assertFalse((self.root / "equity_structure/000001.parquet").exists())

    def wrong_issuer_fetch(self, *args, **kwargs):
        return self.original.iloc[1:].copy()

    def test_initial_subset_requests_only_that_issuer(self):
        result = self.update(codes=["000001"])
        self.assertEqual(result["queries"], 1)
        self.assertEqual([call[0] for call in self.calls], ["000001"])
        self.assertTrue((self.root / "equity_structure/000001.parquet").exists())
        self.assertFalse((self.root / "equity_structure/000002.parquet").exists())
        self.assertFalse(market_events.market_receipt_path("equity_structure").exists())

    def test_incremental_replaces_only_changed_issuer_and_retains_others(self):
        self.update()
        unchanged = self.root / "equity_structure/000002.parquet"
        before = unchanged.stat().st_mtime_ns
        self.current = self.original.copy()
        self.current.loc[0, "end_date"] = pd.Timestamp("2019-12-31")
        self.current.loc[0, "notice_date"] = pd.Timestamp("2026-10-11")
        self.current.loc[0, "total_shares"] = 7.0
        self.detector = self.current.iloc[:1].copy()
        self.calls.clear()
        result = self.update("2026-10-11")
        self.assertEqual(result["mode"], "incremental")
        self.assertEqual(result["changed_codes"], ["000001"])
        self.assertEqual([call[0] for call in self.calls], [None, None, "000001"])
        self.assertEqual(before, unchanged.stat().st_mtime_ns)
        result = pd.read_parquet(self.root / "equity_structure/000001.parquet")
        self.assertEqual(result.total_shares.tolist(), [7.0])
        self.assertEqual(result.end_date.tolist(), [pd.Timestamp("2019-12-31")])

    def test_identical_detector_and_empty_window_do_not_refresh_issuers(self):
        self.update()
        self.detector = self.original.copy()
        self.calls.clear()
        result = self.update("2026-10-11")
        self.assertEqual(result["processed"], 0)
        self.assertEqual(len(self.calls), 2)
        self.detector = self.original.iloc[:0]
        self.calls.clear()
        result = self.update("2026-10-12")
        self.assertEqual(result["processed"], 0)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(
            read_json(market_events.market_receipt_path("equity_structure"))[
                "checked_through"
            ],
            "2026-10-12",
        )

    def test_legacy_bj_detector_requires_suffix_and_name_agreement(self):
        master = pd.DataFrame(
            {"code": ["920879"], "name": ["基康技术"], "cninfo_name": ["基康技术"]}
        )
        source = pd.DataFrame(
            {"code": ["830879", "830879"], "name": ["基康技术", "另一公司"]}
        )
        # Conflicting names for the same source code must not be guessed.
        matched = market_events.canonical_detector(source.iloc[:1], master)
        self.assertEqual(matched.code.tolist(), ["920879"])
        unmatched = market_events.canonical_detector(source.iloc[1:], master)
        self.assertEqual(unmatched.code.tolist(), ["830879"])
        self.assertEqual(
            market_events.canonical_detector(source, master).code.tolist(),
            ["830879", "830879"],
        )

    def test_numeric_dtype_difference_keeps_existing_event_file_unchanged(self):
        self.update()
        path = self.root / "equity_structure/000001.parquet"
        before = path.stat().st_mtime_ns
        self.current = self.original.copy()
        self.current["total_shares"] = self.current.total_shares.astype("int64")
        result = self.update("2026-10-11", force=True)
        self.assertEqual(result["changed_codes"], [])
        self.assertEqual(before, path.stat().st_mtime_ns)

    def test_new_security_bootstraps_full_history_and_empty_declared_history_is_valid(
        self,
    ):
        self.update()
        atomic_parquet(
            pd.DataFrame({"code": ["000001", "000002", "000003"]}),
            self.root / "security_master/security_master.parquet",
        )
        self.calls.clear()
        result = self.update("2026-10-11")
        self.assertEqual(result["processed"], 1)
        self.assertIn(("000003", None, None), self.calls)
        self.assertTrue(
            pd.read_parquet(self.root / "equity_structure/000003.parquet").empty
        )

    def test_partial_failure_does_not_advance_date_and_resume_skips_published_issuer(
        self,
    ):
        self.update()
        self.current = self.original.assign(
            notice_date=pd.Timestamp("2026-10-11"), total_shares=[11.0, 22.0]
        )
        self.detector = self.current.copy()
        self.fail = {"000002"}
        with self.assertRaisesRegex(RuntimeError, "failed"):
            self.update("2026-10-11", workers=1)
        state = read_json(market_events.market_receipt_path("equity_structure"))
        self.assertEqual(state["checked_through"], "2026-10-10")
        self.assertEqual(set(state["progress"]), {"000001"})
        self.fail.clear()
        self.calls.clear()
        result = self.update("2026-10-11", workers=1)
        self.assertEqual(result["mode"], "incremental")
        self.assertNotIn(("000001", None, None), self.calls)
        self.assertIn(("000002", None, None), self.calls)

    def test_periodic_full_and_historical_repair_reconcile_unannounced_deletion(self):
        self.update()
        self.current = self.original.iloc[:1]
        result = self.update("2026-11-10")
        self.assertEqual(result["mode"], "full")
        self.assertTrue(
            pd.read_parquet(self.root / "equity_structure/000002.parquet").empty
        )
        result = market_events.refresh_market_events(
            "equity_structure",
            self.fetch,
            {**self.config, "end_date": "2026-11-11", "refresh_from": "2019-01-01"},
        )
        self.assertEqual(result["mode"], "full")

    def test_empty_full_response_and_subset_cannot_overwrite_verified_market(self):
        self.update()
        path = market_events.market_receipt_path("equity_structure")
        before = path.read_bytes()
        self.update("2026-10-11", codes=["000001"])
        self.assertEqual(before, path.read_bytes())
        self.current = self.original.iloc[:0]
        with self.assertRaisesRegex(RuntimeError, "Empty full-market"):
            self.update("2026-11-11")
        self.assertEqual(before, path.read_bytes())


class WindowTests(unittest.TestCase):
    def test_notice_detector_includes_future_ex_dates_without_publishing_them(self):
        from types import SimpleNamespace
        from ashare_alpha.data.sources import corporate_actions

        calls = []
        payload = {
            "success": True,
            "result": {
                "count": 1,
                "pages": 1,
                "data": [
                    {
                        "SECURITY_CODE": "000001",
                        "EX_DIVIDEND_DATE": "2026-10-12",
                        "NOTICE_DATE": "2026-10-10",
                        "REPORT_DATE": "2026-06-30",
                        "IMPL_PLAN_PROFILE": "10派1",
                    }
                ],
            },
        }

        def get(*args, **kwargs):
            calls.append(kwargs["params"])
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

        session = SimpleNamespace(get=get)
        throttle = SimpleNamespace(wait=lambda: None)
        frame = corporate_actions.fetch_corporate_actions(
            session,
            None,
            throttle,
            data_cutoff="2026-10-10",
            refresh_from="2026-10-03",
            date_field="notice_date",
        )
        self.assertEqual(frame.ex_date.tolist(), [pd.Timestamp("2026-10-12")])
        self.assertIn("NOTICE_DATE>='2026-10-03'", calls[0]["filter"])
        self.assertNotIn("EX_DIVIDEND_DATE<=", calls[0]["filter"])
        with self.assertRaisesRegex(RuntimeError, "cutoff"):
            corporate_actions.fetch_corporate_actions(
                session, None, throttle, data_cutoff="2026-10-10"
            )


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_catalog_change_does_not_invalidate_equity_or_financials(self):
        (self.root / "research").mkdir()
        file = self.root / "research/catalog.py"
        file.write_text("value=1")
        before = {
            name: identity.implementation_signature(name, package=self.root)
            for name in ["catalog", "equity_structure", "financials"]
        }
        file.write_text("value=2")
        after = {
            name: identity.implementation_signature(name, package=self.root)
            for name in before
        }
        self.assertNotEqual(before["catalog"], after["catalog"])
        self.assertEqual(before["equity_structure"], after["equity_structure"])
        self.assertEqual(before["financials"], after["financials"])

    def test_only_related_handler_branch_and_shared_rule_invalidate(self):
        (self.root / "pipeline").mkdir()
        path = self.root / "pipeline/handlers.py"
        path.write_text(
            "def execute(name):\n if name == 'prices':\n  return 1\n elif name == 'catalog':\n  return 2\n"
        )
        before = identity.implementation_signature("prices", package=self.root)
        path.write_text(
            "def execute(name):\n if name == 'prices':\n  return 1\n elif name == 'catalog':\n  return 3\n"
        )
        self.assertEqual(
            before, identity.implementation_signature("prices", package=self.root)
        )
        (self.root / "rules").mkdir()
        rule = self.root / "rules/prices.py"
        rule.write_text("rounding=1")
        before = identity.implementation_signature("limits", package=self.root)
        rule.write_text("rounding=2")
        self.assertNotEqual(
            before, identity.implementation_signature("limits", package=self.root)
        )

    def test_fact_identity_does_not_depend_on_queue_or_scheduler_changes(self):
        (self.root / "sources").mkdir()
        (self.root / "pipeline").mkdir()
        for name in [
            "storage.py",
            "incremental_files.py",
            "sources/fetch_windows.py",
            "sources/financial_statements.py",
        ]:
            (self.root / name).write_text("value=1")
        queue = self.root / "pipeline/source_receipts.py"
        queue.write_text("batch=50")
        before = identity.source_implementation_signature(
            "financials", package=self.root
        )
        queue.write_text("batch=100")
        self.assertEqual(
            before,
            identity.source_implementation_signature("financials", package=self.root),
        )
        (self.root / "sources/fetch_windows.py").write_text("value=2")
        self.assertNotEqual(
            before,
            identity.source_implementation_signature("financials", package=self.root),
        )

    def test_transport_settings_and_other_domain_settings_do_not_change_semantic_config(
        self,
    ):
        config = {
            "start_date": "2010-01-01",
            "financial_periods": 80,
            "bar_adjustments": ["raw"],
            "derive_qfq": True,
            "retries": 3,
            "cninfo_min_interval_seconds": 0.25,
        }
        other = {
            **config,
            "retries": 5,
            "cninfo_min_interval_seconds": 0.1,
            "derive_qfq": False,
            "bar_adjustments": ["qfq"],
        }
        self.assertEqual(
            identity.semantic_config("financials", config),
            identity.semantic_config("financials", other),
        )
        self.assertNotEqual(
            identity.semantic_config("prices", config),
            identity.semantic_config("prices", other),
        )
        self.assertNotEqual(
            identity.semantic_config("financials", config),
            identity.semantic_config("financials", {**config, "financial_periods": 12}),
        )


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.universe = pd.DataFrame({"code": ["000001", "000002"]})
        self.config = {
            "start_date": "2010-01-01",
            "end_date": "2026-10-10",
            "source_batch_size": 1,
            "sina_min_interval_seconds": 0,
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_financials_start_before_all_announcements_finish_and_failures_remain_isolated(
        self,
    ):
        channel = Queue()
        financial_started = threading.Event()
        calls = []

        def announce(config, codes, max_workers, on_code_done):
            atomic_parquet(
                pd.DataFrame({"value": [1]}), self.root / "announcements/000001.parquet"
            )
            on_code_done("000001")
            self.assertTrue(
                financial_started.wait(3),
                "financial consumer waited for entire producer",
            )
            # The second issuer deliberately fails its announcement check.

        def financial(config, codes, max_workers, on_code_done):
            calls.extend(codes)
            self.assertIsNotNone(
                read_json(
                    self.root / "state/fetch_receipts/announcements.json", {}
                ).get("000001")
            )
            financial_started.set()
            for code in codes:
                atomic_parquet(
                    pd.DataFrame({"value": [2]}),
                    self.root / f"financials/{code}.parquet",
                )
                on_code_done(code)

        with (
            patch.object(source_receipts, "STATE_ROOT", self.root / "state"),
            patch.object(source_receipts, "PARQUET_ROOT", self.root),
            patch(
                "ashare_alpha.data.incremental_files._listed_universe",
                side_effect=lambda codes=None: (
                    self.universe
                    if codes is None
                    else self.universe[self.universe.code.isin(codes)]
                ),
            ),
            patch.object(
                financial_statements, "update_financials_incremental", financial
            ),
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                consumer = pool.submit(
                    source_receipts.stream_financials,
                    {**self.config, "_ready_queue": channel},
                    None,
                    workers=1,
                )
                with self.assertRaisesRegex(RuntimeError, "未完成"):
                    source_receipts.refresh_securities(
                        "announcements",
                        announce,
                        {**self.config, "_ready_queue": channel},
                        None,
                        workers=1,
                    )
                with self.assertRaisesRegex(RuntimeError, "blocked securities"):
                    consumer.result(timeout=4)
        self.assertEqual(calls, ["000001"])
        self.assertEqual(
            set(read_json(self.root / "state/fetch_receipts/financials.json")),
            {"000001"},
        )

    def test_cached_announcement_successes_are_handed_off_without_refetch(self):
        channel = Queue()

        def announce(config, codes, max_workers, on_code_done):
            for code in codes:
                atomic_parquet(
                    pd.DataFrame({"v": [1]}),
                    self.root / f"announcements/{code}.parquet",
                )
                on_code_done(code)

        with (
            patch.object(source_receipts, "STATE_ROOT", self.root / "state"),
            patch.object(source_receipts, "PARQUET_ROOT", self.root),
            patch(
                "ashare_alpha.data.incremental_files._listed_universe",
                return_value=self.universe,
            ),
        ):
            source_receipts.refresh_securities(
                "announcements", announce, self.config, None, workers=1
            )
            result = source_receipts.refresh_securities(
                "announcements",
                announce,
                {**self.config, "_ready_queue": channel, "retries": 9},
                None,
                workers=1,
            )
        self.assertEqual(result["processed"], 0)
        self.assertEqual(set(channel.get_nowait()["codes"]), set(self.universe.code))
        self.assertTrue(channel.get_nowait()["end"])


if __name__ == "__main__":
    unittest.main()
