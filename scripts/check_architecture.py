"""Check dependency directions in the application package without importing it."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors = []
for directory, forbidden in (
    (
        "src/data",
        {
            "strategy",
            "ashare_alpha.backtest",
            "ashare_alpha.cli",
            "ashare_alpha.web",
            "ashare_alpha.research",
        },
    ),
    (
        "src/backtest",
        {"strategy", "ashare_alpha.cli", "ashare_alpha.web", "ashare_alpha.research"},
    ),
):
    base = ROOT / directory
    if not base.is_dir():
        raise SystemExit(f"Missing source directory: {directory}")
    for path in base.rglob("*.py"):
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
                if any(
                    name == prefix or name.startswith(prefix + ".")
                    for prefix in forbidden
                ):
                    errors.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: forbidden dependency {name}"
                    )
if errors:
    raise SystemExit("\n".join(errors))
print("Architecture dependencies passed")
