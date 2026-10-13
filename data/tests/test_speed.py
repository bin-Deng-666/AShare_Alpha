"""Request-count and equivalence regressions for the common incremental path."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from data.ashare_data.sources import corporate_actions as events
from data.ashare_data.sources import securities
from data.ashare_data.pipeline import handlers, planner
from data.ashare_data.pipeline.receipts import fingerprint
from data.ashare_data.storage import atomic_parquet


_init_reference_worker = events._initialize_reference_worker


def init_local_reference_worker(root):
    _init_reference_worker(root)
    events.bs = None  # This offline process must never log into a live source.


class SpeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def local_prices(self, code='000001'):
        dates = pd.to_datetime(['2026-07-01', '2026-07-02', '2026-07-03'])
        raw = pd.DataFrame({'date': dates, 'close': [10., 10., 10.]})
        atomic_parquet(raw, self.root / f'daily_bars/adjustment=raw/{code}.parquet')
        return raw

    def test_complete_reference_cache_avoids_source(self):
        raw = self.local_prices()
        cache = raw.iloc[:2][['date']].copy()
        cache['observed_reference_price'] = [0., 10.]
        atomic_parquet(cache, self.root/'reference/exchange_reference_prices/000001.parquet')
        indicator = raw[['date']].copy()
        indicator['preclose'] = [None, None, 10.]
        atomic_parquet(indicator, self.root/'daily_indicators/000001.parquet')
        backend = Mock()
        with patch.object(events, 'PARQUET', self.root), patch.object(events, 'bs', backend):
            result = events.fetch_baostock_preclose('000001', '2026-07-01', '2026-07-03')
        self.assertEqual(result['observed_reference_price'].tolist(), [0., 10., 10.])
        backend.query_history_k_data_plus.assert_not_called()
        backend.login.assert_not_called()

    def test_reference_gap_and_forced_refresh_still_query_source(self):
        raw = self.local_prices()
        indicator = raw[['date']].copy()
        indicator['preclose'] = [0., 10., None]
        atomic_parquet(indicator, self.root/'daily_indicators/000001.parquet')
        with patch.object(events, 'PARQUET', self.root):
            self.assertIsNone(events.reference_prices_from_local('000001', '2026-07-01', '2026-07-03'))
        rows = iter([['2026-07-01', '0'], ['2026-07-02', '10'], ['2026-07-03', '10']])
        class Response:
            error_code = '0'
            fields = ['date','preclose']
            def next(self):
                self.row = next(rows, None)
                return self.row is not None
            def get_row_data(self): return self.row
        backend = Mock()
        backend.query_history_k_data_plus.return_value = Response()
        with patch.object(events, 'PARQUET', self.root), patch.object(events, 'bs', backend):
            result = events.fetch_baostock_preclose('000001', '2026-07-01', '2026-07-03', use_local=False)
        self.assertEqual(len(result), 3)
        backend.query_history_k_data_plus.assert_called_once()

    def test_events_reuse_distributions_and_indicators_and_invalidate_changed_input(self):
        raw = self.local_prices()
        atomic_parquet(pd.DataFrame({'date':raw['date'], 'preclose':[0.,10.,10.]}),
                       self.root/'daily_indicators/000001.parquet')
        master = pd.DataFrame({'code':['000001'], 'exchange':['SZ'],
                               'list_date':pd.to_datetime(['1991-01-01'])})
        atomic_parquet(master, self.root/'security_master/security_master.parquet')
        distribution = pd.DataFrame({'code':['000001'], 'ex_date':pd.to_datetime(['2026-07-02']),
                                     'cash_dividend_per_10':[0.], 'bonus_transfer_per_10':[0.]})
        config = {'start_date':'2026-07-01','end_date':'2026-07-03','retries':1,
                  'retry_base_seconds':0,'eastmoney_min_interval_seconds':0}
        backend = Mock()
        with patch.object(events,'PARQUET',self.root),patch.object(events,'STATE',self.root/'state'),patch.object(events,'bs',backend),patch.object(events,'fetch_corporate_actions',return_value=distribution) as fetch,patch.object(events,'fetch_cninfo_allotment_events',return_value=events._empty_corporate_action_events()) as rights:
            events.build_corporate_actions(config,force=True)
            events.build_corporate_action_events(config,data_cutoff='2026-07-03',max_workers=1)
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(rights.call_count,1)
            import json
            first = json.loads((self.root/'state/corporate_action_event_coverage.json').read_text())
            self.assertEqual(first['work']['distribution_downloads'], 0)
            self.assertEqual(first['work']['reference_source_securities'], 0)
            self.assertEqual(first['work']['reference_local_securities'], 1)
            events.build_corporate_action_events(config,data_cutoff='2026-07-03',max_workers=1)
            self.assertEqual(rights.call_count,1)
            second = json.loads((self.root/'state/corporate_action_event_coverage.json').read_text())
            self.assertEqual(first['reference_validation'], second['reference_validation'])
            self.assertEqual(second['work']['reused_event_securities'], 1)
            raw.loc[2,'close'] = 11.
            atomic_parquet(raw,self.root/'daily_bars/adjustment=raw/000001.parquet')
            events.build_corporate_action_events(config,data_cutoff='2026-07-03',max_workers=1)
            self.assertEqual(rights.call_count,2)
            # Distribution files remain unchanged and their receipt is valid.
            self.assertEqual(fetch.call_count,1)
            backend.login.assert_not_called()
            backend.query_history_k_data_plus.assert_not_called()

    def test_parallel_reference_validation_matches_serial_without_source_access(self):
        codes=['000001','000002']
        for code in codes:
            raw=self.local_prices(code)
            atomic_parquet(pd.DataFrame({'date':raw.date,'preclose':[0.,10.,10.]}),self.root/f'daily_indicators/{code}.parquet')
        master=pd.DataFrame({'code':codes,'exchange':['SZ','SZ'],
                            'list_date':pd.to_datetime(['1991-01-01']*2)})
        atomic_parquet(master,self.root/'security_master/security_master.parquet')
        distribution=pd.DataFrame({'code':codes,'ex_date':pd.to_datetime(['2026-07-02']*2),
                                  'cash_dividend_per_10':[0.,0.],'bonus_transfer_per_10':[0.,0.]})
        config={'start_date':'2026-07-01','end_date':'2026-07-03','retries':1,
                'retry_base_seconds':0,'eastmoney_min_interval_seconds':0}
        with patch.object(events,'PARQUET',self.root),patch.object(events,'STATE',self.root/'state'),patch.object(events,'fetch_corporate_actions',return_value=distribution),patch.object(events,'fetch_cninfo_allotment_events',return_value=events._empty_corporate_action_events()),patch.object(events,'_initialize_reference_worker',init_local_reference_worker):
            events.build_corporate_actions(config,force=True)
            events.build_corporate_action_events(config,data_cutoff='2026-07-03',max_workers=1)
            expected={code:pd.read_parquet(self.root/f'corporate_action_events/{code}.parquet') for code in codes}
            import json
            state_path=self.root/'state/corporate_action_event_coverage.json'
            serial=json.loads(state_path.read_text())
            state_path.unlink()
            events.build_corporate_action_events(config,data_cutoff='2026-07-03',max_workers=2)
            parallel=json.loads(state_path.read_text())
        for code in codes:
            pd.testing.assert_frame_equal(expected[code],pd.read_parquet(self.root/f'corporate_action_events/{code}.parquet'))
        self.assertEqual(serial['reference_validation'],parallel['reference_validation'])
        self.assertEqual(parallel['work']['reference_source_securities'],0)
        self.assertEqual(parallel['work']['reference_local_securities'],2)
        self.assertEqual(parallel['failures'],[])

    def test_distribution_receipt_rejects_cutoff_and_file_changes(self):
        frame=pd.DataFrame({'code':['000001'], 'value':[1.]})
        atomic_parquet(pd.DataFrame({'code':['000001']}),self.root/'security_master/security_master.parquet')
        config={'end_date':'2026-07-03','eastmoney_min_interval_seconds':0,'retries':1,'retry_base_seconds':0}
        with patch.object(events,'PARQUET',self.root),patch.object(events,'STATE',self.root/'state'),patch.object(events,'fetch_corporate_actions',return_value=frame):
            events.build_corporate_actions(config,force=True)
            self.assertIsNotNone(events.completed_distributions(['000001'],'2026-07-03'))
            self.assertIsNone(events.completed_distributions(['000001'],'2026-07-04'))
            atomic_parquet(pd.DataFrame({'code':['000001'],'value':[2.]}),self.root/'corporate_actions/000001.parquet')
            self.assertIsNone(events.completed_distributions(['000001'],'2026-07-03'))

    def test_securities_does_not_repeat_already_resolved_bulk_listing_query(self):
        master=pd.DataFrame({'status':['listed'], 'list_date':pd.to_datetime(['2026-06-01'])})
        with patch.object(securities,'build_universe',return_value=master),patch.object(securities,'build_listing_dates') as listing:
            handlers.execute('securities',{},None,1)
            listing.assert_not_called()
            master.loc[0,'list_date']=pd.NaT
            handlers.execute('securities',{},None,1)
            listing.assert_called_once()

    def test_planning_observations_invalidated_after_file_mutation(self):
        path=self.root/'prices/a.parquet'
        atomic_parquet(pd.DataFrame({'v':[1]}),path)
        with patch.object(planner,'PARQUET_ROOT',self.root):
            context=planner.PlanningContext()
            files=context.files(('prices/*.parquet',))
            before=context.fingerprint(files)
            atomic_parquet(pd.DataFrame({'v':[2]}),path)
            context.invalidate()
            self.assertNotEqual(before,context.fingerprint(context.files(('prices/*.parquet',))))
            self.assertEqual(context.fingerprint(files),fingerprint(files))


if __name__=='__main__': unittest.main()
