"""Reject incomplete core date coverage before declaring an update successful."""

from __future__ import annotations
from pathlib import Path
import pandas as pd
import duckdb
from ..paths import PARQUET_ROOT, STATE_ROOT
from ..storage import read_json
from ..pipeline.receipts import fingerprint


def confirmed_indicator_gaps(root=PARQUET_ROOT, state_root=STATE_ROOT):
    """Only accept source-checked holes whose underlying files still match."""
    records = read_json(Path(state_root) / "indicator_source_gaps.json", {})
    rows = []
    for code, record in records.items():
        raw = Path(root) / "daily_bars/adjustment=raw" / f"{code}.parquet"
        indicator = Path(root) / "daily_indicators" / f"{code}.parquet"
        if (
            record.get("source") != "baostock"
            or not record.get("checked_through")
            or not raw.exists()
            or not indicator.exists()
            or record.get("raw") != fingerprint([raw])
            or record.get("indicator") != fingerprint([indicator])
        ):
            continue
        rows.extend(
            {"code": code, "date": date} for date in record.get("missing_dates", [])
        )
    frame = pd.DataFrame(rows, columns=["code", "date"])
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def validate_core_coverage(root: Path = PARQUET_ROOT, state_root: Path = STATE_ROOT):
    master = pd.read_parquet(
        root / "security_master/security_master.parquet", columns=["code"]
    )
    codes = set(master.code.astype(str))
    for relative in (
        "daily_bars/adjustment=raw",
        "daily_bars/adjustment=qfq",
        "daily_indicators",
        "shares_daily",
        "limit_prices",
    ):
        present = {p.stem for p in (root / relative).glob("*.parquet")}
        missing = codes - present
        if missing:
            raise RuntimeError(
                f"{relative}: missing {len(missing)} securities: {sorted(missing)[:10]}"
            )
    raw = str(root / "daily_bars/adjustment=raw/*.parquet").replace("'", "''")
    qfq = str(root / "daily_bars/adjustment=qfq/*.parquet").replace("'", "''")
    with duckdb.connect() as con:
        con.register("source_gaps", confirmed_indicator_gaps(root, state_root))
        mismatch = con.execute(f"""SELECT coalesce(a.code,b.code), coalesce(a.date,b.date)
            FROM read_parquet('{raw}') a FULL OUTER JOIN read_parquet('{qfq}') b
            ON a.code=b.code AND a.date=b.date
            WHERE a.code IS NULL OR b.code IS NULL LIMIT 1""").fetchone()
        if mismatch:
            raise RuntimeError(f"raw/qfq dates differ at {mismatch}")
        for name in ("limit_prices", "daily_indicators", "shares_daily"):
            asset = str(root / name / "*.parquet").replace("'", "''")
            exceptions = (
                "ANTI JOIN source_gaps g ON a.code=g.code AND a.date=g.date"
                if name == "daily_indicators"
                else ""
            )
            missing = con.execute(f"""SELECT a.code,a.date FROM read_parquet('{raw}') a
                ANTI JOIN read_parquet('{asset}', union_by_name=true) b
                ON a.code=b.code AND a.date=b.date {exceptions} LIMIT 1""").fetchone()
            if missing:
                raise RuntimeError(f"{name} missing raw bar key: {missing}")
