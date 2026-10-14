"""Display and workflow configuration for the local strategy console."""

from dataclasses import dataclass, field


@dataclass
class StageConfig:
    """An update or verification subprocess; a nonzero exit stops the workflow."""

    id: str
    label: str
    source_desc: str
    command: list[str]
    cwd: str
    timeout: int = 1800


@dataclass
class ColumnDef:
    key: str
    label: str


@dataclass
class AuxSource:
    id: str
    display_name: str
    table: str
    date_column: str


@dataclass
class MarketStatusField:
    key: str
    label: str


@dataclass
class StrategyConfig:
    strategy_id: str
    display_name: str
    version: str
    repo_root: str
    panel_path: str
    catalog_path: str
    calendar_source: str
    ledger_source: str
    output_dir: str
    daily_plan_command: list[str]
    daily_plan_cwd: str
    daily_plan_timeout: int = 600
    daily_asof_argument: str = ""
    aux_sources: list[AuxSource] = field(default_factory=list)
    market_status_fields: list[MarketStatusField] = field(default_factory=list)
    plan_columns_buy: list[ColumnDef] = field(default_factory=list)
    plan_columns_sell: list[ColumnDef] = field(default_factory=list)
    positions_columns: list[ColumnDef] = field(default_factory=list)
    update_pipeline: list[StageConfig] = field(default_factory=list)
    post_update_checks: list[StageConfig] = field(default_factory=list)

    @property
    def all_stages(self) -> list[StageConfig]:
        return [*self.update_pipeline, *self.post_update_checks]
