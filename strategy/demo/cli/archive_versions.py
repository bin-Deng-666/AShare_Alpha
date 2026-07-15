"""Archive frozen version backtests under releases/<ver>/."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.metrics import compute_metrics
from backtest.portfolio import run_portfolio
from cli.run_backtest import load_bars, load_hs300, write_report_bundle
from config.params import REPORT_DIR, SAMPLE_START
from config.versions import ARCHIVABLE, CURRENT_VERSION, VERSION_PRESETS
from features.build_panel import load_signals

RELEASES_DIR = ROOT / "releases"
ARCHIVE_ABLATIONS = ROOT / "archive" / "experiments" / "ablations"


def _write_meta(out: Path, version: str, params, metrics: dict, panel: Path) -> None:
    cap = metrics["capital"]
    ret = metrics["returns"]
    risk = metrics["risk"]
    tr = metrics["trading"]
    meta = {
        "version": version,
        "lineage": "live",
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "panel": str(panel),
        "sample_start": SAMPLE_START,
        "benchmark": "沪深300",
        "initial_capital": params.initial_capital,
        "headline": {
            "total_return": cap["total_return"],
            "ann_return": ret["ann_return"],
            "max_drawdown": risk["max_drawdown"],
            "sharpe": risk["sharpe"],
            "vs_hs300_total": ret["excess_total"],
            "fees": cap["total_fees"],
            "n_buys": tr["n_buys"],
            "avg_cash_ratio": tr["avg_cash_ratio"],
        },
        "params": {
            k: (list(v) if isinstance(v, tuple) else v)
            for k, v in params.__dict__.items()
        },
    }
    (out / "META.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        f"# {version} 发布说明",
        "",
        f"- 归档时间（UTC）：`{meta['archived_at']}`",
        f"- 面板：`{panel}`",
        f"- 区间起点：`{SAMPLE_START}` · 基准：沪深300 · 本金：{params.initial_capital:,.0f}",
        "",
        "## 摘要",
        "",
        "| 总收益 | 年化 | 最大回撤 | Sharpe | vs沪深300 | 买入笔数 | 日均现金 |",
        "|--------|------|----------|--------|-----------|----------|----------|",
        (
            f"| {cap['total_return']:.2%} | {ret['ann_return']:.2%} | "
            f"{risk['max_drawdown']:.2%} | {risk['sharpe']:.2f} | "
            f"{ret['excess_total']:.2%} | {tr['n_buys']} | {tr['avg_cash_ratio']:.1%} |"
        ),
        "",
        "完整指标见同目录 `PERFORMANCE.md`；流水见 `trades.csv` / `ledger_daily.csv`。",
        "参数快照见 `META.json`。",
        "",
    ]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")


def _resolve_panel(params, fallback: Path) -> Path:
    from lib.panel_paths import default_panel_path

    preferred = default_panel_path()
    if preferred.exists():
        return preferred
    latest = ROOT / "cache" / "signals_2020-01-01_latest.parquet"
    if latest.exists():
        return latest
    if fallback.exists():
        return fallback
    return preferred


_ABLATION_BY_VERSION: dict[str, tuple[str, ...]] = {
    "v1.0": ("ablation_realistic_costs.csv",),
}


def _copy_ablations(version: str, out: Path) -> None:
    names = _ABLATION_BY_VERSION.get(version, ())
    for name in names:
        src = ARCHIVE_ABLATIONS / name
        if src.exists():
            shutil.copy2(src, out / name)


def archive_one(version: str, panel: Path, sync_latest: bool) -> dict:
    if version not in VERSION_PRESETS:
        raise SystemExit(f"unknown version: {version}")
    params = VERSION_PRESETS[version]()
    use_panel = _resolve_panel(params, panel)
    out = RELEASES_DIR / version
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    print(f"=== archive {version} ===")
    sig = load_signals(use_panel)
    sig["date"] = pd.to_datetime(sig["date"])
    sig = sig[sig["date"] >= pd.Timestamp(SAMPLE_START)]
    names = (
        sig.drop_duplicates("code")
        .set_index("code")["name"]
        .fillna("")
        .astype(str)
        .to_dict()
    )
    bars = load_bars(SAMPLE_START)
    hs = load_hs300(SAMPLE_START)
    port = run_portfolio(sig, bars, names=names, params=params, market=hs)
    if "error" in port:
        raise SystemExit(port)
    metrics = compute_metrics(port["daily"], port["trades"], hs, params.initial_capital)
    if "error" in metrics:
        raise SystemExit(metrics)

    tag = f"{SAMPLE_START}_latest"
    hub = write_report_bundle(port, metrics, params, tag, out)
    _write_meta(out, version, params, metrics, use_panel)
    _copy_ablations(version, out)

    if sync_latest and version == CURRENT_VERSION:
        write_report_bundle(port, metrics, params, tag, REPORT_DIR)
        print(f"synced latest reports/ → {CURRENT_VERSION}")

    h = metrics["capital"]["total_return"]
    print(f"  wrote {hub}  total_return={h:.2%}")
    return {
        "version": version,
        "total_return": metrics["capital"]["total_return"],
        "ann_return": metrics["returns"]["ann_return"],
        "max_drawdown": metrics["risk"]["max_drawdown"],
        "sharpe": metrics["risk"]["sharpe"],
        "vs_hs300": metrics["returns"]["excess_total"],
        "fees": metrics["capital"]["total_fees"],
        "buys": metrics["trading"]["n_buys"],
        "path": str(out),
    }


def write_releases_index(rows: list[dict]) -> None:
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# releases/",
        "",
        "活版本序列（A 股实测执行）。历史 flat15bp 见 [`../archive/versions/`](../archive/versions/)。",
        "",
        "| 版本 | 总收益 | 年化 | 最大回撤 | Sharpe | vs沪深300 | 目录 |",
        "|------|--------|------|----------|--------|-----------|------|",
    ]
    for r in rows:
        vs = r.get("vs_hs300")
        vs_s = f"{vs:.2%}" if isinstance(vs, (int, float)) and vs == vs else "—"
        lines.append(
            f"| {r['version']} | {r['total_return']:.2%} | {r['ann_return']:.2%} | "
            f"{r['max_drawdown']:.2%} | {r['sharpe']:.2f} | {vs_s} | "
            f"[`{r['version']}/`]({r['version']}/) |"
        )
    lines += [
        "",
        f"当前默认：**{CURRENT_VERSION}**（与 `reports/PERFORMANCE.*` 同步）。",
        "",
        "```bash",
        "python3 cli/archive_versions.py --version v1.0 --sync-latest",
        "```",
        "",
    ]
    (RELEASES_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")
    if rows:
        pd.DataFrame(rows).to_csv(RELEASES_DIR / "summary.csv", index=False)


def main() -> None:
    ap = argparse.ArgumentParser(description="Archive live-lineage version reports → releases/")
    ap.add_argument("--version", action="append", dest="versions", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument(
        "--panel",
        default=str(ROOT / "cache" / "signals_2020-01-01_v1.0.parquet"),
    )
    ap.add_argument(
        "--sync-latest",
        action="store_true",
        default=True,
        help="also refresh reports/ root when archiving CURRENT_VERSION",
    )
    ap.add_argument("--no-sync-latest", action="store_false", dest="sync_latest")
    args = ap.parse_args()

    panel = Path(args.panel)
    if not panel.exists():
        from lib.panel_paths import default_panel_path

        for alt in (
            default_panel_path(),
            ROOT / "cache" / "signals_2020-01-01_latest.parquet",
        ):
            if alt.exists():
                panel = alt
                print(f"panel missing; falling back to {panel}")
                break
        else:
            raise SystemExit(f"panel not found: {panel}")

    versions = list(ARCHIVABLE) if args.all else (args.versions or [CURRENT_VERSION])
    for v in versions:
        archive_one(v, panel, sync_latest=args.sync_latest)

    index_rows = []
    for v in ARCHIVABLE:
        perf = RELEASES_DIR / v / "PERFORMANCE.json"
        meta = RELEASES_DIR / v / "META.json"
        if perf.exists():
            data = json.loads(perf.read_text(encoding="utf-8"))
            # support both write_report_bundle shape and hand-authored META-derived
            if "capital" in data:
                index_rows.append(
                    {
                        "version": v,
                        "total_return": data["capital"]["total_return"],
                        "ann_return": data["returns"]["ann_return"],
                        "max_drawdown": data["risk"]["max_drawdown"],
                        "sharpe": data["risk"]["sharpe"],
                        "vs_hs300": data.get("returns", {}).get("excess_total"),
                        "fees": data["capital"].get("total_fees"),
                        "buys": data.get("trading", {}).get("n_buys"),
                        "path": str(RELEASES_DIR / v),
                    }
                )
            continue
        if meta.exists():
            m = json.loads(meta.read_text(encoding="utf-8"))
            h = m.get("headline", {})
            index_rows.append(
                {
                    "version": v,
                    "total_return": h.get("total_return"),
                    "ann_return": h.get("ann_return"),
                    "max_drawdown": h.get("max_drawdown"),
                    "sharpe": h.get("sharpe"),
                    "vs_hs300": h.get("vs_hs300_total"),
                    "fees": h.get("fees"),
                    "buys": h.get("n_buys"),
                    "path": str(RELEASES_DIR / v),
                }
            )
    write_releases_index(index_rows)
    print(f"\nindex → {RELEASES_DIR / 'README.md'}")


if __name__ == "__main__":
    main()
