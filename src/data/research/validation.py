"""research — validation: data acquisition and normalization."""

from __future__ import annotations
import datetime as dt
import logging
from typing import Dict
import pandas as pd
import duckdb
from ashare_alpha.data.paths import DATA_ROOT as ROOT, PARQUET_ROOT as PARQUET

from ashare_alpha.data.storage import atomic_json


def build_manifest(config: Dict) -> None:
    master_path = PARQUET / "security_master" / "security_master.parquet"
    master = pd.read_parquet(master_path) if master_path.exists() else pd.DataFrame()
    files = list(PARQUET.rglob("*.parquet"))
    bars = [path for path in files if "daily_bars" in path.parts]
    financials = [path for path in files if "financials" in path.parts]
    announcements = [path for path in files if "announcements" in path.parts]
    corporate_actions = [path for path in files if "corporate_actions" in path.parts]
    manifest = {
        "generated_at": dt.datetime.now(),
        "configured_start_date": config["start_date"],
        "configured_end_date": config["end_date"] or str(dt.date.today()),
        "security_count": len(master),
        "listed_count": int(master["status"].eq("listed").sum())
        if not master.empty
        else 0,
        "delisted_count": int(master["status"].eq("delisted").sum())
        if not master.empty
        else 0,
        "bar_files": len(bars),
        "financial_files": len(financials),
        "announcement_files": len(announcements),
        "corporate_action_files": len(corporate_actions),
        "limitations": [
            "Historical universe is best-effort; free exchange lists may omit renamed or migrated securities.",
            "Forward-adjusted prices are snapshots and can change after future corporate actions.",
            "Sina statements are aligned to CNInfo announcement dates only approximately; revisions are not versioned.",
            "Beijing and delisted bars may use the rate-limited Eastmoney fallback.",
        ],
    }
    atomic_json(manifest, ROOT / "manifest.json")


