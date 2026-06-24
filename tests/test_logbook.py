import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workers import logbook, review


def write(path: Path, content: str = "# Test\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class LogbookApiTests(unittest.TestCase):
    def test_record_change_appends_reversible_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            logbook.record_change(
                root,
                actor="review.accept",
                action="append",
                path=root / "MEMORY.md",
                summary="promoted a candidate",
                reversal={"appended_text": "- a new memory\n"},
            )
            path = root / logbook.LOGBOOK_PATH
            self.assertTrue(path.exists())
            entry = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(entry["actor"], "review.accept")
            self.assertEqual(entry["action"], "append")
            self.assertEqual(entry["path"], "MEMORY.md")
            self.assertIn("timestamp", entry)
            self.assertEqual(entry["reversal"]["appended_text"], "- a new memory\n")

    def test_append_only(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            logbook.record_change(root, actor="a", action="x", path="A", summary="1")
            logbook.record_change(root, actor="b", action="y", path="B", summary="2")
            lines = (root / logbook.LOGBOOK_PATH).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)

    def test_path_is_logbook_not_ledger(self) -> None:
        self.assertEqual(str(logbook.LOGBOOK_PATH), "journal/logbook.jsonl")


class ReviewAcceptLogbookTests(unittest.TestCase):
    def _make_candidate_root(self) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        write(root / "MEMORY.md", "# Memory\n")
        manifest_dir = root / "journal" / "sessions" / "2026-06-21"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        candidate = {
            "candidate_type": "memory",
            "text": "A durable insight worth keeping",
            "normalized_key": "a durable insight worth keeping",
            "suggested_destination": "MEMORY.md",
            "artifact_kind": "memory_note",
            "why_it_matters": "matters",
            "justification": "matters",
            "confidence": "high",
            "signal_ladder": "candidate",
            "first_seen": "2026-06-21T10:00:00+00:00",
            "last_seen": "2026-06-21T10:00:00+00:00",
            "source_session_ids": ["sess-1"],
            "domain": None,
            "project": None,
            "self_model_layer": None,
            "tier": "queue",
            "contradicts": [],
            "related_focus": [],
        }
        (manifest_dir / "sess-1.candidates.json").write_text(
            json.dumps({"candidate_records": [candidate]}), encoding="utf-8"
        )
        return root

    def test_accept_records_to_logbook_with_reversal(self) -> None:
        root = self._make_candidate_root()
        before = (root / "MEMORY.md").read_text(encoding="utf-8")
        review.apply_action(
            root,
            review.choose_record(review.pending_records(root), "durable insight"),
            "accepted",
            None,
        )
        path = root / logbook.LOGBOOK_PATH
        self.assertTrue(path.exists())
        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        accept_entries = [e for e in entries if e.get("actor") == "review.accept"]
        self.assertEqual(len(accept_entries), 1)
        entry = accept_entries[0]
        self.assertEqual(entry["path"], "MEMORY.md")
        # Reversal is sufficient to undo: restore the prior content.
        prior = entry["reversal"]["prior_content"]
        self.assertEqual(prior, before)
        after = (root / "MEMORY.md").read_text(encoding="utf-8")
        self.assertNotEqual(after, before)
        (root / "MEMORY.md").write_text(prior, encoding="utf-8")
        self.assertEqual((root / "MEMORY.md").read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
