#!/usr/bin/env python3
"""Ingest shareholder change (股东增减持) data via akshare stock_ggcg_em.

Writes to:
  parquet/reference/holder_change/holder_change.parquet
  DuckDB catalog table: holder_change

PIT alignment: 公告日 (announce_date) is the availability date.
  变动开始日/变动截止日 are the actual transaction dates — NOT used for PIT.
"""
from __future__ import annotations
import logging
from datetime import datetime
import pandas as pd

from data.ashare_data.paths import DATA_ROOT as ROOT
from data.ashare_data.storage import atomic_parquet
PARQUET = ROOT / "parquet" / "reference" / "holder_change"

def refresh(config):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("=== Ingesting shareholder change data ===", flush=True)

    import akshare as ak
    import warnings; warnings.filterwarnings("ignore")

    print("  Fetching all holder changes (stock_ggcg_em)...", flush=True)
    df = ak.stock_ggcg_em(symbol="全部")
    print(f"  Raw rows: {len(df)}", flush=True)

    # Rename columns
    df = df.rename(columns={
        "代码": "code", "名称": "name",
        "股东名称": "shareholder",
        "持股变动信息-增减": "direction",
        "持股变动信息-变动数量": "change_shares_wan",
        "持股变动信息-占总股本比例": "change_pct_total",
        "持股变动信息-占流通股比例": "change_pct_float",
        "变动开始日": "change_start_date",
        "变动截止日": "change_end_date",
        "公告日": "announce_date",
    })

    # Filter columns
    keep = ["code", "name", "shareholder", "direction",
            "change_shares_wan", "change_pct_total", "change_pct_float",
            "change_start_date", "change_end_date", "announce_date"]
    df = df[keep]

    # Type conversions
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["announce_date"] = pd.to_datetime(df["announce_date"], errors="coerce")
    df["change_start_date"] = pd.to_datetime(df["change_start_date"], errors="coerce")
    df["change_end_date"] = pd.to_datetime(df["change_end_date"], errors="coerce")
    df["change_pct_float"] = pd.to_numeric(df["change_pct_float"], errors="coerce")
    df["change_pct_total"] = pd.to_numeric(df["change_pct_total"], errors="coerce")

    # Filter for 2020+ by announce_date (PIT anchor)
    df = df[df["announce_date"].between(pd.Timestamp(config["start_date"]), pd.Timestamp(config["end_date"]))].copy()
    df["source"] = "eastmoney"
    df["ingest_ts"] = datetime.now().isoformat()

    # Direction: 增持 → +1, 减持 → -1
    df["direction_code"] = df["direction"].map({"增持": 1, "减持": -1}).fillna(0)

    df = df.sort_values("announce_date").reset_index(drop=True)
    print(f"  Filtered rows (2020+): {len(df)}", flush=True)
    print(f"  Date range: {df['announce_date'].min().date()} to {df['announce_date'].max().date()}", flush=True)
    print(f"  Unique codes: {df['code'].nunique()}", flush=True)
    print(f"  Direction: 增持={len(df[df['direction']=='增持'])}, 减持={len(df[df['direction']=='减持'])}", flush=True)

    PARQUET.mkdir(parents=True, exist_ok=True)
    pq_path = PARQUET / "holder_change.parquet"
    if df.empty:
        raise RuntimeError('Holder-change source returned empty; keeping previous data')
    if pq_path.exists():
        df = pd.concat([pd.read_parquet(pq_path), df], ignore_index=True)
        df = df.drop_duplicates(['code', 'shareholder', 'direction', 'change_start_date', 'change_end_date', 'announce_date'], keep='last')
    atomic_parquet(df, pq_path)
    print(f"  Saved to {pq_path}", flush=True)
