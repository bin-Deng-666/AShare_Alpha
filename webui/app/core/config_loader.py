"""Strategy configuration loader — reads strategy.yaml files.

Discovers all registered strategies under app/strategies/ and loads their
strategy.yaml into StrategyConfig dataclasses.
"""

from __future__ import annotations

from dataclasses import fields
import os
from pathlib import Path
from typing import Any

from ashare_alpha.strategies import web_strategies

import yaml

from webui.app.core.schema import (
    AuxSource,
    ColumnDef,
    MarketStatusField,
    StageConfig,
    StrategyConfig,
)

WEBUI_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = WEBUI_DIR.parent
STRATEGIES_DIR = WEBUI_DIR / "app" / "strategies"
TEST_STRATEGIES_DIR = WEBUI_DIR / "tests"


def _parse_column(col: dict[str, Any]) -> ColumnDef:
    return ColumnDef(
        key=col["key"],
        label=col.get("label", col["key"]),
    )


def _parse_aux_source(src: dict[str, Any]) -> AuxSource:
    return AuxSource(
        id=src["id"],
        display_name=src.get("display_name", src["id"]),
        table=src["table"],
        date_column=src["date_column"],
    )


def _parse_market_field(f: dict[str, Any]) -> MarketStatusField:
    return MarketStatusField(
        key=f["key"],
        label=f.get("label", f["key"]),
    )


def _parse_stage(s: dict[str, Any]) -> StageConfig:
    return StageConfig(
        id=s["id"],
        label=s.get("label", s["id"]),
        source_desc=s.get("source_desc", ""),
        command=s["command"],
        cwd=s.get("cwd", "."),
        timeout=s.get("timeout", 1800),
    )


def load_strategy(yaml_path: Path) -> StrategyConfig:
    """Load a single strategy.yaml into a StrategyConfig."""
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{yaml_path}: expected a configuration object")
    allowed = {item.name for item in fields(StrategyConfig)} | {"plan_columns"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"{yaml_path}: unknown fields: {', '.join(sorted(unknown))}")
    for required in (
        "strategy_id",
        "repo_root",
        "panel_path",
        "catalog_path",
        "calendar_source",
        "ledger_source",
        "output_dir",
        "daily_plan_command",
    ):
        if required not in raw:
            raise ValueError(f"{yaml_path}: missing {required}")
    stages = raw.get("update_pipeline", []) + raw.get("post_update_checks", [])
    ids = [stage["id"] for stage in stages]
    if len(ids) != len(set(ids)) or "daily_plan" in ids:
        raise ValueError(f"{yaml_path}: duplicate or reserved stage id")
    for stage in stages + [{"command": raw["daily_plan_command"], "id": "daily"}]:
        command = stage["command"]
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(token, str) or not token for token in command)
        ):
            raise ValueError(
                f"{yaml_path}: {stage['id']} command must contain string tokens"
            )
        timeout = stage.get("timeout", 600)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ValueError(f"{yaml_path}: {stage['id']} timeout must be positive")

    return StrategyConfig(
        strategy_id=raw["strategy_id"],
        display_name=raw.get("display_name", raw["strategy_id"]),
        version=raw.get("version", "v1.0"),
        repo_root=raw["repo_root"],
        panel_path=raw["panel_path"],
        catalog_path=raw["catalog_path"],
        calendar_source=raw["calendar_source"],
        ledger_source=raw["ledger_source"],
        output_dir=raw["output_dir"],
        daily_plan_command=raw["daily_plan_command"],
        daily_plan_cwd=raw.get("daily_plan_cwd", "."),
        daily_asof_argument=raw.get("daily_asof_argument", ""),
        daily_plan_timeout=raw.get("daily_plan_timeout", 600),
        aux_sources=[_parse_aux_source(s) for s in raw.get("aux_sources", [])],
        market_status_fields=[
            _parse_market_field(f) for f in raw.get("market_status_fields", [])
        ],
        plan_columns_buy=[
            _parse_column(c) for c in raw.get("plan_columns", {}).get("buy", [])
        ],
        plan_columns_sell=[
            _parse_column(c) for c in raw.get("plan_columns", {}).get("sell", [])
        ],
        positions_columns=[_parse_column(c) for c in raw.get("positions_columns", [])],
        update_pipeline=[_parse_stage(s) for s in raw.get("update_pipeline", [])],
        post_update_checks=[_parse_stage(c) for c in raw.get("post_update_checks", [])],
    )


