"""Ensure the published tree contains source and the single allowed demo."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
paths = (
    subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
)
for name in filter(None, paths):
    path = Path(name)
    parts = path.parts
    if parts[0] == "strategy" and len(parts) > 1:
        assert parts[1] in {
            "demo",
            "research",
            "README.md",
            "ARCHITECTURE.md",
            "__init__.py",
            "__main__.py",
        }, name
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
        "artifacts",
        "node_modules",
        ".local_research",
        "runs",
        "cache",
    }, name
    assert not path.is_file() or path.stat().st_size <= 1024 * 1024, name
print("Publication scope passed: source only and one demo")
