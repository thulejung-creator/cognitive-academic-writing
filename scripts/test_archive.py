"""Test project persistence boundaries with fictional workspace-local fixtures."""
import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        # All recursive fixture cleanup is limited to this verified task directory.
        base = Path.cwd().resolve() / "work" / "archive-test-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="case-", dir=base)
        self.root = Path(self.temporary.name).resolve()
        assert self.root.is_relative_to(base.resolve()) and self.root != base.resolve()
        self.addCleanup(self.temporary.cleanup)
        self.plan = json.loads((Path(__file__).parents[1] / "assets/project-template.json").read_text(encoding="utf-8"))
        self.input = self.root / "project-plan.json"
        self.save()
        self.folder = self.root / ".project-archive"

    def save(self):
        self.input.write_text(json.dumps(self.plan, ensure_ascii=False), encoding="utf-8")

    def sync(self):
        self.save()
        with patch.object(archive, "read_git", return_value={"available": False, "reason": "Fictional test fixture"}):
            return archive.sync(self.root, self.input, allow_project_archive=True)

    def query(self, statement):
        with closing(sqlite3.connect(self.folder / "archive.sqlite3")) as connection:
            return connection.execute(statement).fetchall()

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob("*") if p.is_file()}

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(Path(archive.__file__).resolve()),
                               *args, "--root", str(self.root)], capture_output=True, check=False)

    def test_validate_does_not_create_or_modify_files(self):
        before = self.snapshot()
        archive.validate(copy.deepcopy(self.plan), self.root)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.folder.exists())

    def test_library_write_requires_explicit_permission(self):
        with self.assertRaises(ValueError):
            archive.sync(self.root, self.input)
        with self.assertRaises(ValueError):
            archive.export(self.root)
        self.assertFalse(self.folder.exists())

    def test_cli_write_without_flag_creates_nothing(self):
        before = self.snapshot()
        result = self.cli("sync", "--input", str(self.input))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.folder.exists())

    def test_sync_creates_valid_database_and_exports(self):
        self.sync()
        self.assertEqual({p.name for p in self.folder.iterdir()}, {"archive.sqlite3", "project.json", "PROJECT.md"})
        self.assertEqual(self.query("PRAGMA integrity_check"), [("ok",)])
        self.assertEqual(self.query("PRAGMA user_version"), [(1,)])
        self.assertEqual(self.query("PRAGMA foreign_key_check"), [])
        self.assertIn("Working agreement v1 (draft)", (self.folder / "PROJECT.md").read_text(encoding="utf-8"))

    def test_repeated_sync_deduplicates_revisions(self):
        self.sync()
        self.sync()
        self.assertEqual(self.query("SELECT count(*) FROM revisions"), [(1,)])
        self.assertEqual(self.query("SELECT count(*) FROM contracts"), [(1,)])
        self.assertEqual(self.query("SELECT count(*) FROM git_snapshots"), [(1,)])

    def test_show_is_read_only(self):
        self.sync()
        before = self.snapshot()
        result = self.cli("show")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn("Task request", result.stdout.decode("utf-8"))
        self.assertEqual(before, self.snapshot())

    def test_show_missing_archive_creates_nothing(self):
        result = self.cli("show")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.folder.exists())

    def test_export_permission_and_recovery(self):
        self.sync()
        overview = self.folder / "PROJECT.md"
        overview.unlink()
        with self.assertRaises(ValueError):
            archive.export(self.root)
        self.assertFalse(overview.exists())
        archive.export(self.root, allow_project_archive=True)
        self.assertTrue(overview.is_file())

    def test_contract_change_requires_version_increment(self):
        self.sync()
        self.plan["contract"]["goal"] = "A revised authorized scope"
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual(self.query("SELECT count(*) FROM contracts"), [(1,)])
        self.plan["contract"]["version"] = 2
        self.sync()
        self.assertEqual(self.query("SELECT count(*) FROM contracts"), [(2,)])
        self.plan["contract"]["version"] = 1
        with self.assertRaises(ValueError):
            self.sync()

    def test_agreement_needs_actual_evidence_field(self):
        self.plan["contract"]["status"] = "agreed"
        with self.assertRaises(ValueError):
            self.sync()
        self.assertFalse(self.folder.exists())

    def test_cycles_missing_and_unfinished_dependencies_rejected(self):
        original = copy.deepcopy(self.plan)
        for kind in ("cycle", "missing", "unfinished", "self"):
            self.plan = copy.deepcopy(original)
            if kind == "cycle":
                self.plan["units"][0]["depends_on"] = ["U002"]
            elif kind == "missing":
                self.plan["units"][1]["depends_on"] = ["UNKNOWN"]
            elif kind == "unfinished":
                self.plan["units"][1]["status"] = "ready"
            else:
                self.plan["units"][0]["depends_on"] = ["U001"]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.sync()
            self.assertFalse(self.folder.exists())

    def test_done_and_blocked_require_evidence_or_reason(self):
        for state in ("done", "blocked"):
            self.plan["units"][0]["status"] = state
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.sync()
        self.assertFalse(self.folder.exists())

    def test_ready_dependencies_and_new_revision(self):
        self.sync()
        self.plan["units"][0].update(status="done", evidence=["Inspected fictional evidence map"])
        self.plan["units"][1]["status"] = "ready"
        self.sync()
        self.assertEqual(self.query("SELECT count(*) FROM revisions"), [(2,)])

    def test_private_and_unknown_fields_rejected(self):
        original = copy.deepcopy(self.plan)
        for location, key in (("top", "cognitive_model"), ("project", "private_notes"),
                              ("contract", "collaboration_history"), ("unit", "unrecognized")):
            self.plan = copy.deepcopy(original)
            target = self.plan if location == "top" else self.plan["units"][0] if location == "unit" else self.plan[location]
            target[key] = "PRIVATE_CANARY"
            with self.subTest(location=location), self.assertRaises(ValueError):
                self.sync()
        self.assertFalse(self.folder.exists())

    def test_artifact_paths_cannot_escape_or_index_archive(self):
        for path in ("../outside.txt", "C:\\outside.txt", ".project-archive/archive.sqlite3", "file:stream"):
            self.plan["artifacts"] = [{"path": path, "kind": "text", "description": "Fictional artifact"}]
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.sync()
        self.assertFalse(self.folder.exists())

    def test_artifact_checksum_and_missing_state(self):
        artifact = self.root / "manuscript.txt"
        artifact.write_text("Fictional draft", encoding="utf-8")
        self.plan["artifacts"] = [
            {"path": "manuscript.txt", "kind": "text", "description": "Fictional draft"},
            {"path": "missing.txt", "kind": "text", "description": "Not yet produced"},
        ]
        self.sync()
        items = json.loads((self.folder / "project.json").read_text(encoding="utf-8"))["artifacts"]
        self.assertEqual(items[0]["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())
        self.assertFalse(items[1]["exists"])
        self.assertIsNone(items[1]["sha256"])

    def test_local_skill_checksums_without_fake_github_metadata(self):
        local = self.root / ".agents" / "skills" / "fixture"
        local.mkdir(parents=True)
        entry = local / "SKILL.md"
        entry.write_text("Fictional local test skill", encoding="utf-8")
        self.plan["skills"] = [{
            "id": "S1", "name": "fixture", "status": "installed", "source_kind": "local",
            "local_path": ".agents/skills/fixture", "content_sha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
            "workflow": "Use this fictional fixture", "review": "Reviewed test data", "unit_ids": ["U001"],
        }]
        self.sync()
        entry.write_text("Changed fixture", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.sync()

    def test_project_identity_cannot_be_overwritten(self):
        self.sync()
        self.plan["project"]["id"] = "another-project"
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual(self.query("SELECT id FROM projects"), [("replace-with-stable-project-id",)])

    def test_duplicate_json_keys_and_nonfinite_values_rejected(self):
        for content in ('{"project":{},"project":{}}', '{"project":NaN}'):
            self.input.write_text(content, encoding="utf-8")
            result = self.cli("validate", "--input", str(self.input))
            self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.folder.exists())

    def test_utf8_cli_and_archive_overview(self):
        self.plan["project"]["name"] = "Academic writing: caf\u00e9 observations"
        self.sync()
        result = self.cli("show")
        self.assertEqual(result.returncode, 0)
        self.assertIn(self.plan["project"]["name"], result.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
