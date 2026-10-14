"""Explicit initial loads, imports and repair operations outside incremental updates."""

from __future__ import annotations
import importlib
import inspect
from ..runtime import load_config
from ..paths import RAW_ROOT, STATE_ROOT
from ..storage import writer_lock

COMMANDS = {
    "universe": ("sources.securities", "build_universe"),
    "listing-dates": ("sources.securities", "build_listing_dates"),
    "bars": ("sources.prices", "build_bars"),
    "repair-qfq": ("sources.prices", "repair_qfq_bars"),
    "financials": ("sources.financial_statements", "build_financials"),
    "repair-financials": (
        "sources.financial_statements",
        "repair_financial_duplicates",
    ),
    "announcements": ("sources.announcements", "build_financial_announcements"),
    "announcements-q1": ("sources.announcements", "build_financial_announcements"),
    "financial-availability": (
        "research.financial_availability",
        "rebuild_financial_availability",
    ),
    "corporate-actions": ("sources.corporate_actions", "build_corporate_actions"),
    "corporate-action-events": (
        "sources.corporate_actions",
        "build_corporate_action_events",
    ),
    "qfq-corporate-actions": (
        "assets.adjustment_limits",
        "build_qfq_from_corporate_actions",
    ),
    "equity-structure": ("sources.equity_structure", "build_equity_structure"),
    "shares-daily": ("assets.shares", "build_shares_daily"),
    "limit-prices": ("assets.adjustment_limits", "build_limit_prices"),
    "indicators": ("sources.indicators", "build_daily_indicators"),
    "bj-indicators": ("sources.indicators", "build_bj_daily_indicators"),
    "bj-valuation": ("assets.beijing_valuation", "enrich_bj_indicators_with_valuation"),
    "import-simtradedata-indicators": (
        "sources.indicators",
        "import_simtradedata_indicators",
    ),
    "reference": ("sources.reference", "build_reference_data"),
    "reference-intervals": ("sources.reference", "build_reference_intervals"),
    "catalog": ("research.catalog", "build_catalog"),
    "validate": ("research.validation", "validate_dataset"),
    "manifest": ("research.validation", "build_manifest"),
}


def run_maintenance(args):
    config = load_config()
    if args.data_cutoff:
        config["end_date"] = args.data_cutoff
    module, name = COMMANDS[args.asset]
    function = getattr(importlib.import_module("ashare_alpha.data." + module), name)
    options = dict(
        config=config,
        limit=args.limit,
        codes=args.codes,
        force=args.force,
        data_cutoff=args.data_cutoff,
        max_workers=args.workers,
        source_root=args.source_root or RAW_ROOT / "simtradelab" / "cn",
        q1_only=args.asset == "announcements-q1",
    )
    kwargs = {
        key: options[key]
        for key in inspect.signature(function).parameters
        if key in options
    }
    with writer_lock(STATE_ROOT / "data_update.lock"):
        function(**kwargs)
    return 0
