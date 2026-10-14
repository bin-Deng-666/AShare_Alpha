"""Installed artifacts contain one application package and the public demo."""

from pathlib import Path
import tarfile
import zipfile

wheels = list(Path("dist").glob("*.whl"))
assert wheels, "Build the wheel first"
for wheel in wheels:
    with zipfile.ZipFile(wheel) as bundle:
        for name in bundle.namelist():
            parts = Path(name).parts
            assert parts[0] in {"ashare_alpha", "strategy"} or parts[0].endswith(
                ".dist-info"
            ), name
            if parts[0] == "strategy" and len(parts) > 1:
                assert parts[1] in {"demo", "__init__.py"}, name
            if name.startswith("ashare_alpha/web/strategies/"):
                assert parts[3] in {"demo", "__init__.py"}, name
            if name.startswith("ashare_alpha/cli/utils/adapters/"):
                assert Path(name).name in {"demo.py", "__init__.py"}, name
            assert Path(name).suffix not in {
                ".parquet",
                ".duckdb",
                ".pkl",
                ".pt",
                ".pth",
                ".lgb",
            }, name
            assert not set(parts) & {
                "artifacts",
                "workspace",
                "runs",
                "cache",
                "tests",
                ".local_research",
            }, name
    print(f"Distribution scope passed: {wheel.name}")

for source in Path("dist").glob("*.tar.gz"):
    with tarfile.open(source) as bundle:
        for name in bundle.getnames():
            parts = Path(name).parts[1:]
            assert not set(parts) & {
                "workspace",
                ".local_research",
                "node_modules",
                "runs",
                "cache",
            }, name
            if parts and parts[0] == "strategy" and len(parts) > 1:
                assert parts[1] in {"demo", "__init__.py", "README.md"}, name
            assert Path(name).suffix not in {
                ".parquet",
                ".duckdb",
                ".pkl",
                ".pt",
                ".pth",
                ".lgb",
            }, name
    print(f"Source distribution scope passed: {source.name}")
