"""One installed entry point dispatches to the existing public domain commands."""

import argparse
import sys
from importlib import import_module
from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(description="AShare Alpha research workspace")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "command",
        choices=["data", "strategy", "plan", "web", "check", "verify", "demo", "cache"],
    )
    argv = sys.argv[1:] if argv is None else list(argv)
    targets = {
        "data": "data.__main__",
        "strategy": "strategy.__main__",
        "plan": "cli.run",
        "web": "webui.server",
        "check": "ashare_alpha.check",
        "verify": "ashare_alpha.verify",
        "demo": "ashare_alpha.demo",
        "cache": "ashare_alpha.cache",
    }
    if argv and argv[0] in targets:
        module = import_module(targets[argv[0]])
        return module.main(argv[1:])
    parser.parse_args(argv)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
