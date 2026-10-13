"""Content identities and atomic pointers for generated research artifacts."""

from hashlib import sha256
from pathlib import Path
import json
from backtest.results import json_value

from ashare_alpha.paths import ARTIFACT_ROOT
from ashare_alpha.storage import atomic_json

ROOT = ARTIFACT_ROOT


def identity(value):
    return sha256(
        json.dumps(
            json_value(value), sort_keys=True, ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def code_identity(files):
    return {
        str(Path(p).relative_to(Path(__file__).resolve().parents[2])): sha256(
            Path(p).read_bytes()
        ).hexdigest()
        for p in files
    }


def write_json(path, value):
    atomic_json(path, json_value(value))
