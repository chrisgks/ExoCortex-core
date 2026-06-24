import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workers import context_hygiene, health_check, ingest_raw, reprocess_sessions, retrieve


def write(path: Path, content: str = "# Test\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class IntelligenceLoopBehaviorTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        for name in ("README.md", "AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md", "SKILLS.md"):
            write(root / name, "# Root\n\n## Current Focus\n\n- Test focus.\n")
        for name in ("README.md", "AGENT.md", "STATE.md", "DECISION RULES.md", "INTENDED BEHAVIORS.md"):
            write(root / "system" / name, "# System\n\n## Current Focus\n\n- Test focus.\n")
        write(root / "wiki" / "index.md", "# Root Wiki\n")
        write(root / "wiki" / "log.md", "# Log\n\n")
        write(root / "wiki-map.md", "# Wiki Map\n")
        return temp_dir, root

    def test_raw_ingest_dry_run_does_not_move_files(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)
        raw_file = root / "raw" / "inbox" / "capture.md"
        write(raw_file, "# Capture\n\nA useful capture.")

        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            exit_code = ingest_raw.main(["--root", str(root), "--limit", "1"])

        self.assertEqual(exit_code, 1)
        self.assertTrue(raw_file.exists())
        self.assertFalse((root / "raw" / "processed").exists())
        self.assertIn("capture.md -> wiki/05_sources/", stream.getvalue())

    def test_raw_ingest_apply_moves_files_and_writes_ledger(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)
        raw_file = root / "raw" / "inbox" / "capture.md"
        write(raw_file, "# Capture\n\nA useful capture.")

        exit_code = ingest_raw.main(["--root", str(root), "--limit", "1", "--apply"])

        self.assertEqual(exit_code, 0)
        self.assertFalse(raw_file.exists())
        self.assertTrue((root / "raw" / "processed").exists())
        self.assertTrue(any((root / "wiki" / "05_sources").glob("Source - * - capture.md")))
        logbook_lines = (root / "journal" / "logbook.jsonl").read_text(encoding="utf-8").splitlines()
        entry = json.loads(logbook_lines[-1])
        self.assertEqual(entry["authority"], "safe_apply")
        self.assertEqual(entry["command"], "exocortex-ingest --apply")

    def test_hygiene_reports_oversized_context_without_deleting_evidence(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)
        state = root / "STATE.md"
        original = "# State\n\n## Current Focus\n\n- Live.\n\n" + ("history\n" * 200)
        write(state, original)

        findings = context_hygiene.run_checks(root, active_limit=100, preload_limit=50, pending_limit=10, stale_days=30)

        self.assertIn("active_context_size", {finding.category for finding in findings})
        self.assertEqual(state.read_text(encoding="utf-8"), original)

    def test_reprocessing_timeout_stays_bounded(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)
        script = root / "tools" / "workers" / "process_session.py"
        write(script, "import time\ntime.sleep(5)\n")
        manifest = root / "journal" / "sessions" / "2026-04-29" / "s1.json"
        write(manifest, "{}\n")

        code = reprocess_sessions.process_manifest(root, manifest, timeout_seconds=1)

        self.assertEqual(code, 124)

    def test_retrieval_ignores_journals_by_default(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)
        write(root / "wiki" / "decision-space.md", "# Decision Space\n\nMap the option set.")
        write(root / "journal" / "raw" / "noisy.md", "# Noise\n\ndecision option decision option decision option")

        hits = retrieve.search(root, "decision option", limit=3)

        self.assertEqual(hits[0].path, "wiki/decision-space.md")
        self.assertFalse(any(hit.path.startswith("journal/") for hit in hits))

    def test_health_report_has_operational_sections(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)
        write(root / "journal" / "sessions" / "2026-04-29" / "s1.json", "{}\n")

        items = health_check.build_health(root)
        names = {item.name for item in items}

        self.assertIn("hygiene", names)
        self.assertIn("session_artifacts", names)
        self.assertIn("candidate_queue", names)
        self.assertIn("logbook", names)


if __name__ == "__main__":
    unittest.main()
