"""Code resources and writable workspace paths are independent."""

import os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]


def workspace_root():
    explicit = os.environ.get("ASHARE_WORKSPACE")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if (CODE_ROOT / "pyproject.toml").is_file():
        return CODE_ROOT / "workspace"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "ashare-alpha"


WORKSPACE_ROOT = workspace_root()
DATA_ROOT = WORKSPACE_ROOT / "data"
ARTIFACT_ROOT = WORKSPACE_ROOT / "artifacts/strategies"
JOBS_ROOT = WORKSPACE_ROOT / "jobs"
