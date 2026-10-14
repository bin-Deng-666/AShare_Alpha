"""Concrete update tasks with dependencies and independent refresh cadences."""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    name: str
    dependencies: tuple[str, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    network: bool = False
    cadence_days: int = 1
    resources: tuple[str, ...] = ()


TASKS = (
    Task(
        "securities",
        (),
        (),
        ("security_master/*.parquet",),
        True,
        resources=("baostock",),
    ),
    Task(
        "calendar",
        (),
        (),
        ("reference/trade_calendar.parquet",),
        True,
        resources=("baostock",),
    ),
    Task(
        "prices",
        ("securities", "calendar"),
        ("security_master/*.parquet",),
        (
            "daily_bars/adjustment=raw/*.parquet",
            "daily_bars/adjustment=qfq/92*.parquet",
            "daily_bars/adjustment=qfq/8*.parquet",
            "daily_bars/adjustment=qfq/4*.parquet",
        ),
        True,
    ),
    Task(
        "indicators",
        ("securities", "calendar"),
        ("security_master/*.parquet",),
        ("daily_indicators/[036]*.parquet",),
        True,
        resources=("baostock",),
    ),
    Task(
        "distributions",
        ("securities",),
        ("security_master/*.parquet",),
        ("corporate_actions/*.parquet",),
        True,
        resources=("eastmoney_bulk",),
    ),
    Task(
        "corporate_events",
        ("prices", "indicators", "distributions"),
        (
            "daily_bars/adjustment=raw/*.parquet",
            "daily_indicators/[036]*.parquet",
            "corporate_actions/*.parquet",
        ),
        ("corporate_action_events/*.parquet",),
        True,
        resources=("baostock",),
    ),
    Task(
        "equity_structure",
        ("securities",),
        ("security_master/*.parquet",),
        ("equity_structure/*.parquet",),
        True,
        resources=("eastmoney_bulk",),
    ),
    Task(
        "announcements",
        ("securities",),
        ("security_master/*.parquet",),
        ("announcements/*.parquet",),
        True,
    ),
    Task(
        "financials",
        ("securities", "announcements"),
        ("announcements/*.parquet",),
        ("financials/*.parquet",),
        True,
        7,
    ),
    Task(
        "availability",
        ("financials", "announcements", "calendar"),
        (
            "financials/*.parquet",
            "announcements/*.parquet",
            "reference/trade_calendar.parquet",
        ),
        ("reference/financial_availability.parquet",),
    ),
    Task(
        "shares",
        ("prices", "equity_structure", "distributions", "calendar"),
        (
            "daily_bars/adjustment=raw/*.parquet",
            "equity_structure/*.parquet",
            "corporate_actions/*.parquet",
            "reference/trade_calendar.parquet",
        ),
        ("shares_daily/*.parquet",),
    ),
    Task(
        "beijing_valuation",
        ("prices", "indicators", "financials", "availability", "shares"),
        (
            "daily_bars/adjustment=raw/*.parquet",
            "financials/*.parquet",
            "reference/financial_availability.parquet",
            "shares_daily/*.parquet",
        ),
        (
            "daily_indicators/92*.parquet",
            "daily_indicators/8*.parquet",
            "daily_indicators/4*.parquet",
        ),
    ),
    Task(
        "adjustment",
        ("prices", "corporate_events"),
        ("daily_bars/adjustment=raw/*.parquet", "corporate_action_events/*.parquet"),
        ("daily_bars/adjustment=qfq/*.parquet",),
    ),
    Task(
        "limits",
        ("prices", "indicators", "corporate_events", "calendar", "beijing_valuation"),
        (
            "daily_bars/adjustment=raw/*.parquet",
            "daily_indicators/*.parquet",
            "corporate_actions/*.parquet",
            "corporate_action_events/*.parquet",
            "security_master/*.parquet",
            "reference/trade_calendar.parquet",
        ),
        ("limit_prices/*.parquet",),
    ),
    Task(
        "references",
        ("calendar",),
        (),
        (
            "reference/index_bars/*.parquet",
            "reference/industry_membership*.parquet",
            "reference/index_constituent*.parquet",
        ),
        True,
        resources=("baostock",),
    ),
    Task(
        "catalog",
        ("adjustment", "limits", "shares", "beijing_valuation", "references"),
        ("**/*.parquet",),
        (),
    ),
)
DEFAULT_NAMES = tuple(t.name for t in TASKS)
TASKS += (
    Task(
        "connect_margin",
        (),
        (),
        (
            "reference/northbound_market_daily.parquet",
            "reference/margin_market_daily.parquet",
        ),
        True,
        resources=("eastmoney_bulk",),
    ),
    Task(
        "disclosure_trades",
        (),
        (),
        ("reference/lhb_detail/lhb_detail.parquet",),
        True,
        resources=("eastmoney_bulk",),
    ),
    Task(
        "block_trades",
        (),
        (),
        ("reference/block_trade/block_trade.parquet",),
        True,
        resources=("eastmoney_bulk",),
    ),
    Task(
        "holder_changes",
        (),
        (),
        ("reference/holder_change/holder_change.parquet",),
        True,
        resources=("eastmoney_bulk",),
    ),
    Task("cash_yield", (), (), ("reference/cash_yield/gc001.parquet",), True),
)

BY_NAME = {t.name: t for t in TASKS}


def select_tasks(names=None):
    selected = set()
    visiting = set()

    def visit(name):
        if name not in BY_NAME:
            raise ValueError(f"Unknown task: {name}")
        if name in visiting:
            raise ValueError(f"Cyclic task dependency: {name}")
        if name in selected:
            return
        visiting.add(name)
        for dep in BY_NAME[name].dependencies:
            visit(dep)
        visiting.remove(name)
        selected.add(name)

    for name in names or DEFAULT_NAMES:
        visit(name)
    return tuple(t for t in TASKS if t.name in selected)
