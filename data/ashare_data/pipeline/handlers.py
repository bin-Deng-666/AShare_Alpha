"""Execute named data jobs; no strategy imports, panel rebuilds or global T_full."""
from __future__ import annotations

from ..paths import PARQUET_ROOT as P, STATE_ROOT as S
from ..storage import read_json


def _require_success(report):
    data = read_json(S / report)
    if data is None or data.get('failures'):
        raise RuntimeError(f'{report}: incomplete source update: {str(data and data.get("failures"))[:500]}')


def execute(name, config, codes, workers, force=False):
    from ..incremental_files import _listed_universe
    if name == 'securities':
        from ..sources.securities import build_universe, build_listing_dates
        master = build_universe(config)
        # The bulk universe request already includes exchange listing dates.
        # Fetch those lists again only if listed securities are still unresolved.
        missing_dates = master['status'].eq('listed') & master['list_date'].isna()
        if missing_dates.any():
            build_listing_dates(config)
        return {'listing_date_requests_repeated': bool(missing_dates.any()),
                'securities': len(master)}
    elif name == 'calendar':
        from ..sources.reference import refresh_trade_calendar
        refresh_trade_calendar(config)
    elif name == 'prices':
        from ..sources.prices import update_bars_incremental
        # Direct prices-only jobs still maintain both series. The full dependency
        # chain requests raw only and derives SH/SZ qfq after event validation.
        update_bars_incremental({**config, 'force_fetch': force}, codes=codes, max_workers=workers)
        _require_success('daily_bar_update.json')
    elif name == 'indicators':
        from ..sources.indicators import update_indicators_incremental
        update_indicators_incremental({**config, 'force_fetch': force}, codes=codes, max_workers=workers)
        _require_success('daily_indicator_update.json')
    elif name == 'distributions':
        from ..sources.corporate_actions import refresh_distributions
        return refresh_distributions(config,codes=codes,force=force,workers=workers)
    elif name == 'corporate_events':
        from ..sources.corporate_actions import build_corporate_action_events, _check_mini_racer
        if not _check_mini_racer():
            raise RuntimeError('公司行为源 MiniRacer 不可用，未发布过期复权数据')
        build_corporate_action_events(config, codes=codes, data_cutoff=config['end_date'], max_workers=workers, force=force)
        _require_success('corporate_action_event_coverage.json')
    elif name == 'equity_structure':
        from ..sources.equity_structure import refresh_equity_structure
        return refresh_equity_structure(config,codes=codes,force=force,workers=workers)
    elif name in ('financials', 'announcements'):
        if name=='financials' and config.get('_ready_queue') is not None:
            from .source_receipts import stream_financials
            return stream_financials(config,codes,workers=workers,force=force)
        from ..sources.financial_statements import update_financials_incremental
        from ..sources.announcements import update_announcements_incremental
        function = update_financials_incremental if name == 'financials' else update_announcements_incremental
        from .source_receipts import refresh_securities
        return refresh_securities(
            name, function, config, codes, workers=workers,
            cadence_days=7 if name == 'financials' else 1, force=force,
            inputs=('announcements/{code}.parquet',) if name == 'financials' else (),
        )
    elif name == 'availability':
        from ..research.financial_availability import rebuild_financial_availability
        rebuild_financial_availability(config)
    elif name == 'beijing_valuation':
        from ..sources.indicators import build_bj_daily_indicators
        from ..assets.beijing_valuation import enrich_bj_indicators_with_valuation
        if codes is None:
            import pandas as pd
            universe=pd.read_parquet(P/'security_master/security_master.parquet')
        else:
            universe=_listed_universe(codes)
        bj = universe.loc[universe.exchange.eq('BJ'), 'code'].tolist()
        if bj:
            build_bj_daily_indicators(config, codes=bj, force=True)
            enrich_bj_indicators_with_valuation(config, codes=bj)
            _require_success('bj_indicators_coverage.json')
            _require_success('bj_valuation_coverage.json')
    elif name in ('adjustment', 'limits', 'shares'):
        from ..assets.adjustment_limits import build_qfq_from_corporate_actions, build_limit_prices
        from ..assets.shares import build_shares_daily
        from ..assets.incremental import rebuild_changed
        all_codes = codes
        if all_codes is None:
            import pandas as pd
            all_codes = pd.read_parquet(P / 'security_master/security_master.parquet',columns=['code']).code.tolist()
        universe = _listed_universe(all_codes)
        if name == 'adjustment':
            # BJ qfq is maintained by the vendor price source.
            universe = universe[universe.exchange.isin(['SH', 'SZ'])]
        selected = universe.code.tolist()
        function, inputs, output = {
            'adjustment': (build_qfq_from_corporate_actions, ['daily_bars/adjustment=raw/{code}.parquet', 'corporate_action_events/{code}.parquet'], 'daily_bars/adjustment=qfq/{code}.parquet'),
            'limits': (build_limit_prices, ['daily_bars/adjustment=raw/{code}.parquet', 'corporate_action_events/{code}.parquet', 'corporate_actions/{code}.parquet', 'daily_indicators/{code}.parquet', 'security_master/security_master.parquet', 'reference/trade_calendar.parquet'], 'limit_prices/{code}.parquet'),
            'shares': (build_shares_daily, ['daily_bars/adjustment=raw/{code}.parquet', 'corporate_actions/{code}.parquet', 'equity_structure/{code}.parquet', 'reference/trade_calendar.parquet'], 'shares_daily/{code}.parquet'),
        }[name]
        return rebuild_changed(name, function, config, selected, inputs, output, force=force)
    elif name == 'references':
        from ..sources.reference import build_reference_data, build_reference_intervals
        build_reference_data(config)
        build_reference_intervals(config)
    elif name == 'catalog':
        from ..research.catalog import build_catalog
        from ..research.coverage import validate_core_coverage
        validate_core_coverage()
        build_catalog(config)
    elif name in ('block_trades', 'disclosure_trades'):
        import importlib
        return importlib.import_module('data.ashare_data.sources.' + name).refresh(config, workers=workers)
    elif name == 'holder_changes':
        from ..sources.holder_changes import refresh
        refresh(config)
    elif name == 'connect_margin':
        import requests
        from ..runtime import UA
        from ..sources.connect_margin import ingest_northbound, ingest_margin_sse
        with requests.Session() as session:
            session.headers.update({'User-Agent': UA})
            ingest_northbound(session)
        ingest_margin_sse(end=config['end_date'].replace('-', ''))
    elif name == 'cash_yield':
        from ..sources.cash_yield import refresh_cash_yield
        return refresh_cash_yield()
    else:
        raise ValueError(f'No implementation for {name}')
    return {}
