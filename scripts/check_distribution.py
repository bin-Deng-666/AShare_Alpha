"""Installed artifacts must contain only public source and demo resources."""

from pathlib import Path
import zipfile

wheels = list(Path("dist").glob("*.whl"))
assert wheels, "Build the wheel first"
for wheel in wheels:
    with zipfile.ZipFile(wheel) as bundle:
        for name in bundle.namelist():
            parts = Path(name).parts
            if parts[0] == "strategy" and len(parts) > 1:
                assert parts[1] in {"demo", "research", "__init__.py", "__main__.py"}, (
                    name
                )
            if name.startswith("webui/app/strategies/"):
                assert parts[3] in {"demo", "__init__.py"}, name
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
                "runs",
                "cache",
                "tests",
                ".local_research",
            }, name
    print(f"Distribution scope passed: {wheel.name}")
