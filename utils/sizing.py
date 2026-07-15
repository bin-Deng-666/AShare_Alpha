"""Translate picks into actionable buy/sell sizing suggestions."""

from __future__ import annotations

from typing import Any


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        if v != v:
            return default
        return v
    except (TypeError, ValueError):
        return default


def attach_orders(
    result: dict[str, Any],
    *,
    capital: float = 1_000_000.0,
    top_n: int = 5,
    max_single_weight: float = 0.25,
    satellite_weight_frac: float = 0.5,
    reduce_frac: float = 0.5,
    lot_size: int = 100,
    min_position_mult_to_buy: float = 0.7,
) -> dict[str, Any]:
    """
    Add `orders` with concrete 买卖建议.

    Buy sizing mirrors backtest equal-weight slot:
      per_name ≈ capital * position_mult / top_n, capped by max_single_weight.
    Sell: avoid → 清仓；reduce → 减仓 reduce_frac（仅当持有时）。
    """
    capital = max(float(capital), 0.0)
    buys = list(result.get("buy") or [])
    sats = list(result.get("satellite") or [])
    reds = list(result.get("reduce") or [])

    pos_mults = [
        _f(r.get("position_mult"), 0.0)
        for r in buys + sats
        if r.get("position_mult") is not None
    ]
    pos_mult = sorted(pos_mults)[len(pos_mults) // 2] if pos_mults else 1.0
    if pos_mult < min_position_mult_to_buy and not buys and not sats:
        pos_mult = 0.0

    per_core = (capital * max(pos_mult, 0.0)) / max(int(top_n), 1)
    per_core = min(per_core, capital * float(max_single_weight))
    per_sat = per_core * float(satellite_weight_frac)

    def _buy_order(row: dict[str, Any], notional: float, bucket: str) -> dict[str, Any]:
        px = _f(row.get("close"), 0.0)
        lots = None
        shares = None
        if px > 0 and notional > 0:
            shares = int(notional // (px * lot_size)) * lot_size
            lots = shares // lot_size
            notional = round(shares * px, 2) if shares >= lot_size else 0.0
        return {
            "side": "BUY",
            "code": row.get("code"),
            "name": row.get("name"),
            "action": row.get("action"),
            "bucket": bucket,
            "target_weight": round(notional / capital, 4) if capital > 0 else 0.0,
            "target_notional": notional,
            "ref_price": px if px > 0 else None,
            "approx_shares": shares if shares and shares >= lot_size else 0,
            "approx_lots": lots if lots else 0,
            "note": "按开盘价成交；手数按信号日收盘价粗算，开盘后以实际价重算",
        }

    buy_orders = [_buy_order(r, per_core, "core") for r in buys]
    sat_orders = [_buy_order(r, per_sat, "satellite") for r in sats]

    sell_orders = []
    for r in reds:
        action = str(r.get("action") or "")
        if action == "avoid":
            frac, note = 1.0, "若持有 → 清仓"
        elif action == "reduce":
            frac, note = float(reduce_frac), f"若持有 → 减仓约 {reduce_frac:.0%}"
        else:
            frac, note = float(reduce_frac), "若持有 → 按策略减仓"
        sell_orders.append(
            {
                "side": "SELL",
                "code": r.get("code"),
                "name": r.get("name"),
                "action": action,
                "sell_frac": frac,
                "note": note,
            }
        )

    result["orders"] = {
        "capital": capital,
        "position_mult": pos_mult,
        "per_core_notional": round(per_core, 2),
        "per_satellite_notional": round(per_sat, 2),
        "top_n": top_n,
        "max_single_weight": max_single_weight,
        "buy": buy_orders,
        "satellite": sat_orders,
        "sell": sell_orders,
    }
    return result


def format_orders_markdown(orders: dict[str, Any] | None) -> str:
    if not orders:
        return ""
    lines = [
        "## 操作建议（按策略等权仓位粗算）",
        "",
        f"- 假定权益：**{orders['capital']:,.0f} 元** · position_mult=`{orders['position_mult']:.2f}` · 单票槽位约 `{orders['per_core_notional']:,.0f}` 元（≤ {orders['max_single_weight']:.0%}）",
        "- 卖出仅当**你持仓命中** REDUCE/AVOID；未持有忽略。",
        "",
        "### 买入",
        "",
    ]
    buys = (orders.get("buy") or []) + (orders.get("satellite") or [])
    if not buys:
        lines.append("_无买入_")
        lines.append("")
    else:
        lines += [
            "| 方向 | 代码 | 名称 | 动作 | 建议金额(元) | 约手数 | 参考价 | 说明 |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
        for o in buys:
            px = o.get("ref_price")
            px_s = f"{px:.2f}" if isinstance(px, (int, float)) and px else "—"
            lines.append(
                f"| 买入 | {o.get('code')} | {o.get('name')} | {o.get('action')} | "
                f"{o.get('target_notional', 0):,.0f} | {o.get('approx_lots', 0)} | {px_s} | "
                f"{o.get('bucket')} · 开盘执行 |"
            )
        lines.append("")

    lines += ["### 卖出（仅持仓命中时）", ""]
    sells = orders.get("sell") or []
    if not sells:
        lines.append("_无减避提示_")
        lines.append("")
    else:
        lines += [
            "| 方向 | 代码 | 名称 | 动作 | 卖出比例 | 说明 |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
        for o in sells[:20]:
            lines.append(
                f"| 卖出 | {o.get('code')} | {o.get('name')} | {o.get('action')} | "
                f"{float(o.get('sell_frac', 0)):.0%} | {o.get('note')} |"
            )
        lines.append("")
    return "\n".join(lines)


def format_orders_console(orders: dict[str, Any] | None) -> str:
    if not orders:
        return ""
    lines = [
        f"=== 操作建议 ===",
        f"capital={orders['capital']:,.0f}  pos_mult={orders['position_mult']:.2f}  "
        f"单票槽位≈{orders['per_core_notional']:,.0f}",
        "",
        "【买入】",
    ]
    buys = (orders.get("buy") or []) + (orders.get("satellite") or [])
    if not buys:
        lines.append("  (无)")
    else:
        for o in buys:
            lines.append(
                f"  买入 {o.get('code')} {o.get('name')}  "
                f"约 {o.get('target_notional', 0):,.0f} 元 "
                f"({o.get('approx_lots', 0)} 手)  [{o.get('action')}/{o.get('bucket')}]"
            )
    lines += ["", "【卖出】仅持仓命中时"]
    sells = orders.get("sell") or []
    if not sells:
        lines.append("  (无)")
    else:
        for o in sells[:15]:
            lines.append(
                f"  卖出 {o.get('code')} {o.get('name')}  "
                f"{float(o.get('sell_frac', 0)):.0%}  [{o.get('action')}]  {o.get('note')}"
            )
        if len(sells) > 15:
            lines.append(f"  … 另有 {len(sells) - 15} 只，见 report")
    return "\n".join(lines)


def format_details_console(result: dict[str, Any]) -> str:
    """Strategy pick details after the action block."""
    meta = result.get("meta") or {}
    lines = [
        "=== 策略细节 ===",
        f"regime={meta.get('mkt_regime')}  universe={meta.get('universe_n')}  "
        f"open_A/B/C={meta.get('open_A_n', 0)}/{meta.get('open_B_n', 0)}/{meta.get('open_C_n', 0)}  "
        f"reduce/avoid={meta.get('reduce_n', 0)}/{meta.get('avoid_n', 0)}",
        "",
        "【BUY】",
    ]

    def _pick_line(r: dict[str, Any]) -> str:
        score = r.get("score")
        score_s = f"{score:.3f}" if isinstance(score, (int, float)) else "—"
        return (
            f"  {r.get('code')} {r.get('name')}  score={score_s}  "
            f"{r.get('action')}  {r.get('industry_name') or ''}  "
            f"{r.get('pattern_primary') or ''}"
        ).rstrip()

    buys = result.get("buy") or []
    if not buys:
        lines.append("  (无)")
    else:
        lines.extend(_pick_line(r) for r in buys)

    lines += ["", "【SATELLITE】"]
    sats = result.get("satellite") or []
    if not sats:
        lines.append("  (无)")
    else:
        lines.extend(_pick_line(r) for r in sats)

    lines += ["", "【REDUCE / AVOID】前 15"]
    reds = result.get("reduce") or []
    if not reds:
        lines.append("  (无)")
    else:
        for r in reds[:15]:
            lines.append(
                f"  {r.get('code')} {r.get('name')}  [{r.get('action')}]  "
                f"{r.get('industry_name') or ''}".rstrip()
            )
        if len(reds) > 15:
            lines.append(f"  … 共 {len(reds)} 只，见 report")
    return "\n".join(lines)
