#!/usr/bin/env python3
"""Tests for the Brief — the single startup surface.

The Brief is assembled from signals that already exist (health_check,
context_hygiene, review, the period syntheses, surface-now). These tests pin
the contract: the sections it must contain, that it is idempotent and writes
only the Brief file, and that the wrapper preloads it at session start.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.workers import build_brief


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def minimal_repo(root: Path) -> None:
    """A tiny ExoCortex-shaped tree the Brief can read without crashing."""
    write(root / "STATE.md", "# Root State\n\n**Last updated:** 2026-04-29\n\n## Current Focus\n\n- ship the loop\n")
    write(root / "journal" / "inbox" / "surface-now.md", "# Surface Now\n")
    (root / "journal" / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "inbox").mkdir(parents=True, exist_ok=True)
    (root / "wiki").mkdir(parents=True, exist_ok=True)


class BuildBriefSectionsTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        minimal_repo(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_brief_has_all_required_sections(self) -> None:
        text = build_brief.render_brief(self.root)
        # The five surfaces, plus the periodic-synthesis pointer.
        self.assertIn("# ExoCortex Brief", text)
        self.assertIn("What changed", text)
        self.assertIn("What's stale", text)
        self.assertIn("What's queued for your decision", text)
        self.assertIn("Next best moves", text)
        self.assertIn("What's ready to ship", text)

    def test_next_best_moves_come_from_the_allocator(self) -> None:
        text = build_brief.render_brief(self.root)
        # The Allocator now drives Next best moves; the placeholder TODO is gone.
        self.assertIn("Allocator", text)
        self.assertNotIn("TODO", text)
        self.assertNotIn("Lightweight heuristic", text)
        # Allocator output carries a one-line why per move.
        self.assertIn("why:", text)

    def test_ship_tracker_section_renders(self) -> None:
        # The Ship tracker is real now. With no items, the section still
        # renders and points at how to add one — it never crashes or vanishes.
        text = build_brief.render_brief(self.root)
        lowered = text.lower()
        self.assertIn("ship tracker", lowered)
        self.assertIn("exocortex-ship", lowered)

    def test_never_uses_the_banned_word(self) -> None:
        text = build_brief.render_brief(self.root).lower()
        self.assertNotIn("ledger", text)

    def test_word_brief_appears(self) -> None:
        self.assertIn("Brief", build_brief.render_brief(self.root))


class BuildBriefWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        minimal_repo(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_creates_brief_file(self) -> None:
        out = build_brief.write_brief(self.root)
        self.assertEqual(out, self.root.resolve() / "journal" / "inbox" / "brief.md")
        self.assertTrue(out.exists())
        self.assertIn("# ExoCortex Brief", out.read_text(encoding="utf-8"))

    def test_write_is_atomic_no_partial_reads(self) -> None:
        # A new session's SessionStart hook may read brief.md exactly while a
        # closing session rewrites it. The write must be atomic (temp + replace)
        # so a concurrent reader sees either the old or the new file in full,
        # never an empty/truncated one. We assert no stray temp file lingers and
        # the published file is always complete.
        import os

        build_brief.write_brief(self.root)
        path = self.root.resolve() / "journal" / "inbox" / "brief.md"
        for _ in range(8):
            build_brief.write_brief(self.root)
            body = path.read_text(encoding="utf-8")
            self.assertTrue(body.strip(), "brief.md was observed empty mid-write")
            self.assertIn("# ExoCortex Brief", body)
        # No leftover temp artifact from the atomic swap.
        leftovers = [p.name for p in path.parent.iterdir() if p.name.startswith(".brief")]
        self.assertEqual(leftovers, [], f"atomic-write temp left behind: {leftovers}")

    def test_idempotent_only_writes_the_brief(self) -> None:
        # Snapshot every file except the Brief, regenerate twice, assert nothing
        # else changed and the Brief itself is stable across runs.
        def snapshot() -> dict[str, bytes]:
            return {
                str(p): p.read_bytes()
                for p in self.root.rglob("*")
                if p.is_file() and p.name != "brief.md"
            }

        before = snapshot()
        first = build_brief.write_brief(self.root).read_text(encoding="utf-8")
        after_one = snapshot()
        second = build_brief.write_brief(self.root).read_text(encoding="utf-8")
        after_two = snapshot()

        self.assertEqual(before, after_one, "first build mutated durable state")
        self.assertEqual(after_one, after_two, "second build mutated durable state")
        # Brief body (minus the generated-at line) must be identical run to run.
        self.assertEqual(
            build_brief.strip_volatile(first),
            build_brief.strip_volatile(second),
        )


class BuildBriefSignalsTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        minimal_repo(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_latest_period_pointer_surfaces(self) -> None:
        write(self.root / "journal" / "monthly" / "2026-06.md", "# Month Synthesis - 2026-06\n")
        write(self.root / "journal" / "weekly" / "2026-W25.md", "# Week Synthesis - 2026-W25\n")
        text = build_brief.render_brief(self.root)
        # Should point at the most recent period synthesis, not leave it orphaned.
        self.assertIn("2026-W25", text)

    def test_no_period_synthesis_does_not_crash(self) -> None:
        text = build_brief.render_brief(self.root)
        self.assertIn("# ExoCortex Brief", text)

    def test_surface_now_items_feed_queued_section(self) -> None:
        write(
            self.root / "journal" / "inbox" / "surface-now.md",
            "# Surface Now\n\n## 2026-06-20T00:00:00+00:00 surface-now\n\n"
            "- session_id: `abc`\n\n### A concrete decision waiting on you\n\n"
            "- candidate_type: `memory`\n- confidence: `high`\n",
        )
        text = build_brief.render_brief(self.root)
        self.assertIn("A concrete decision waiting on you", text)


class WrapperPreloadTest(unittest.TestCase):
    """The Brief must load at session start the same way surface-now does."""

    def test_brief_is_a_preload_candidate(self) -> None:
        import tempfile

        sys.path.insert(0, str(REPO_ROOT / "tools" / "wrappers"))
        import exocortex_wrapper as w

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            minimal_repo(root)
            write(root / "journal" / "inbox" / "brief.md", "# ExoCortex Brief\n\ncontent\n")
            context = w.collect_context(root, root, "chief-of-staff", "conversation")
            candidates = w.authoritative_preload_candidates(context)
            self.assertIn("journal/inbox/brief.md", candidates)

    def test_brief_skipped_when_empty(self) -> None:
        import tempfile

        sys.path.insert(0, str(REPO_ROOT / "tools" / "wrappers"))
        import exocortex_wrapper as w

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            minimal_repo(root)
            # No brief.md written at all.
            context = w.collect_context(root, root, "chief-of-staff", "conversation")
            candidates = w.authoritative_preload_candidates(context)
            self.assertNotIn("journal/inbox/brief.md", candidates)


class StartupDigestResilienceTest(unittest.TestCase):
    """The SessionStart digest must never silently vanish when brief.md is
    missing/empty/partial — the hook renders the brief live instead so the user
    always sees the ExoCortex brief at session open (not just other tools)."""

    def _hook_module(self):
        import importlib.util

        sys.path.insert(0, str(REPO_ROOT / "tools" / "wrappers"))
        sys.path.insert(0, str(REPO_ROOT))
        spec = importlib.util.spec_from_file_location(
            "hook_context", REPO_ROOT / "tools" / "wrappers" / "hook-context.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_missing_brief_file_falls_back_to_live_render(self) -> None:
        import tempfile

        hc = self._hook_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            minimal_repo(root)
            # No brief.md on disk at all.
            self.assertFalse((root / "journal" / "inbox" / "brief.md").exists())
            text = hc._load_brief_text(root)
            self.assertTrue(text and "ExoCortex Brief" in text)
            digest = hc.render_brief_digest(root)
            self.assertTrue(digest and "ExoCortex Brief" in digest)

    def test_empty_brief_file_falls_back_to_live_render(self) -> None:
        import tempfile

        hc = self._hook_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            minimal_repo(root)
            # Simulate a mid-rewrite empty read.
            write(root / "journal" / "inbox" / "brief.md", "")
            digest = hc.render_brief_digest(root)
            self.assertTrue(digest and "ExoCortex Brief" in digest)


if __name__ == "__main__":
    unittest.main()
