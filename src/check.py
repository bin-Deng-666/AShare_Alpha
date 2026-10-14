"""Run all maintained tests with an explicit and complete discovery scope."""

import argparse
import unittest
from .paths import CODE_ROOT

TEST_DIRS = ("tests",)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    suite = unittest.TestSuite()
    for directory in TEST_DIRS:
        path = CODE_ROOT / directory
        if not path.is_dir():
            parser.error(
                "Tests are available in the source checkout; install .[dev] and run from the checkout"
            )
        suite.addTests(
            unittest.TestLoader().discover(str(path), top_level_dir=str(CODE_ROOT))
        )
    return (
        0
        if unittest.TextTestRunner(verbosity=1 if args.quiet else 2)
        .run(suite)
        .wasSuccessful()
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
