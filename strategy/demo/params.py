"""Parameters for the one-instrument buy-and-hold example."""

from dataclasses import dataclass, asdict
import re


@dataclass(frozen=True)
class Params:
    code: str = "000001"

    def __post_init__(self):
        if not isinstance(self.code, str) or not re.fullmatch(r"\d{6}", self.code):
            raise ValueError("code must be a six-digit security code")

    @classmethod
    def parse(cls, value=None):
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        if not isinstance(value, dict) or set(value) - {"code"}:
            raise ValueError("demo parameters only accept code")
        return cls(**value)

    def as_dict(self):
        return asdict(self)
