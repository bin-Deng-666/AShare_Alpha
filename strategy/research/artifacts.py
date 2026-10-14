"""Content identities and atomic pointers for generated research artifacts."""

from hashlib import sha256
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


def write_json(path, value):
    atomic_json(path, json_value(value))
