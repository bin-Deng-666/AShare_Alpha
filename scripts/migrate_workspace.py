"""Plan or move legacy workspace assets without overwriting existing destinations."""

import argparse
import fcntl
import json
from contextlib import ExitStack
from pathlib import Path


def migration_plan(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    pairs = [
        (source / "data", destination / "data"),
        (source / "strategy/artifacts", destination / "artifacts/strategies"),
        (source / "webui/jobs", destination / "jobs"),
        (source / "webui/server.json", destination / "server.json"),
    ]
    plan = []
    for old, new in pairs:
        if old == new or not old.exists():
            continue
        if old.name == "data" and any(
            (old / name).exists() for name in ("ashare_data", "__main__.py")
        ):
            raise ValueError(
                "The data directory still contains source; migrate source layout first"
            )
        if old in new.parents:
            raise ValueError("Cannot move a directory into itself")
        if new.exists():
            raise FileExistsError(new)
        plan.append((old, new))
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Old workspace root")
    parser.add_argument(
        "--destination", required=True, type=Path, help="New workspace root"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    plan = migration_plan(args.source, args.destination)
    print(json.dumps([{"from": str(a), "to": str(b)} for a, b in plan], indent=2))
    if not args.apply:
        return 0
    if (args.source / "webui/server.json").exists():
        raise RuntimeError(
            "Stop the recorded WebUI server before migrating its workspace"
        )
    with ExitStack() as stack:
        for root in (args.source, args.destination):
            lock = root / "data/state/data_update.lock"
            if lock.exists():
                stream = stack.enter_context(lock.open("rb"))
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        moved = []
        try:
            for old, new in plan:
                new.parent.mkdir(parents=True, exist_ok=True)
                old.rename(new)
                moved.append((old, new))
        except Exception:
            for old, new in reversed(moved):
                old.parent.mkdir(parents=True, exist_ok=True)
                new.rename(old)
            raise
    print(
        "Moved assets. Set ASHARE_WORKSPACE to the destination and run ashare-alpha data catalog."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