# ════════════════════════════════════════════════════════════════
#  Strategy registry
# ════════════════════════════════════════════════════════════════

_cache: dict[str, StrategyConfig] = {}


def _discover() -> None:
    """Scan strategies/ and tests/ directories for strategy.yaml files."""
    _cache.clear()
    # Production strategies
    for yaml_path in sorted(STRATEGIES_DIR.glob("*/strategy.yaml")):
        if yaml_path.parent.name not in web_strategies():
            continue  # Local, unpublished strategies are outside the public catalog.
        cfg = load_strategy(yaml_path)
        if cfg.strategy_id not in web_strategies():
            raise ValueError(
                f"{yaml_path}: strategy has no registered WebUI capability"
            )
        if cfg.strategy_id in _cache:
            raise ValueError(f"{yaml_path}: duplicate strategy id")
        _cache[cfg.strategy_id] = cfg
    # Fixtures are enabled explicitly by integration tests, never listed in production.
    for yaml_path in (
        sorted(TEST_STRATEGIES_DIR.glob("*/strategy.yaml"))
        if os.environ.get("ASHARE_WEBUI_TEST_STRATEGIES") == "1"
        else []
    ):
        cfg = load_strategy(yaml_path)
        _cache[cfg.strategy_id] = cfg


def list_strategies() -> list[str]:
    """Return sorted list of registered strategy IDs."""
    if not _cache:
        _discover()
    return sorted(_cache.keys())


def get_strategy(strategy_id: str) -> StrategyConfig:
    """Get config for a specific strategy. Raises KeyError if not found."""
    if not _cache:
        _discover()
    if strategy_id not in _cache:
        raise KeyError(f"Unknown strategy: {strategy_id}. Known: {list_strategies()}")
    return _cache[strategy_id]


def get_strategy_paths(cfg: StrategyConfig) -> dict[str, Path]:
    """Resolve all relative paths in config to absolute paths."""
    from ashare_alpha.paths import WORKSPACE_ROOT

    repo = REPO_ROOT
    workspace = WORKSPACE_ROOT
    strat_root = repo / cfg.repo_root
    return {
        "repo": repo,
        "strat_root": strat_root,
        "panel": workspace / cfg.repo_root / cfg.panel_path,
        "catalog": workspace / cfg.catalog_path,
        "calendar": workspace / cfg.calendar_source,
        "ledger": workspace / cfg.repo_root / cfg.ledger_source,
        "output": workspace / cfg.output_dir,
    }


def get_adapter_dir(strategy_id: str) -> Path:
    """Return the directory containing the strategy's adapters.py.

    For production strategies, directory name == strategy_id.
    For test strategies, the directory name may differ, so we scan
    all test subdirectories for a matching strategy_id in strategy.yaml.
    """
    prod = STRATEGIES_DIR / strategy_id
    if (prod / "adapters.py").exists():
        return prod
    # Scan test subdirectories — directory name may not match strategy_id
    if TEST_STRATEGIES_DIR.exists():
        for d in sorted(TEST_STRATEGIES_DIR.iterdir()):
            if not d.is_dir():
                continue
            yaml_path = d / "strategy.yaml"
            if not yaml_path.exists():
                continue
            try:
                raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                if raw.get("strategy_id") == strategy_id:
                    return d
            except Exception:
                pass
    raise ImportError(f"No adapters.py found for strategy '{strategy_id}'")
