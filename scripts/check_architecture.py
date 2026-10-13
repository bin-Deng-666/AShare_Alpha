"""Check declared dependency directions without importing optional providers."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors = []
for directory, forbidden in (
    ("data/ashare_data", {"strategy", "cli", "webui"}),
    ("backtest", {"strategy", "cli", "webui"}),
):
    for path in (ROOT / directory).rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom) and node.level == 0
                else [item.name for item in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for name in names:
                if name.split(".")[0] in forbidden:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: forbidden dependency {name}"
                    )
if errors:
    raise SystemExit("\n".join(errors))
print("Architecture dependencies passed")
