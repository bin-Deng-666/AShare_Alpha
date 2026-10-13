"""Read-only inventory of generated research data and logs."""

import argparse
import json
from .paths import DATA_ROOT, ARTIFACT_ROOT, JOBS_ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows = []
    for name, root in (
        ("data", DATA_ROOT),
        ("artifacts", ARTIFACT_ROOT),
        ("jobs", JOBS_ROOT),
    ):
        files = [p for p in root.rglob("*") if p.is_file()] if root.exists() else []
        rows.append(
            {
                "name": name,
                "path": str(root),
                "files": len(files),
                "bytes": sum(p.stat().st_size for p in files),
            }
        )
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(
                f"{row['name']:12} {row['files']:8} files {row['bytes'] / 2**20:12.2f} MiB {row['path']}"
            )
    return 0
