"""Console and Markdown rendering of model-account orders."""


def format_orders_markdown(orders):
    if not orders:
        return ""
    rows = []
    for order in orders.get("buy", []):
        rows.append(
            f"| BUY | {order['code']} | {order.get('budget', 0):,.2f} 含费预算 |"
        )
    for order in orders.get("sell", []):
        rows.append(f"| SELL | {order['code']} | {order.get('quantity', 0)} 股 |")
    return (
        "\n".join(
            [
                "## 模型账户待执行订单",
                "",
                "| 方向 | 标的 | 请求 |",
                "|---|---|---|",
                *rows,
            ]
        )
        if rows
        else "## 模型账户待执行订单\n\n无待执行订单。"
    )


def format_orders_console(orders):
    if not orders:
        return "无待执行订单。"
    lines = [
        f"BUY  {row['code']} 含费预算 {row.get('budget', 0):,.2f}"
        for row in orders.get("buy", [])
    ]
    lines.extend(
        f"SELL {row['code']} 请求 {row.get('quantity', 0)} 股"
        for row in orders.get("sell", [])
    )
    return "\n".join(lines) if lines else "无待执行订单。"


def format_details_console(result):
    positions = result.get("positions", [])
    return "\n".join(
        [
            f"模型持仓：{len(positions)} 个标的",
            *[f"{row['code']} {row['shares']} 股" for row in positions],
        ]
    )
