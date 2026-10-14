"""Migration must preserve runtime assets and refuse conflicting destinations."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from scripts.migrate_workspace import main, migration_plan


class WorkspaceMigrationTests(unittest.TestCase):
    def test_plan_is_read_only_and_apply_preserves_bytes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "old"
            destination = root / "new"
            data = source / "data/parquet/sample"
            data.parent.mkdir(parents=True)
            data.write_bytes(b"frozen-market-evidence")
            run = source / "strategy/artifacts/demo/run.json"
            run.parent.mkdir(parents=True)
            run.write_text('{"run_id":"preserved"}')
            plan = migration_plan(source, destination)
            self.assertEqual(len(plan), 2)
            self.assertTrue(data.exists())
            self.assertFalse(destination.exists())
            main(
                ["--source", str(source), "--destination", str(destination), "--apply"]
            )
            self.assertEqual(
                (destination / "data/parquet/sample").read_bytes(),
                b"frozen-market-evidence",
            )
            self.assertEqual(
                (destination / "artifacts/strategies/demo/run.json").read_text(),
                '{"run_id":"preserved"}',
            )

    def test_source_code_and_existing_assets_are_never_overwritten(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "old"
            destination = root / "new"
            (source / "data").mkdir(parents=True)
            marker = source / "data/__main__.py"
            marker.write_text("# source")
            with self.assertRaises(ValueError):
                migration_plan(source, destination)
            marker.unlink()
            (destination / "data").mkdir(parents=True)
            with self.assertRaises(FileExistsError):
                migration_plan(source, destination)
