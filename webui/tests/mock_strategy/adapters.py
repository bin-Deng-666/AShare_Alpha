"""The fixture exercises production report parsing with its own configuration."""

from webui.app.strategies.demo.adapters import (
    parse_daily_latest,
    parse_history,
    parse_history_detail,
    parse_positions,
)

__all__ = [
    "parse_daily_latest",
    "parse_history",
    "parse_history_detail",
    "parse_positions",
]
