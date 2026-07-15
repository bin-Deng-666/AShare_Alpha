#!/usr/bin/env python3
"""简单查看已下载的 A 股量化数据。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "parquet"


def count_code_files(directory: Path) -> int:
    """Count per-security parquet files (stem == 6-digit code)."""
    if not directory.exists():
        return 0
    return sum(1 for path in directory.glob("*.parquet") if path.stem.isdigit())


def format_coverage(actual: int, expected: int) -> str:
    if expected <= 0:
        return f"{actual:,} / —"
    pct = 100.0 * actual / expected
    return f"{actual:,} / {expected:,}  ({pct:5.1f}%)"


def print_frame(frame: pd.DataFrame, rows: int) -> None:
    if frame.empty:
        print("暂无数据")
        return
    with pd.option_context(
        "display.max_columns", None,
        "display.width", 180,
        "display.max_colwidth", 40,
    ):
        print(frame.tail(rows).to_string(index=False))


def show_summary() -> None:
    master_path = PARQUET / "security_master" / "security_master.parquet"
    if not master_path.exists():
        print("股票主表尚未生成。")
        return

    master = pd.read_parquet(master_path)
    total = len(master)
    listed = int(master["status"].eq("listed").sum())
    delisted = int(master["status"].eq("delisted").sum())
    sh_sz = int(master["exchange"].isin(["SH", "SZ"]).sum())
    bj = int(master["exchange"].eq("BJ").sum())

    print("A股量化数据概览")
    print("=" * 72)
    print(f"股票总数：{total:,}    上市：{listed:,}    退市：{delisted:,}")
    print(f"市场结构：SH/SZ {sh_sz:,}    BJ {bj:,}")
    print()
    print(f"{'数据层':<16}{'实际/理论':<28}说明")
    print("-" * 72)

    raw_dir = PARQUET / "daily_bars" / "adjustment=raw"
    qfq_dir = PARQUET / "daily_bars" / "adjustment=qfq"
    raw_n = count_code_files(raw_dir)
    qfq_n = count_code_files(qfq_dir)

    rows = [
        ("日线 raw", raw_n, total, "主表全市场；退市/极早上市可能缺"),
        ("日线 qfq", qfq_n, total, "应与 raw 一一对应"),
        (
            "每日指标",
            count_code_files(PARQUET / "daily_indicators"),
            total,
            "SH/SZ=BaoStock；BJ=bars+流通市值派生换手",
        ),
        (
            "财务三表",
            count_code_files(PARQUET / "financials"),
            total,
            "主表全市场",
        ),
        (
            "财报公告",
            count_code_files(PARQUET / "announcements"),
            total,
            "主表全市场；巨潮无记录可为空文件",
        ),
        (
            "公司行为",
            count_code_files(PARQUET / "corporate_actions"),
            total,
            "主表全市场；无分红送转也落空文件",
        ),
        (
            "股本结构",
            count_code_files(PARQUET / "equity_structure"),
            total,
            "主表全市场（流通股历史来源）",
        ),
        (
            "每日股本",
            count_code_files(PARQUET / "shares_daily"),
            total,
            "有股本事件且能对齐交易日才生成",
        ),
        (
            "涨跌停价",
            count_code_files(PARQUET / "limit_prices"),
            raw_n if raw_n else total,
            "理论=已有 raw 日线的股票数",
        ),
    ]
    for label, actual, expected, note in rows:
        print(f"{label:<16}{format_coverage(actual, expected):<28}{note}")

    # Snapshot: one file, listed-row target.
    snapshots = sorted((PARQUET / "market_snapshots").glob("*.parquet"))
    if snapshots:
        snapshot = pd.read_parquet(snapshots[-1])
        print(
            f"{'行情快照':<16}"
            f"{format_coverage(len(snapshot), listed):<28}"
            f"最新文件 {snapshots[-1].stem}；理论=当前上市数"
        )
    else:
        print(f"{'行情快照':<16}{format_coverage(0, 1):<28}理论=1 个快照文件")

    reference_targets = [
        ("交易日历", PARQUET / "reference" / "trade_calendar.parquet"),
        ("财务可得日", PARQUET / "reference" / "financial_availability.parquet"),
        ("行业区间", PARQUET / "reference" / "industry_membership_intervals.parquet"),
        ("指数成分区间", PARQUET / "reference" / "index_constituent_intervals.parquet"),
        ("行业成分快照", PARQUET / "reference" / "industry_membership.parquet"),
        ("指数成分快照", PARQUET / "reference" / "index_constituents.parquet"),
    ]
    present = sum(1 for _, path in reference_targets if path.exists())
    print(
        f"{'参考核心表':<16}"
        f"{format_coverage(present, len(reference_targets)):<28}"
        "日历/可得日/行业·指数区间与快照"
    )
    missing_ref = [label for label, path in reference_targets if not path.exists()]
    if missing_ref:
        print(f"{'':16}缺失：{', '.join(missing_ref)}")

    shares_dir = PARQUET / "shares_daily"
    if shares_dir.exists():
        float_files = 0
        share_files = sorted(shares_dir.glob("*.parquet"))
        for path in share_files:
            frame = pd.read_parquet(path, columns=["float_shares"])
            if frame["float_shares"].notna().any():
                float_files += 1
        # Denominator is shares_daily itself: how many daily share files already
        # carry float_shares. Equity-structure backfill progress is shown above.
        print(
            f"{'流通股已写入':<16}"
            f"{format_coverage(float_files, len(share_files)):<28}"
            "shares_daily 中已有 float_shares 的文件占比"
        )

    quality_path = ROOT / "quality_report.json"
    if quality_path.exists():
        report = json.loads(quality_path.read_text(encoding="utf-8"))
        print(
            f"\n质量报告：{report.get('status', 'n/a')} "
            f"（生成于 {report.get('generated_at', 'n/a')}）"
        )
        avail = report.get("financial_availability_quality") or {}
        if avail:
            print(
                "财务可得日："
                + ", ".join(f"{k}={v:,}" for k, v in sorted(avail.items(), key=lambda x: -x[1]))
            )

    catalog = ROOT / "catalog" / "quant.duckdb"
    warehouse = ROOT / "warehouse" / "stock_daily"
    if catalog.exists():
        print(f"研究目录：{catalog.relative_to(ROOT)} 已生成")
    if warehouse.exists():
        parts = len(list(warehouse.rglob("*.parquet")))
        print(f"研究宽表：warehouse/stock_daily 分区文件 {parts}")

    print("说明：理论总量按当前主表与数据契约计算，不等于历史永远可补全数。")


def show_file(file_name: str, rows: int) -> None:
    path = Path(file_name).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        raise SystemExit("只能查看 a_share_quant_data 目录内的文件")
    if not path.exists() or not path.is_file():
        raise SystemExit(f"文件不存在：{path}")

    print(f"文件：{path.relative_to(ROOT)}")
    print("=" * 80)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        frame = pd.read_parquet(path)
        print(f"行数：{len(frame):,}，列数：{len(frame.columns)}")
        print(f"字段：{', '.join(frame.columns)}\n")
        print_frame(frame, rows)
    elif suffix == ".csv":
        print_frame(pd.read_csv(path), rows)
    elif suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    elif suffix in (".txt", ".log"):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        print("\n".join(lines[-rows:]))
    else:
        raise SystemExit(f"暂不支持查看 {suffix} 文件")


def show_samples(rows: int) -> None:
    categories = [
        ("股票主表", PARQUET / "security_master"),
        ("日线", PARQUET / "daily_bars"),
        ("每日指标", PARQUET / "daily_indicators"),
        ("财务", PARQUET / "financials"),
        ("公告", PARQUET / "announcements"),
        ("公司行为", PARQUET / "corporate_actions"),
        ("股本结构", PARQUET / "equity_structure"),
        ("每日股本", PARQUET / "shares_daily"),
        ("涨跌停价", PARQUET / "limit_prices"),
        ("行情快照", PARQUET / "market_snapshots"),
        ("参考数据", PARQUET / "reference"),
    ]
    for label, directory in categories:
        files = list(directory.rglob("*.parquet")) if directory.exists() else []
        if not files:
            continue
        latest = max(files, key=lambda item: item.stat().st_mtime)
        frame = pd.read_parquet(latest)
        print(f"\n{label}示例：{latest.relative_to(ROOT)}")
        print("-" * 80)
        print_frame(frame, min(rows, 5))


def find_security(code: str) -> pd.Series:
    master = pd.read_parquet(
        PARQUET / "security_master" / "security_master.parquet"
    )
    matched = master[master["code"].astype(str).eq(code)]
    if matched.empty:
        raise SystemExit(f"股票代码 {code} 不在股票主表中")
    return matched.iloc[0]


def show_stock(code: str, rows: int, adjustment: str) -> None:
    security = find_security(code)
    print(
        f"{security['name']}（{code}.{security['exchange']}）"
        f"  状态：{security['status']}"
    )
    if pd.notna(security.get("list_date")):
        print(f"上市日：{pd.Timestamp(security['list_date']).date()}", end="")
        if pd.notna(security.get("delist_date")):
            print(f"  退市日：{pd.Timestamp(security['delist_date']).date()}")
        else:
            print()
    print("=" * 80)

    bar_path = (
        PARQUET / "daily_bars" / f"adjustment={adjustment}" / f"{code}.parquet"
    )
    if bar_path.exists():
        bars = pd.read_parquet(bar_path).sort_values("date")
        print(
            f"\n{adjustment} 日线：{len(bars):,} 行，"
            f"{bars['date'].min().date()} 至 {bars['date'].max().date()}"
        )
        print_frame(
            bars[["date", "open", "high", "low", "close", "volume", "amount"]],
            rows,
        )
    else:
        print(f"\n{adjustment} 日线尚未下载")

    indicator_path = PARQUET / "daily_indicators" / f"{code}.parquet"
    if indicator_path.exists():
        indicators = pd.read_parquet(indicator_path).sort_values("date")
        print("\n最近每日估值与交易状态")
        columns = [
            "date", "turn", "tradestatus", "pctChg", "peTTM",
            "pbMRQ", "psTTM", "isST",
        ]
        print_frame(indicators[[c for c in columns if c in indicators.columns]], min(rows, 10))

    limit_path = PARQUET / "limit_prices" / f"{code}.parquet"
    if limit_path.exists():
        limits = pd.read_parquet(limit_path).sort_values("date")
        print("\n最近涨跌停价")
        cols = [
            "date", "prev_close", "close", "limit_up", "limit_down",
            "limit_pct", "is_limit_up", "is_limit_down",
        ]
        print_frame(
            limits[[c for c in cols if c in limits.columns]],
            min(rows, 10),
        )

    shares_path = PARQUET / "shares_daily" / f"{code}.parquet"
    if shares_path.exists():
        shares = pd.read_parquet(shares_path).sort_values("date")
        float_pct = shares["float_shares"].notna().mean() * 100
        print(
            f"\n最近每日股本（流通股覆盖 {float_pct:.1f}%）"
            f"  来源：{shares['source'].iloc[-1] if len(shares) else ''}"
        )
        print_frame(
            shares[
                [
                    "date", "total_shares", "float_shares",
                    "total_market_cap", "float_market_cap",
                ]
            ],
            min(rows, 10),
        )

    equity_path = PARQUET / "equity_structure" / f"{code}.parquet"
    if equity_path.exists():
        equity = pd.read_parquet(equity_path).sort_values("end_date")
        print("\n最近股本结构变动")
        columns = [
            "end_date", "change_reason", "total_shares", "float_shares",
            "listed_a_shares", "limited_shares",
        ]
        print_frame(equity[[c for c in columns if c in equity.columns]], min(rows, 10))

    action_path = PARQUET / "corporate_actions" / f"{code}.parquet"
    if action_path.exists():
        actions = pd.read_parquet(action_path)
        if not actions.empty:
            actions = actions.sort_values(["ex_date", "publish_date"])
            print("\n最近公司行为")
            columns = [
                "ex_date", "cash_dividend_per_10", "bonus_share_per_10",
                "transfer_share_per_10", "total_shares", "plan_description",
            ]
            print_frame(actions[[c for c in columns if c in actions.columns]], min(rows, 10))

    financial_path = PARQUET / "financials" / f"{code}.parquet"
    if financial_path.exists():
        financials = pd.read_parquet(financial_path)
        latest_date = financials["report_date"].max()
        latest = financials[financials["report_date"].eq(latest_date)]
        preferred_items = [
            "营业收入", "营业总收入", "归属于母公司股东的净利润",
            "净利润", "资产总计", "负债合计", "经营活动产生的现金流量净额",
        ]
        selected = latest[latest["item"].isin(preferred_items)]
        if selected.empty:
            selected = latest
        print(f"\n最新财报期：{pd.Timestamp(latest_date).date()}，展示主要科目")
        cols = ["statement_type", "item", "value", "year_on_year"]
        if "item_code" in selected.columns:
            cols = ["statement_type", "item_code", "item", "value", "year_on_year"]
        print_frame(selected[[c for c in cols if c in selected.columns]], rows)
    else:
        print("\n财务数据尚未下载")

    availability_path = PARQUET / "reference" / "financial_availability.parquet"
    if availability_path.exists():
        availability = pd.read_parquet(availability_path)
        code_avail = availability[availability["code"].astype(str).eq(code)]
        if not code_avail.empty:
            code_avail = code_avail.sort_values("report_date")
            print("\n最近财务可得日")
            cols = [
                c for c in [
                    "report_date", "available_date", "match_quality",
                    "announcement_date", "title",
                ]
                if c in code_avail.columns
            ]
            print_frame(code_avail[cols], min(rows, 10))

    announcement_path = PARQUET / "announcements" / f"{code}.parquet"
    if announcement_path.exists():
        announcements = pd.read_parquet(announcement_path).sort_values(
            "announcement_date"
        )
        print("\n最近财报公告")
        print_frame(
            announcements[["announcement_date", "title", "category"]],
            min(rows, 10),
        )
    else:
        print("\n公告数据尚未下载")

    snapshots = sorted((PARQUET / "market_snapshots").glob("*.parquet"))
    if snapshots:
        snapshot = pd.read_parquet(snapshots[-1])
        quote = snapshot[snapshot["code"].astype(str).eq(code)]
        if not quote.empty:
            columns = [
                "snapshot_time", "price", "change_pct", "turnover_pct",
                "pe_ttm", "pb", "market_cap_yi",
            ]
            print("\n最新行情快照")
            print_frame(quote[[c for c in columns if c in quote.columns]], 1)


def search_stocks(keyword: str, rows: int) -> None:
    master = pd.read_parquet(
        PARQUET / "security_master" / "security_master.parquet"
    )
    keyword = keyword.strip()
    matched = master[
        master["code"].astype(str).str.contains(keyword, na=False)
        | master["name"].astype(str).str.contains(keyword, case=False, na=False)
    ]
    print_frame(
        matched[["code", "name", "exchange", "status", "list_date", "delist_date"]],
        rows,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", help="查看指定股票，如 600519")
    parser.add_argument("--search", help="按代码或名称搜索股票")
    parser.add_argument(
        "--file",
        help="查看指定数据文件，如 parquet/daily_bars/adjustment=raw/600519.parquet",
    )
    parser.add_argument(
        "--samples",
        action="store_true",
        help="在数据概览后展示各类文件的内容样例",
    )
    parser.add_argument("--rows", type=int, default=10, help="显示行数，默认10")
    parser.add_argument(
        "--adjustment",
        choices=["raw", "qfq"],
        default="qfq",
        help="日线复权方式，默认前复权",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.file:
        show_file(args.file, args.rows)
    elif args.code:
        show_stock(args.code.zfill(6), args.rows, args.adjustment)
    elif args.search:
        search_stocks(args.search, args.rows)
    else:
        show_summary()
        if args.samples:
            show_samples(args.rows)


if __name__ == "__main__":
    main()
