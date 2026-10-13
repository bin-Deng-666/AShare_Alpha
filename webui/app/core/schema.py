"""Standard schema definitions for the strategy-agnostic WebUI core.

All strategy adapters must return data conforming to these schemas.
The frontend only consumes these standard shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ════════════════════════════════════════════════════════════════
#  Strategy Config (loaded from strategy.yaml)
# ════════════════════════════════════════════════════════════════


@dataclass
class StageConfig:
    """One stage in the update pipeline."""

    id: str
    label: str
    source_desc: str  # Template: "个股日行情（{t_full+1} → {latest_trade_date}）"
    command: list[str]  # Command tokens
    cwd: str  # Working directory (relative to repo root)
    timeout: int = 1800


@dataclass
class CheckConfig:
    """One post-update check (golden, factor_health, etc.)."""

    id: str
    label: str
    command: list[str]
    cwd: str
    timeout: int = 120
    updates_golden: bool = False  # If true, parse golden PASS/FAIL from output


@dataclass
class ColumnDef:
    """One column in a table (buy/sell/positions)."""

    key: str  # JSON field name in the data
    label: str  # Display header
    width: str = ""  # CSS width hint


@dataclass
class AuxSource:
    """An auxiliary data source for per-source date display."""

    id: str  # Internal id (e.g. "bars")
    display_name: str  # Display label (e.g. "行情bars")
    table: str  # Catalog table name
    date_column: str  # Date column name in that table


@dataclass
class MarketStatusField:
    """A field to display in the market status row."""

    key: str  # JSON field path in result.meta (e.g. "mkt_regime")
    label: str  # Display label (e.g. "大盘量能")


@dataclass
class StrategyConfig:
    """Full strategy configuration loaded from strategy.yaml."""

    strategy_id: str
    display_name: str
    version: str
    repo_root: str  # Relative to repo root
    panel_path: str  # Relative to strategy root
    panel_hash_expected: str  # Expected MD5 hash (first 8 chars)
    catalog_path: str  # Relative to repo root
    t_full_source: str  # Path to pipeline state file (relative to repo root)
    calendar_source: str  # Path to index bars parquet (relative to repo root)
    ledger_source: str  # Path to trades.csv (relative to strategy root)
    output_dir: str  # Relative to repo root (e.g. "strategy/artifacts/demo/plans")
    golden_meta_path: str  # Relative to strategy root
    factor_health_dir: str  # Relative to strategy root
    daily_plan_command: list[str]  # Command to generate daily plan
    daily_plan_cwd: str  # Working directory for daily plan
    daily_plan_timeout: int = 600
    daily_asof_argument: str = ""

    aux_sources: list[AuxSource] = field(default_factory=list)
    market_status_fields: list[MarketStatusField] = field(default_factory=list)
    plan_columns_buy: list[ColumnDef] = field(default_factory=list)
    plan_columns_sell: list[ColumnDef] = field(default_factory=list)
    positions_columns: list[ColumnDef] = field(default_factory=list)
    sell_reasons: list[str] = field(default_factory=list)

    update_pipeline: list[StageConfig] = field(default_factory=list)
    post_update_checks: list[CheckConfig] = field(default_factory=list)

    # Positions calculation config
    take_profit: float = 0.15
    stop_loss: float = 0.08

    @property
    def all_stages(self) -> list[StageConfig | CheckConfig]:
        """All stages in order: update_pipeline + post_update_checks."""
        return list(self.update_pipeline) + list(self.post_update_checks)
