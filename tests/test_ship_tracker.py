#!/usr/bin/env python3
"""Tests for the Ship tracker + shape hook.

Contract pinned here:

- The Ship tracker is append-or-update structured data at ``journal/ship.jsonl``.
  Each item: id, title, status in {captured, shaped, shipped}, channel in
  {essay, post, code, oss, product, other}, income flag, created/updated
  timestamps, optional link.
- ``load_ship_items(root)`` is the read API the Allocator will call.
- ``exocortex-ship`` supports add / advance / set / list / shape.
- Durable changes (add, status flip) record to the Logbook (reversible).
- ``shape`` prepares/links a draft outline and emits a ready-to-paste prompt,
  then flips the item to ``shaped``.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.workers import ship_tracker, logbook, build_brief


class ShipDataShapeTests(unittest.TestCase):
    def test_path_is_ship_jsonl(self) -> None:
        self.assertEqual(str(ship_tracker.SHIP_PATH), "journal/ship.jsonl")

    def test_add_creates_captured_item_with_full_schema(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            item = ship_tracker.add_item(
                root, title="The Outsider's Ticket", channel="essay", income=True
            )
            for key in (
                "id",
                "title",
                "status",
                "channel",
                "income",
                "created",
                "updated",
                "link",
            ):
                self.assertIn(key, item)
            self.assertEqual(item["status"], "captured")
            self.assertEqual(item["channel"], "essay")
            self.assertTrue(item["income"])
            self.assertEqual(item["title"], "The Outsider's Ticket")

    def test_load_ship_items_reads_back(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.assertEqual(ship_tracker.load_ship_items(root), [])
            ship_tracker.add_item(root, title="A", channel="post")
            ship_tracker.add_item(root, title="B", channel="code", income=True)
            items = ship_tracker.load_ship_items(root)
            self.assertEqual(len(items), 2)
            titles = {i["title"] for i in items}
            self.assertEqual(titles, {"A", "B"})

    def test_invalid_channel_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with self.assertRaises(ValueError):
                ship_tracker.add_item(root, title="X", channel="nonsense")

    def test_default_channel_is_other(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            item = ship_tracker.add_item(root, title="X")
            self.assertEqual(item["channel"], "other")

    def test_ids_are_unique(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            a = ship_tracker.add_item(root, title="Same Title")
            b = ship_tracker.add_item(root, title="Same Title")
            self.assertNotEqual(a["id"], b["id"])


class ShipLifecycleTests(unittest.TestCase):
    def test_advance_walks_captured_shaped_shipped(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            item = ship_tracker.add_item(root, title="A")
            iid = item["id"]
            self.assertEqual(ship_tracker.advance(root, iid)["status"], "shaped")
            self.assertEqual(ship_tracker.advance(root, iid)["status"], "shipped")
            # Already shipped — advancing further is a no-op terminal.
            self.assertEqual(ship_tracker.advance(root, iid)["status"], "shipped")

    def test_set_status_directly(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            iid = ship_tracker.add_item(root, title="A")["id"]
            updated = ship_tracker.set_status(root, iid, "shipped")
            self.assertEqual(updated["status"], "shipped")

    def test_set_invalid_status_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            iid = ship_tracker.add_item(root, title="A")["id"]
            with self.assertRaises(ValueError):
                ship_tracker.set_status(root, iid, "bogus")

    def test_unknown_id_raises(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with self.assertRaises(KeyError):
                ship_tracker.set_status(root, "nope", "shaped")

    def test_update_bumps_updated_timestamp_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            item = ship_tracker.add_item(root, title="A")
            created = item["created"]
            after = ship_tracker.set_status(root, item["id"], "shaped")
            self.assertEqual(after["created"], created)


class ShipLogbookTests(unittest.TestCase):
    def test_add_records_to_logbook(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            ship_tracker.add_item(root, title="A")
            entries = (root / logbook.LOGBOOK_PATH).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(entries), 1)
            entry = json.loads(entries[0])
            self.assertEqual(entry["actor"], "ship.add")

    def test_status_flip_records_reversible_logbook_entry(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            iid = ship_tracker.add_item(root, title="A")["id"]
            ship_tracker.set_status(root, iid, "shipped")
            entries = [
                json.loads(line)
                for line in (root / logbook.LOGBOOK_PATH)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            flip = entries[-1]
            self.assertEqual(flip["actor"], "ship.set")
            # Reversal carries the prior status so the flip can be undone.
            self.assertEqual(flip["reversal"].get("prior_status"), "captured")
            self.assertEqual(flip["reversal"].get("item_id"), iid)


class ShapeHookTests(unittest.TestCase):
    def _seeds_dir(self, root: Path) -> Path:
        d = root / "domains" / "writing" / "projects" / "essays" / "seeds"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_shape_creates_seed_and_flips_to_shaped(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self._seeds_dir(root)
            item = ship_tracker.add_item(root, title="Calm Is a Balance Sheet", channel="essay")
            result = ship_tracker.shape(root, item["id"])
            # Status flipped.
            self.assertEqual(
                ship_tracker.get_item(root, item["id"])["status"], "shaped"
            )
            # A seed file was created and linked.
            seed_path = root / result["seed"]
            self.assertTrue(seed_path.exists())
            self.assertEqual(
                ship_tracker.get_item(root, item["id"])["link"], result["seed"]
            )
            # The shaping prompt is emitted and references the draft outline.
            self.assertIn("shape", result["prompt"].lower())
            self.assertIn(result["seed"], result["prompt"])

    def test_shape_links_existing_seed_when_given(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            seeds = self._seeds_dir(root)
            existing = seeds / "already-here.md"
            existing.write_text("# Already Here\n", encoding="utf-8")
            item = ship_tracker.add_item(root, title="Already Here", channel="essay")
            result = ship_tracker.shape(
                root, item["id"], seed=str(existing.relative_to(root))
            )
            # No new file created; it linked the existing one.
            self.assertEqual(result["seed"], str(existing.relative_to(root)))
            self.assertEqual(existing.read_text(encoding="utf-8"), "# Already Here\n")
            self.assertEqual(
                ship_tracker.get_item(root, item["id"])["status"], "shaped"
            )

    def test_shape_records_to_logbook(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self._seeds_dir(root)
            item = ship_tracker.add_item(root, title="A Thread", channel="essay")
            ship_tracker.shape(root, item["id"])
            actors = [
                json.loads(line)["actor"]
                for line in (root / logbook.LOGBOOK_PATH)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertIn("ship.shape", actors)


class ShipCliTests(unittest.TestCase):
    def _run(self, root: Path, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            code = ship_tracker.main(["--root", str(root), *argv])
        finally:
            sys.stdout = old
        return code, buf.getvalue()

    def test_add_then_list(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            code, _ = self._run(root, ["add", "Ship the loop", "--channel", "code", "--income"])
            self.assertEqual(code, 0)
            code, out = self._run(root, ["list"])
            self.assertEqual(code, 0)
            self.assertIn("Ship the loop", out)
            self.assertIn("captured", out)

    def test_advance_via_cli(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self._run(root, ["add", "Item One"])
            iid = ship_tracker.load_ship_items(root)[0]["id"]
            code, out = self._run(root, ["advance", iid])
            self.assertEqual(code, 0)
            self.assertEqual(ship_tracker.get_item(root, iid)["status"], "shaped")


class ShipBriefRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "STATE.md").write_text(
            "# Root State\n\n**Last updated:** 2026-04-29\n\n## Current Focus\n\n- ship\n",
            encoding="utf-8",
        )
        (self.root / "journal" / "sessions").mkdir(parents=True, exist_ok=True)
        (self.root / "raw" / "inbox").mkdir(parents=True, exist_ok=True)
        (self.root / "wiki").mkdir(parents=True, exist_ok=True)

    def test_brief_groups_items_by_status(self) -> None:
        ship_tracker.add_item(self.root, title="Captured Thing", channel="essay")
        iid = ship_tracker.add_item(self.root, title="Shipped Thing", channel="post")["id"]
        ship_tracker.set_status(self.root, iid, "shipped")
        brief = build_brief.render_brief(self.root)
        self.assertIn("Captured Thing", brief)
        self.assertIn("Shipped Thing", brief)
        self.assertIn("captured", brief.lower())
        self.assertIn("shipped", brief.lower())

    def test_brief_omits_tracker_gracefully_when_empty(self) -> None:
        brief = build_brief.render_brief(self.root)
        # No items -> no item titles, but the section still renders cleanly.
        self.assertIn("ship", brief.lower())


if __name__ == "__main__":
    unittest.main()
