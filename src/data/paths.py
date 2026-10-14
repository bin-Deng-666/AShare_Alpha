"""Canonical storage paths; all source and derived modules share this root."""

from pathlib import Path

from ashare_alpha.paths import CODE_ROOT, DATA_ROOT as DATA_ROOT

REPO_ROOT = CODE_ROOT
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "dataset.json"
PARQUET_ROOT = DATA_ROOT / "parquet"
CATALOG_ROOT = DATA_ROOT / "catalog"
CATALOG_PATH = CATALOG_ROOT / "quant.duckdb"
STATE_ROOT = DATA_ROOT / "state"
RAW_ROOT = DATA_ROOT / "raw"
