"""Versioned plan envelope; strategy-specific display fields remain extensible."""

from datetime import date
import math

PLAN_FORMAT_VERSION = 1


def validate_plan(value):
    if value.get("format_version", PLAN_FORMAT_VERSION) != PLAN_FORMAT_VERSION:
        raise ValueError("Unsupported daily plan format")
    if not isinstance(value.get("strategy"), str) or not value["strategy"]:
        raise ValueError("Plan must name its strategy")
    asof = date.fromisoformat(value["asof"])
    execution = value.get("exec_date")
    if execution is not None and date.fromisoformat(execution) <= asof:
        raise ValueError("Execution date must follow the signal date")
    for key in ("buy", "reduce", "positions"):
        rows = value.get(key, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"Plan {key} must be a list of records")
        for row in rows:
            if "code" not in row:
                raise ValueError(f"Plan {key} record lacks a security code")
            for field in ("budget", "quantity", "shares"):
                if field in row and row[field] is not None:
                    number = row[field]
                    if (
                        isinstance(number, bool)
                        or not isinstance(number, (int, float))
                        or not math.isfinite(number)
                        or number < 0
                    ):
                        raise ValueError(f"Invalid {key}.{field}")
    return value