def validate_dataset(config: Dict) -> None:
    del config
    master = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    issues = []
    if master["code"].duplicated().any():
        issues.append("duplicate security codes in master")
    listed_missing_dates = int(
        (master["status"].eq("listed") & master["list_date"].isna()).sum()
    )
    if listed_missing_dates:
        issues.append(f"{listed_missing_dates} listed securities missing list_date")

    master_codes = set(master["code"].astype(str))
    raw_files = sorted((PARQUET / "daily_bars" / "adjustment=raw").glob("*.parquet"))
    qfq_files = sorted((PARQUET / "daily_bars" / "adjustment=qfq").glob("*.parquet"))
    raw_codes = {path.stem for path in raw_files}
    qfq_codes = {path.stem for path in qfq_files}
    raw_rows = 0
    qfq_rows = 0
    min_date = None
    max_date = None
    invalid_ohlc = 0
    duplicate_keys = 0
    qfq_date_mismatch_codes = []
    for path in raw_files:
        frame = pd.read_parquet(path)
        raw_rows += len(frame)
        if frame.empty:
            continue
        current_min = pd.to_datetime(frame["date"]).min()
        current_max = pd.to_datetime(frame["date"]).max()
        min_date = current_min if min_date is None else min(min_date, current_min)
        max_date = current_max if max_date is None else max(max_date, current_max)
        invalid_ohlc += int(
            (
                (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
                | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
            ).sum()
        )
        duplicate_keys += int(frame.duplicated(["code", "date"]).sum())

        qfq_path = PARQUET / "daily_bars" / "adjustment=qfq" / path.name
        if qfq_path.exists():
            qfq_dates = pd.read_parquet(qfq_path, columns=["date"])["date"]
            qfq_rows += len(qfq_dates)
            if not pd.Index(frame["date"]).equals(pd.Index(qfq_dates)):
                qfq_date_mismatch_codes.append(path.stem)
    if qfq_date_mismatch_codes:
        issues.append(
            f"{len(qfq_date_mismatch_codes)} qfq files do not align with raw dates"
        )

    financial_duplicate_rows = 0
    financial_conflicting_keys = 0
    financial_files = sorted((PARQUET / "financials").glob("*.parquet"))
    financial_keys = ["code", "report_date", "statement_type", "item_code"]
    for path in financial_files:
        frame = pd.read_parquet(
            path,
            columns=financial_keys + ["value"],
        )
        duplicate_mask = frame.duplicated(financial_keys, keep=False)
        financial_duplicate_rows += int(duplicate_mask.sum())
        if duplicate_mask.any():
            for _, group in frame.loc[duplicate_mask].groupby(
                financial_keys, dropna=False
            ):
                if len(group["value"].dropna().unique()) > 1:
                    financial_conflicting_keys += 1
    if financial_duplicate_rows:
        issues.append(f"{financial_duplicate_rows} duplicate financial statement rows")

    announcement_files = sorted((PARQUET / "announcements").glob("*.parquet"))
    duplicate_announcements = 0
    for path in announcement_files:
        frame = pd.read_parquet(path, columns=["announcement_id"])
        duplicate_announcements += int(frame["announcement_id"].duplicated().sum())
    if duplicate_announcements:
        issues.append(f"{duplicate_announcements} duplicate announcements")

    availability_path = PARQUET / "reference" / "financial_availability.parquet"
    availability_quality = {}
    if availability_path.exists():
        availability = pd.read_parquet(availability_path)
        availability_quality = (
            availability["availability_quality"].value_counts(dropna=False).to_dict()
        )

    interval_checks = {}
    for name, path, keys, groups in (
        (
            "industry",
            PARQUET / "reference" / "industry_membership_intervals.parquet",
            [
                "code",
                "classification_system",
                "industry_level",
                "industry_code",
                "valid_from",
            ],
            ["code", "classification_system"],
        ),
        (
            "index_constituent",
            PARQUET / "reference" / "index_constituent_intervals.parquet",
            ["index_code", "code", "valid_from"],
            ["index_code", "code"],
        ),
    ):
        if not path.exists():
            continue
        frame = pd.read_parquet(path)
        duplicate_interval_keys = int(frame.duplicated(keys).sum())
        invalid_ranges = int(
            (
                frame["valid_to"].notna() & (frame["valid_to"] <= frame["valid_from"])
            ).sum()
        )
        overlaps = 0
        for _, group in frame.sort_values("valid_from").groupby(groups):
            previous_end = None
            for row in group.itertuples(index=False):
                if (
                    previous_end is not None
                    and pd.notna(previous_end)
                    and row.valid_from < previous_end
                ):
                    overlaps += 1
                previous_end = row.valid_to
        interval_checks[name] = {
            "rows": len(frame),
            "duplicate_keys": duplicate_interval_keys,
            "invalid_ranges": invalid_ranges,
            "overlaps": overlaps,
        }
        if duplicate_interval_keys or invalid_ranges or overlaps:
            issues.append(f"invalid {name} validity intervals")

    indicator_files = list((PARQUET / "daily_indicators").glob("*.parquet"))
    corporate_action_files = list((PARQUET / "corporate_actions").glob("*.parquet"))
    shares_daily_files = list((PARQUET / "shares_daily").glob("*.parquet"))
    equity_structure_files = list((PARQUET / "equity_structure").glob("*.parquet"))
    limit_price_files = list((PARQUET / "limit_prices").glob("*.parquet"))
    for layer, paths in (
        ("raw_bars", raw_files),
        ("qfq_bars", qfq_files),
        ("daily_indicators", indicator_files),
        ("financials", financial_files),
        ("announcements", announcement_files),
        ("corporate_actions", corporate_action_files),
        ("shares_daily", shares_daily_files),
        ("equity_structure", equity_structure_files),
        ("limit_prices", limit_price_files),
    ):
        missing = master_codes - {p.stem for p in paths}
        if missing:
            issues.append(
                f"{layer}: {len(missing)} missing securities: {sorted(missing)[:10]}"
            )
    float_filled = 0
    for path in shares_daily_files:
        sample = pd.read_parquet(path, columns=["float_shares"])
        if sample["float_shares"].notna().any():
            float_filled += 1
    from .coverage import confirmed_indicator_gaps

    verified_source_gaps = confirmed_indicator_gaps(PARQUET)
    date_coverage = {}
    if raw_files:
        raw_glob = str(PARQUET / "daily_bars/adjustment=raw/*.parquet").replace(
            "'", "''"
        )
        with duckdb.connect() as con:
            for name, directory in [
                ("daily_indicators", "daily_indicators"),
                ("shares_daily", "shares_daily"),
                ("limit_prices", "limit_prices"),
            ]:
                if not any((PARQUET / directory).glob("*.parquet")):
                    continue
                asset_glob = str(PARQUET / directory / "*.parquet").replace("'", "''")
                rows = con.execute(f"""SELECT a.code, count(*) AS missing_dates
                    FROM read_parquet('{raw_glob}',union_by_name=true) a
                    ANTI JOIN read_parquet('{asset_glob}',union_by_name=true) b
                    ON a.code=b.code AND a.date=b.date
                    GROUP BY a.code ORDER BY a.code""").fetchall()
                date_coverage[name] = {
                    "securities_with_missing_dates": len(rows),
                    "missing_raw_keys": sum(int(row[1]) for row in rows),
                    "sample": [
                        {"code": row[0], "missing_dates": row[1]} for row in rows[:10]
                    ],
                }
                if rows:
                    issues.append(
                        f"{name}: {len(rows)} securities missing raw bar dates"
                    )
    report = {
        "generated_at": dt.datetime.now(),
        "security_count": len(master),
        "listed_missing_list_date": listed_missing_dates,
        "coverage": {
            "raw_bars": {
                "files": len(raw_files),
                "missing_master_codes": len(master_codes - raw_codes),
            },
            "qfq_bars": {
                "files": len(qfq_files),
                "missing_master_codes": len(master_codes - qfq_codes),
            },
            "daily_indicators": {"files": len(indicator_files)},
            "financials": {"files": len(financial_files)},
            "announcements": {"files": len(announcement_files)},
            "corporate_actions": {"files": len(corporate_action_files)},
            "equity_structure": {"files": len(equity_structure_files)},
            "shares_daily": {
                "files": len(shares_daily_files),
                "with_float_shares": float_filled,
            },
            "limit_prices": {"files": len(limit_price_files)},
        },
        "confirmed_indicator_source_gaps": [
            {"code": row.code, "date": str(row.date.date()), "source": "baostock"}
            for row in verified_source_gaps.itertuples(index=False)
        ],
        "date_coverage": date_coverage,
        "security_status_counts": master["status"].value_counts().to_dict(),
        "raw_bar_row_count": raw_rows,
        "qfq_bar_row_count": qfq_rows,
        "bar_min_date": min_date,
        "bar_max_date": max_date,
        "invalid_ohlc_rows": invalid_ohlc,
        "duplicate_bar_keys": duplicate_keys,
        "qfq_date_mismatch_count": len(qfq_date_mismatch_codes),
        "qfq_date_mismatch_sample": qfq_date_mismatch_codes[:100],
        "financial_duplicate_rows": financial_duplicate_rows,
        "financial_conflicting_keys": financial_conflicting_keys,
        "duplicate_announcements": duplicate_announcements,
        "financial_availability_quality": availability_quality,
        "interval_checks": interval_checks,
        "issues": issues,
        "status": "pass"
        if not issues and invalid_ohlc == 0 and duplicate_keys == 0
        else "review",
    }
    atomic_json(report, ROOT / "quality_report.json")
    logging.info("Quality report: %s", report)
