#!/usr/bin/env python3
"""Tests for the Allocator — the next-best-move engine.

Contract pinned here:

- The Allocator is **read + propose only** (observe). It writes only its own
  suggestions log (``journal/allocations.jsonl``); it never takes actions, never
  promotes, never mutates any other file.
- Scoring is a **transparent weighted sum over a named, fixed feature set**.
  The feature schema + weights live in one swappable place; ``score(features)``
  is isolated from the gathering code so a learned/federated policy can replace
  it later without re-architecting.
- ``propose(root)`` returns the top 1-3 candidate moves, each with: a stable id,
  a one-line *why*, the full named feature vector, the per-feature score
  breakdown, and the total score.
- Every ``propose`` run appends one record per suggestion to
  ``journal/allocations.jsonl``: timestamp, id, feature vector, score breakdown,
  proposed move, plus blank ``taken`` / ``reward`` slots — joinable by id with
  ``reward-log.jsonl`` and ``review-decisions.jsonl``.
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

from tools.workers import allocator, ship_tracker, reward_log, build_brief


def _fresh_root(tmp: str) -> Path:
    root = Path(tmp)
    (root / "journal" / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "journal" / "inbox").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "inbox").mkdir(parents=True, exist_ok=True)
    (root / "wiki").mkdir(parents=True, exist_ok=True)
    # A clean, current STATE so default findings are quiet.
    (root / "STATE.md").write_text(
        "# Root State\n\n**Last updated:** 2026-06-21\n\n## Current Focus\n\n- ship the loop\n",
        encoding="utf-8",
    )
    (root / "wiki-map.md").write_text("# Wiki Map\n", encoding="utf-8")
    return root


class FeatureSchemaTests(unittest.TestCase):
    def test_feature_schema_is_named_fixed_and_stable(self) -> None:
        # The named feature set is the schema a learned policy will train against.
        self.assertEqual(
            list(allocator.FEATURE_SCHEMA),
            [
                "staleness",
                "deadline_pressure",
                "energy_match",
                "shippable_unfinished",
                "surfaced_priority",
                "queue_pressure",
            ],
        )

    def test_weights_cover_exactly_the_schema(self) -> None:
        self.assertEqual(set(allocator.WEIGHTS), set(allocator.FEATURE_SCHEMA))

    def test_score_is_weighted_sum_over_features(self) -> None:
        # score() is the swappable policy seam: pure function of the feature vector.
        features = {name: 0.0 for name in allocator.FEATURE_SCHEMA}
        self.assertEqual(allocator.score(features), 0.0)
        features = dict.fromkeys(allocator.FEATURE_SCHEMA, 1.0)
        expected = sum(allocator.WEIGHTS.values())
        self.assertAlmostEqual(allocator.score(features), expected)

    def test_score_ignores_unknown_keys_and_tolerates_missing(self) -> None:
        # Forward/backward compatible: a learned policy or older log won't crash it.
        self.assertEqual(allocator.score({"bogus": 99.0}), 0.0)
        partial = {"staleness": 1.0}
        self.assertAlmostEqual(
            allocator.score(partial), allocator.WEIGHTS["staleness"]
        )


class ProposeTests(unittest.TestCase):
    def test_returns_one_to_three_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _fresh_root(tmp)
            moves = allocator.propose(root)
            self.assertGreaterEqual(len(moves), 1)
            self.assertLessEqual(len(moves), 3)

    def test_each_move_has_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _fresh_root(tmp)
            moves = allocator.propose(root)
            for move in moves:
                for key in ("id", "move", "why", "features", "breakdown", "score"):
                    self.assertIn(key, move)
                self.assertEqual(set(move["features"]), set(allocator.FEATURE_SCHEMA))
                self.assertEqual(set(move["breakdown"]), set(allocator.FEATURE_SCHEMA))
                self.assertIsInstance(move["why"], str)
                self.assertTrue(move["why"].strip())

    def test_moves_sorted_by_score_descending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _fresh_root(tmp)
            # Make a stale captured ship item so a shippable move scores high.
            ship_tracker.add_item(root, title="Old Thread", channel="essay")
            items = ship_tracker.load_ship_items(root)
            items[0]["created"] = "2026-01-01T00:00:00+00:00"
            items[0]["updated"] = "2026-01-01T00:00:00+00:00"
            ship_tracker._write_items(root, items)
            moves = allocator.propose(root)
            scores = [m["score"] for m in moves]
            self.assertEqual(scores, sorted(scores, reverse=True))

    def test_stale_ship_item_yields_finish_and_ship_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _fresh_root(tmp)
            ship_tracker.add_item(root, title="Half Finished Essay", channel="essay")
            items = ship_tracker.load_ship_items(root)
            items[0]["created"] = "2026-01-01T00:00:00+00:00"
            items[0]["updated"] = "2026-01-01T00:00:00+00:00"
            ship_tracker._write_items(root, items)
            moves = allocator.propose(root)
            blob = " ".join(m["move"].lower() + " " + m["why"].lower() for m in moves)
            self.assertIn("ship", blob)
            self.assertTrue(any(m["features"]["shippable_unfinished"] > 0 for m in moves))


class LoggingTests(unittest.TestCase):
    def test_propose_appends_one_record_per_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _fresh_root(tmp)
            moves = allocator.propose(root, log=True)
            path = root / allocator.ALLOCATIONS_PATH
            self.assertTrue(path.exists())
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(rows), len(moves))

    def test_logged_record_is_join_ready_training_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _fresh_root(tmp)
            allocator.propose(root, log=True)
            row = json.loads(
                (root / allocator.ALLOCATIONS_PATH)
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            for key in (
                "timestamp",
                "id",
                "features",
                "breakdown",
                "move",
                "why",
                "score",
                "taken",
                "reward",
            ):
                self.assertIn(key, row)
            # Blank reward slots so a later join can fill them in.
            self.assertIsNone(row["taken"])
            self.assertIsNone(row["reward"])
            # The feature vector is the full stable schema (training-ready).
            self.assertEqual(set(row["features"]), set(allocator.FEATURE_SCHEMA))

    def test_ids_join_with_reward_and_decisions_logs(self) -> None:
        # The allocation id is the join key; it must be the same style used by the
        # reward/decisions logs (an opaque short string), present and stable.
        with tempfile.TemporaryDirectory() as tmp:
            root = _fresh_root(tmp)
            moves = allocator.propose(root, log=True)
            row = json.loads(
                (root / allocator.ALLOCATIONS_PATH)
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            self.assertEqual(row["id"], moves[0]["id"])
            self.assertTrue(isinstance(row["id"], str) and row["id"])

    def test_propose_without_log_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _fresh_root(tmp)
            allocator.propose(root, log=False)
            self.assertFalse((root / allocator.ALLOCATIONS_PATH).exists())

    def test_allocator_writes_only_its_own_log(self) -> None:
        # Observe-only contract: no other file is created or changed by propose.
        with tempfile.TemporaryDirectory() as tmp:
            root = _fresh_root(tmp)
            before = {
                p: p.read_bytes()
                for p in root.rglob("*")
                if p.is_file()
            }
            allocator.propose(root, log=True)
            after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            changed = {
                p
                for p in after
                if p not in before or after[p] != before.get(p)
            }
            allocations = root / allocator.ALLOCATIONS_PATH
            self.assertEqual(changed, {allocations})


class EnergyMatchTests(unittest.TestCase):
    def test_low_recent_energy_favours_light_moves(self) -> None:
        # When the recent rating is low, the energy_match feature should lift
        # light moves (review/ship a small thread) over heavy ones.
        with tempfile.TemporaryDirectory() as tmp:
            root = _fresh_root(tmp)
            reward_log.append_reward(
                root,
                session_id="s1",
                claude_session_id=None,
                agent="claude",
                scope="root",
                energy=1,
                juice="low",
                source="test",
            )
            moves = allocator.propose(root)
            # At least one move carries an energy_match component reflecting state.
            self.assertTrue(any("energy_match" in m["breakdown"] for m in moves))


class CliTests(unittest.TestCase):
    def _run(self, root: Path, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            code = allocator.main(["--root", str(root), *argv])
        finally:
            sys.stdout = old
        return code, buf.getvalue()

    def test_plain_output_lists_moves_with_why(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _fresh_root(tmp)
            code, out = self._run(root, [])
            self.assertEqual(code, 0)
            # Numbered moves, each with a why.
            self.assertIn("1.", out)

    def test_why_flag_shows_score_breakdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _fresh_root(tmp)
            code, out = self._run(root, ["--why"])
            self.assertEqual(code, 0)
            # The breakdown names the feature schema.
            self.assertIn("staleness", out)
            self.assertIn("score", out.lower())


class BriefIntegrationTests(unittest.TestCase):
    def test_brief_next_best_moves_use_allocator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _fresh_root(tmp)
            brief = build_brief.render_brief(root)
            self.assertIn("Next best moves", brief)
            # The replaced-by-Allocator TODO marker is gone.
            self.assertNotIn("replaced by the Allocator", brief)
            self.assertNotIn("Lightweight heuristic", brief)


if __name__ == "__main__":
    unittest.main()
