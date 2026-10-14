"""A committed index selects complete, immutable daily-plan revisions."""

from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from .storage import atomic_json, publication_lock
from .contracts import validate_plan

FORMAT_VERSION = 1


def read_index(root):
    path = Path(root) / "index.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("format_version") != FORMAT_VERSION:
        raise ValueError("Unsupported plan index format; upgrade AShare Alpha")
    return value


def report_directory(root, date):
    root = Path(root)
    index = read_index(root)
    if index is None:
        return root / date
    relative = index["plans"].get(date)
    if relative is None:
        return None
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Unsafe plan revision path")
    return path


def report_dates(root):
    root = Path(root)
    index = read_index(root)
    if index is not None:
        return sorted(index["plans"], reverse=True)
    return (
        sorted(
            (
                p.name
                for p in root.iterdir()
                if p.is_dir() and len(p.name) == 8 and p.name.isdigit()
            ),
            reverse=True,
        )
        if root.exists()
        else []
    )


def current_plan(root):
    root = Path(root)
    index = read_index(root)
    if index is None or index.get("current") is None:
        return None
    directory = report_directory(root, index["current"])
    return json.loads((directory / "result.json").read_text(encoding="utf-8"))


def publish_bundle(out_dir, result, writer, *, variant=None):
    """The index is the only commit point; failures preserve all prior readers."""
    validate_plan(result)
    out_dir = Path(out_dir)
    root, date = out_dir.parent, out_dir.name
    if len(date) != 8 or not date.isdigit():
        # Standalone exports retain the same transaction semantics with one named report.
        root, date = (
            out_dir,
            str(result["exec_date"] or result["asof"]).replace("-", ""),
        )
    result.setdefault("format_version", FORMAT_VERSION)
    # Different export sets are separate complete revisions of the same plan.
    identity = (
        result if variant is None else {"result": result, "export_variant": variant}
    )
    content = json.dumps(
        identity, sort_keys=True, ensure_ascii=False, allow_nan=False
    ).encode()
    revision_id = sha256(content).hexdigest()
    with publication_lock(root / ".publish.lock"):
        revision = root / ".revisions" / date / revision_id
        revision.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".pending-", dir=revision.parent) as temporary:
            files = writer(Path(temporary), result)
            if not revision.exists():
                os.rename(temporary, revision)
        index = read_index(root)
        if index is None:
            plans = {
                d: d for d in report_dates(root) if (root / d / "result.json").exists()
            }
        else:
            plans = dict(index["plans"])
        plans[date] = str(revision.relative_to(root))
        current = max(
            plans,
            key=lambda d: (
                json.loads((root / plans[d] / "result.json").read_text())["asof"],
                d,
            ),
        )
        atomic_json(
            root / "index.json",
            {"format_version": FORMAT_VERSION, "current": current, "plans": plans},
        )
    return {
        key: revision / path.name if key != "dir" else revision
        for key, path in files.items()
    }
