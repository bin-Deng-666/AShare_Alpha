"""Inspect both tracked and newly added public files; exclude local workspace assets."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
paths = (
    subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
    )
    .decode()
    .split("\0")
)
for name in filter(None, paths):
    path = Path(name)
    if not (ROOT / path).is_file():
        continue  # A tracked path removed by the current refactor.
    parts = path.parts
    if parts[0] == "strategy" and len(parts) > 1:
        assert parts[1] in {"demo", "README.md", "__init__.py"}, name
    if name.startswith("src/web/strategies/"):
        assert parts[3] in {"demo", "__init__.py"}, name
    if name.startswith("src/cli/utils/adapters/"):
        assert path.name in {"demo.py", "__init__.py"}, name
    assert path.suffix not in {
        ".parquet",
        ".duckdb",
        ".pkl",
        ".pt",
        ".pth",
        ".lgb",
        ".joblib",
    }, name
    assert not set(parts) & {
        "workspace",
        "artifacts",
        "node_modules",
        ".local_research",
        "runs",
        "cache",
    }, name
    assert (ROOT / path).stat().st_size <= 1024 * 1024, name
print("Publication scope passed: source only and one demo")
