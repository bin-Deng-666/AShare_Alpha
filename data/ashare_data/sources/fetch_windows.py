"""Fetch ranges based on successful checks, including gaps after long pauses."""
from __future__ import annotations

from datetime import date, timedelta


def fetch_start(config, checked_through=None, *, overlap_days=7):
    lower = date.fromisoformat(str(config['start_date']))
    target = date.fromisoformat(str(config['end_date']))
    repair = config.get('refresh_from')
    if repair:
        start = max(lower, date.fromisoformat(str(repair)))
    elif checked_through and date.fromisoformat(str(checked_through)) <= target:
        start = max(lower, date.fromisoformat(str(checked_through)) - timedelta(days=overlap_days))
    else:
        # A latest event date cannot prove the intervening period was checked.
        start = lower
    if start > target:
        raise ValueError('Fetch start is later than target')
    return start.isoformat()


def financial_period_count(config, checked_through=None):
    start = date.fromisoformat(fetch_start(config, checked_through))
    end = date.fromisoformat(str(config['end_date']))
    # Four statements per year, with four quarters of overlap for revisions.
    quarters = ((end.year - start.year) * 12 + end.month - start.month + 2) // 3
    initial = int(config.get('financial_periods', 80)) if not checked_through else 0
    return max(12, initial, quarters + 4)
